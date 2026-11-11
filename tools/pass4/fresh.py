"""Fresh/repeated three-domain migrations and exact private-schema fingerprints."""

import hashlib
import json
import os
import subprocess
import sys
from uuid import uuid4

from sqlalchemy import create_engine, text

from tools.pass3.export import write

BASE = "postgresql+psycopg://interlock:local_only@127.0.0.1:55432/"


def fingerprint(db, schema):
    queries = {
        "columns": "SELECT table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema=:schema ORDER BY table_name,ordinal_position",
        "constraints": "SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema ORDER BY c.relname,k.conname",
        "functions": "SELECT p.proname,pg_get_function_identity_arguments(p.oid),p.prosrc,p.proconfig,p.prosecdef,r.rolname,p.proacl::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner WHERE n.nspname=:schema ORDER BY p.proname,p.oid::regprocedure::text",
        "owners": "SELECT c.relname,r.rolname,c.relacl::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname=:schema AND c.relkind='r' ORDER BY c.relname",
        "triggers": "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND NOT t.tgisinternal ORDER BY c.relname,t.tgname",
    }
    result = {
        name: [list(row) for row in db.execute(text(query), {"schema": schema})]
        for name, query in queries.items()
    }
    if schema == "returns":
        result["upstream_triggers"] = [
            list(row)
            for row in db.execute(
                text(
                    "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='authority' AND t.tgname LIKE 'returns_%' ORDER BY c.relname,t.tgname"
                )
            )
        ]
    return result


if __name__ == "__main__":
    admin = create_engine(BASE + "postgres", isolation_level="AUTOCOMMIT", hide_parameters=True)
    records = []
    for domain, schema, owner in (
        ("control", "returns", "interlock_returns_owner"),
        ("seller", "seller_truth", "interlock_seller_truth_owner"),
        ("reference", "ref_returns", "interlock_reference_economic_owner"),
    ):
        name = "test_pass4_fresh_" + domain + "_" + uuid4().hex[:10]
        assert name.startswith("test_pass4_fresh_") and name.replace("_", "").isalnum()
        with admin.connect() as db:
            db.execute(text('CREATE DATABASE "' + name + '"'))
        fresh = create_engine(BASE + name, hide_parameters=True)
        try:
            for _ in range(2):
                subprocess.run(
                    [sys.executable, "-m", "alembic", "upgrade", "head"],
                    env={**os.environ, "DB_DOMAIN": domain, "MIGRATION_DATABASE_URL": BASE + name},
                    check=True,
                )
            with fresh.connect() as db, create_engine(BASE + domain).connect() as installed:
                shape = fingerprint(db, schema)
                assert shape == fingerprint(installed, schema), "Installed private-schema source/ACL drift"
                assert db.scalar(text("SELECT version_num FROM alembic_version")) == "0018_pass4_bundle"
                assert all(row[1] == owner for row in shape["owners"])
                assert all(
                    row[5] == owner
                    and (not row[4] or row[3] == ["search_path=pg_catalog, " + schema + ", pg_temp"])
                    for row in shape["functions"]
                )
                properties = db.execute(
                    text(
                        "SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit FROM pg_roles WHERE rolname=:owner"
                    ),
                    {"owner": owner},
                ).one()
                assert not any(properties)
                records.append(
                    {
                        "domain": domain,
                        "schema": schema,
                        "owner": owner,
                        "runs": 2,
                        "fingerprint": hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest(),
                        "source_acl_equivalent": True,
                        "nonlogin_owner": True,
                        "classification": "VERIFIED_POSTGRES",
                    }
                )
        finally:
            fresh.dispose()
            with admin.connect() as db:
                db.execute(text('DROP DATABASE "' + name + '"'))
    admin.dispose()
    write("tmp/pass4-migrations.json", records)
    print(json.dumps({"domains": 3, "fresh_and_repeated": True, "classification": "VERIFIED_POSTGRES"}))
