"""Rerun protected SQL on the dedicated M2 acceptance dataset; no PSP I/O."""

import hashlib
import json
import os
import time
import unittest
from datetime import UTC, datetime

from sqlalchemy.engine import make_url

from tools.hackathon.disposable_binding import HEAD
from tools.hackathon.disposable_postgres import DATASET, PRIVATE, ROOT, environment, verify

SUITES = ("paypal_acceptance.test_protected_postgres", "paypal_acceptance.test_adversarial_postgres",
          "paypal_acceptance.test_capture_races_postgres", "paypal_acceptance.test_approval_postgres",
          "paypal_acceptance.test_preflight_expiry_postgres", "paypal_acceptance.test_unsent_cancellation_postgres")
EXPECTED_TESTS = 58


def source_hashes():
    paths = {*ROOT.glob("backend/interlock/paypal_*.py"), ROOT / "backend/interlock/proposer.py",
             *ROOT.glob("paypal_migrations/**/*.py"), *ROOT.glob("paypal_migrations/sql/*.sql"),
             *ROOT.glob("paypal_acceptance/*.py"), ROOT / "tools/hackathon/disposable_binding.py",
             ROOT / "tools/hackathon/disposable_postgres.py"}
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def main():
    if DATASET != "m2_acceptance":
        raise RuntimeError("M2_ACCEPTANCE_DATASET_REQUIRED")
    cluster = verify()
    before = source_hashes()
    env = environment()
    legacy_active = (PRIVATE / "m2-legacy-active.json").exists()
    if legacy_active:
        # Historical suite temporarily holds these existing roles. Only this
        # process's URL passwords change; no profile or PostgreSQL role write.
        for role in ("API", "BROKER", "WORKER", "ECONOMIC", "SELLER"):
            key = "PAYPAL_TEST_" + role + "_URL"
            env[key] = make_url(env[key]).set(password="local_only").render_as_string(hide_password=False)
    os.environ.update(env)
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in SUITES)
    if suite.countTestCases() != EXPECTED_TESTS:
        raise RuntimeError("COMPLETE_PROTECTED_ACCEPTANCE_SUITE_REQUIRED")
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if before != source_hashes():
        raise RuntimeError("ACCEPTANCE_SOURCE_CHANGED_DURING_RUN")
    now = datetime.now(UTC)
    report = {"classification": "ACTUAL_M2_POSTGRES18_SQL_WITH_SYNTHETIC_MODEL_AND_PAYPAL",
              "recorded_at": now.isoformat(), "cluster_system_identifier": cluster["system_identifier"],
              "version": cluster["version"], "host": cluster["host"], "port": cluster["port"],
              "dataset": DATASET, "opt_in_head": HEAD, "tests_run": result.testsRun, "suites": list(SUITES),
              "failures": [t.id() for t, _ in result.failures], "errors": [t.id() for t, _ in result.errors],
              "skipped": [t.id() for t, _ in result.skipped],
              "duration_seconds": round(time.monotonic() - started, 3),
              "genuine_paypal_operations": 0, "genuine_model_requests": 0,
              "protected_product_real_payment_demonstrated": False,
              "transient_legacy_password_in_memory": legacy_active, "source_sha256": before}
    path = ROOT / "docs/hackathon/evidence" / (now.strftime("%Y%m%dT%H%M%S%fZ") + "-m2-postgres18-acceptance.json")
    with path.open("x", encoding="utf-8") as output:
        output.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"evidence": path.relative_to(ROOT).as_posix(), "tests_run": result.testsRun,
                      "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped)}))
    return not result.wasSuccessful()


if __name__ == "__main__":
    raise SystemExit(main())
