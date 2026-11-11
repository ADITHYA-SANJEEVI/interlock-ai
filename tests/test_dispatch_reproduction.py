"""Fresh/repeated head migrations reproduce all Pass 2 private SQL sources and owners."""

import hashlib
import json
import os
import subprocess
import sys
from uuid import uuid4

from sqlalchemy import create_engine, text
from test_dispatch import auth as auth
from test_dispatch import d as d
from test_dispatch import record


def fingerprint(db, schema):
    queries = {
        "columns": "SELECT table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema=:schema ORDER BY table_name,ordinal_position",
        "constraints": "SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema ORDER BY c.relname,k.conname",
        "functions": "SELECT p.proname,pg_get_function_identity_arguments(p.oid),p.prosrc,p.proconfig,p.prosecdef,r.rolname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner WHERE n.nspname=:schema ORDER BY p.proname,p.oid::regprocedure::text",
        "triggers": "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND NOT t.tgisinternal ORDER BY c.relname,t.tgname",
        "owners": "SELECT c.relname,r.rolname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname=:schema AND c.relkind='r' ORDER BY c.relname",
        "indexes": "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname=:schema ORDER BY tablename,indexname",
    }
    return {
        name: [list(row) for row in db.execute(text(sql), {"schema": schema})]
        for name, sql in queries.items()
    }


def test_fresh_repeated_three_domains_exact_installed_dispatch_source(d):
    admin = create_engine(d.auth.owner_url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    outcomes = []
    for domain, schema in (("control", "dispatch"), ("reference", "ref_dispatch"), ("seller", None)):
        name = "test_dispatch_reproduce_" + uuid4().hex[:12] + "_" + domain
        assert name.startswith("test_dispatch_reproduce_") and name.replace("_", "").isalnum()
        with admin.connect() as db:
            db.execute(text('CREATE DATABASE "' + name + '"'))
        url = d.auth.owner_url.rsplit("/", 1)[0] + "/" + name
        fresh = create_engine(url, hide_parameters=True)
        try:
            for _ in range(2):
                completed = subprocess.run(
                    [sys.executable, "-m", "alembic", "upgrade", "head"],
                    env={**os.environ, "DB_DOMAIN": domain, "MIGRATION_DATABASE_URL": url},
                    capture_output=True,
                    text=True,
                )
                assert completed.returncode == 0, (
                    "Fresh domain migration failed; diagnostic output suppressed"
                )
            with fresh.connect() as db:
                assert (
                    db.scalar(text("SELECT version_num FROM alembic_version"))
                    == "0020_pass6_boundaries"
                )
                if schema:
                    shape = fingerprint(db, schema)
                    installed = d.auth.owner if domain == "control" else d.ref_owner
                    with installed.connect() as existing:
                        assert shape == fingerprint(existing, schema)
                    expected_owner = (
                        "interlock_dispatch_owner"
                        if domain == "control"
                        else "interlock_reference_dispatch_owner"
                    )
                    assert all(owner == expected_owner for _, owner in shape["owners"])
                    for _, _, _, config, definer, owner in shape["functions"]:
                        assert owner == expected_owner
                        if definer:
                            assert config == ["search_path=pg_catalog, " + schema + ", pg_temp"]
                    outcomes.append(
                        {
                            "domain": domain,
                            "runs": 2,
                            "source_equivalent": True,
                            "schema": schema,
                            "fingerprint": hashlib.sha256(
                                json.dumps(shape, sort_keys=True).encode()
                            ).hexdigest(),
                            "owner": expected_owner,
                            "functions": len(shape["functions"]),
                            "tables": len(shape["owners"]),
                        }
                    )
                else:
                    assert not db.scalar(
                        text(
                            "SELECT 1 FROM pg_namespace WHERE nspname IN ('dispatch','authority','ref_dispatch')"
                        )
                    )
                    outcomes.append({"domain": domain, "runs": 2, "private_control_schemas_absent": True})
        finally:
            fresh.dispose()
            with admin.connect() as db:
                db.execute(text('DROP DATABASE "' + name + '"'))
    admin.dispose()
    record("fresh_dispatch_migrations", outcomes=outcomes)
