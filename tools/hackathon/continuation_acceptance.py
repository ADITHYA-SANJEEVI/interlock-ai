"""New disposable continuation acceptance only; never installs into demo databases."""

import argparse
import hashlib
import json
import os
import time
import unittest
from datetime import UTC, datetime

from tools.hackathon.disposable_postgres import (
    DATABASES,
    DATASET,
    PRIVATE,
    ROOT,
    connect,
    environment,
    initialize_dataset,
    migrate,
    verify,
)
from tools.hackathon.m2_acceptance import SUITES, source_hashes
from tools.hackathon.m2_demo import write_private

BASELINE = PRIVATE / "m2013-continuation-demo-preservation.json"
NEW_SUITES = ("paypal_acceptance.test_continuation_postgres",)


def demo_fingerprint():
    result = {}
    for domain, tables in {
        "control": ("dispatch.business_operations", "paypal_v1.bindings", "paypal_v1.runtime",
                    "authority.approvals", "authority.approval_heads", "authority.grants", "authority.grant_heads",
                    "authority.grant_versions", "authority.seller_quotes", "authority.quote_heads",
                    "authority.reservations", "authority.reservation_heads", "paypal_v1.phase_commits",
                    "paypal_v1.capture_requests", "dispatch.checkout_exposures", "economic.facts",
                    "economic.postings", "paypal_v1.deployment", "dispatch.integrations"),
        "seller": ("public.quotes", "seller_truth.orders", "seller_truth.entitlements", "seller_truth.finalizations"),
    }.items():
        with connect("hackathon_disposable_008_demo_" + domain) as db:
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            result[domain] = {"schema": db.execute("SELECT version_num FROM alembic_version").fetchone()[0],
                              "v2_schema_absent": db.execute("SELECT to_regnamespace('paypal_v2') IS NULL").fetchone()[0]}
            for table in tables:
                rows = [r[0] for r in db.execute("SELECT to_jsonb(x)::text FROM " + table + " x ORDER BY to_jsonb(x)::text")]
                result[domain][table] = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
    return result


def legacy_functions(demo=False):
    hashes = {}
    for domain, schemas in {"control": ["authority", "dispatch", "paypal_v1", "economic"],
                            "seller": ["seller_truth"]}.items():
        name = "hackathon_disposable_008_demo_" + domain if demo else DATABASES[domain]
        with connect(name) as db:
            db.execute("SET TRANSACTION READ ONLY")
            rows = db.execute("""SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid)
          FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname=ANY(%s) AND p.proname<>'paypal_continuation_v2' ORDER BY 1,2,3""", (schemas,)).fetchall()
            hashes[domain] = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
    return hashes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("setup", "test"))
    args = parser.parse_args()
    if DATASET != "m2_continuation" or DATABASES["control"] != "hackathon_disposable_013_continuation_control":
        raise RuntimeError("NEW_CONTINUATION_ACCEPTANCE_DATASET_REQUIRED")
    cluster = verify()
    before = demo_fingerprint()
    if BASELINE.exists():
        if json.loads(BASELINE.read_text(encoding="utf8")) != before:
            raise RuntimeError("DEMO_PRESERVATION_BASELINE_CHANGED")
    else:
        write_private(BASELINE, json.dumps(before, indent=2))
    if args.command == "setup":
        initialize_dataset()
        migrate()
        if demo_fingerprint() != before:
            raise RuntimeError("DEMO_CHANGED_DURING_ISOLATED_SETUP")
        print(json.dumps({"new_acceptance_databases": DATABASES, "demo_preserved": True,
                          "cluster_system_identifier": cluster["system_identifier"]}))
        return 0
    env = environment()
    os.environ.update(env)
    os.environ["PAYPAL_TEST_SCHEMA_HEAD"] = "0031_continuation_cutoff"
    sources = source_hashes()
    old_functions = legacy_functions()
    if old_functions != legacy_functions(demo=True):
        raise RuntimeError("ORIGINAL_INSTALLED_FUNCTION_DEFINITIONS_CHANGED")
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(s) for s in (*SUITES, *NEW_SUITES))
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if sources != source_hashes() or old_functions != legacy_functions() or demo_fingerprint() != before:
        raise RuntimeError("SOURCE_LEGACY_FUNCTION_OR_DEMO_PRESERVATION_FAILED")
    report = {"classification": "ACTUAL_ISOLATED_POSTGRES_WITH_SYNTHETIC_PAYPAL_AND_HUMAN_IDENTITIES",
              "recorded_at": datetime.now(UTC).isoformat(), "cluster": cluster,
              "dataset": DATASET, "databases": DATABASES, "schema_head": "0031_continuation_cutoff",
              "tests_run": result.testsRun, "failures": [t.id() for t, _ in result.failures],
              "errors": [t.id() for t, _ in result.errors], "skipped": [t.id() for t, _ in result.skipped],
              "duration_seconds": round(time.monotonic() - started, 3), "source_sha256": sources,
              "legacy_installed_functions_unchanged": True, "demo_fingerprints_unchanged": True,
              "legacy_function_sha256_matching_demo": old_functions,
              "demo_migrations": 0, "genuine_paypal_mutations": 0, "genuine_model_calls": 0}
    path = ROOT / "docs/hackathon/evidence" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-continuation-acceptance.json")
    with path.open("x", encoding="utf8") as output:
        output.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"evidence": path.relative_to(ROOT).as_posix(), "tests_run": result.testsRun,
                      "failures": len(result.failures), "errors": len(result.errors), "demo_preserved": True}))
    return not result.wasSuccessful()


if __name__ == "__main__":
    raise SystemExit(main())
