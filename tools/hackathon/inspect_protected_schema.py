"""Read-only installed kernel equivalence and role/conservation inspection."""

import hashlib
import json

from tools.hackathon.disposable_postgres import DATABASES, connect, verify

FUNCTIONS = """SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),
 p.prosrc,p.prosecdef,p.proconfig,r.rolname FROM pg_proc p
 JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner
 WHERE n.nspname=ANY(%s) ORDER BY 1,2,3"""


def functions(database, schemas):
    with connect(database) as db:
        return {(r[0], r[1], r[2]): list(r[3:]) for r in db.execute(FUNCTIONS, (schemas,))}


def inspect():
    cluster = verify()
    reports = []
    for domain, schemas in (("control", ["authority", "dispatch", "economic", "returns", "reconciliation"]),
                            ("seller", ["seller_truth"]), ("reference", ["ref_dispatch", "ref_economic", "ref_returns"])):
        old = functions("test_" + domain, schemas)
        new = functions(DATABASES[domain], schemas)
        differences = [".".join(k[:2]) for k, value in old.items() if new.get(k) != value]
        if not old or differences:
            raise RuntimeError("INSTALLED_LEGACY_FUNCTION_DRIFT: " + ",".join(differences))
        with connect(DATABASES[domain]) as db:
            head = db.execute("SELECT version_num FROM public.alembic_version").fetchone()[0]
        reports.append({"domain": domain, "installed_head": head, "legacy_functions_identical": len(old),
                        "comparison": "BODY_SECURITY_DEFINER_SEARCH_PATH_AND_OWNER", "differences": differences,
                        "legacy_functions_sha256": hashlib.sha256(json.dumps(list(old.items()), sort_keys=True).encode()).hexdigest()})
    with connect(DATABASES["control"]) as db:
        bad = db.execute("""SELECT rolname FROM pg_roles WHERE rolname IN
         ('interlock_api','interlock_broker','interlock_worker','interlock_economic','interlock_seller','interlock_reference')
         AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR NOT rolcanlogin)""").fetchall()
        bad_owners = db.execute("""SELECT DISTINCT r.rolname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
         JOIN pg_roles r ON r.oid=p.proowner WHERE n.nspname='paypal_v1'
         AND (r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolinherit)""").fetchall()
        conservation = db.execute("SELECT count(*) FROM authority.grant_heads WHERE bound<>consumed+reserved+free").fetchone()[0]
        private_reads = db.execute("""SELECT r.rolname,t.tbl FROM
         (VALUES('interlock_api'),('interlock_broker'),('interlock_economic')) r(rolname)
         CROSS JOIN (VALUES('paypal_v1.bindings'),('paypal_v1.phase_commits'),('economic.lines'),('authority.issuer_keys')) t(tbl)
         WHERE has_table_privilege(r.rolname,t.tbl,'SELECT')""").fetchall()
        memberships = db.execute("""SELECT runtime.rolname,owner.rolname FROM pg_roles runtime CROSS JOIN pg_roles owner
         WHERE runtime.rolname IN ('interlock_api','interlock_broker','interlock_worker','interlock_economic','interlock_seller','interlock_reference')
         AND owner.rolname LIKE 'interlock%owner' AND pg_has_role(runtime.oid,owner.oid,'MEMBER')""").fetchall()
        unbalanced = db.execute("""SELECT count(*) FROM economic.postings p WHERE
         (SELECT count(*) FROM economic.lines l WHERE l.posting_id=p.id)<>2 OR
         (SELECT sum(CASE side WHEN 'DEBIT' THEN amount ELSE -amount END) FROM economic.lines l WHERE l.posting_id=p.id)<>0""").fetchone()[0]
    if bad or bad_owners or conservation or private_reads or memberships or unbalanced:
        raise RuntimeError("INSTALLED_ROLE_OR_CONSERVATION_VIOLATION")
    return {"classification": "ACTUAL_POSTGRES_WITH_SYNTHETIC_PROVIDER_FIXTURES", "cluster": cluster,
            "domains": reports, "unsafe_runtime_roles": len(bad), "unsafe_definer_owners": len(bad_owners),
            "runtime_private_read_grants": len(private_reads), "budget_conservation_violations": conservation,
            "runtime_definer_memberships": len(memberships),
            "unbalanced_or_incomplete_journals": unbalanced, "genuine_provider_operations": 0}


if __name__ == "__main__":
    print(json.dumps(inspect(), indent=2))
