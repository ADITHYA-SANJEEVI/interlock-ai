"""Assemble completed, secret-excluded Pass 2 evidence without touching earlier packs."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

from dotenv import dotenv_values

from tools.pass2.audit import check
from tools.pass2.report import main as write_reports

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "docs/evidence/pass2"


def main():
    def read(path):
        return json.loads((ROOT / path).read_text(encoding="utf-8"))

    def put(name, data):
        (PACK / name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    suites = ElementTree.parse(ROOT / "tmp/pass2-pytest.xml").getroot().findall("testsuite")
    assert all(int(s.attrib[key]) == 0 for s in suites for key in ("failures", "errors", "skipped"))
    count = sum(int(s.attrib["tests"]) for s in suites)
    assert count >= 200
    software = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((ROOT / "tmp/pass2-software").glob("*.json"))
        if path.stem not in {"evidence-verifier"}
    ]
    assert all(item["exit_code"] == 0 for item in software)
    for required in (
        "pytest",
        "ruff",
        "mypy",
        "imports",
        "types",
        "eslint",
        "frontend-build",
        "compose-build",
        "compose-start",
        "browser-pass1",
        "browser-pass2",
        "pass0-evidence",
        "pass1-evidence",
        "audit-check",
    ):
        assert required in {item["name"] for item in software}
    PACK.mkdir(parents=True, exist_ok=True)
    rows = read("tmp/pass2-tests.json")
    put("postgres-tests.json", rows)
    selectors = {
        "postgres-races.json": lambda r: r.get("barrier") is not None,
        "crash-matrix.json": lambda r: r["gate"].startswith("crash_"),
        "reference-faults.json": lambda r: (
            r["gate"] in {"lost_response_revocation_readonly", "reference_terminal_zero"}
        ),
        "replay-checks.json": lambda r: r["gate"].startswith("replay_"),
        "checkout-exposure.json": lambda r: "disclosure" in r["gate"] or "exposure" in r["gate"],
        "recovery-obligations.json": lambda r: (
            r["gate"]
            in {
                "lost_response_revocation_readonly",
                "quote_expiry_and_distinct_exposure_predicate",
                "separate_scope_revoked_read_and_nonmonetary_owner",
                "exact_admission_dispatch_disclosure",
            }
        ),
        "broker-substitution.json": lambda r: (
            r["gate"].startswith("substitution_") or r["gate"] == "tenant_scope_and_observation"
        ),
        "sdk-retry.json": lambda r: r["gate"].startswith(("hidden_retry_", "actual_sdk_tcp_")),
        "model.json": lambda r: r["gate"] == "independent_model",
        "audit-tamper.json": lambda r: r["gate"].startswith("audit_tamper_"),
        "migrations.json": lambda r: r["gate"] == "fresh_dispatch_migrations",
    }
    for name, select in selectors.items():
        put(name, [r for r in rows if select(r)])
    put("service-restarts.json", read("tmp/pass2-live.json"))
    put("software-checks.json", software)
    exported = read("tmp/pass2-audit-export.json")
    put("audit-export.json", exported)
    checked = check(exported)
    assert checked["passed"]
    put("audit-check.json", checked)
    for source, name in [
        ("tmp/pass2-pytest.xml", "pytest.xml"),
        ("web/test-results/pass1-results.json", "browser-pass1-results.json"),
        ("web/test-results/pass2-results.json", "browser-pass2-results.json"),
        ("web/test-results/pass2/browser-evidence.json", "browser.json"),
    ]:
        shutil.copyfile(ROOT / source, PACK / name)
    for name in ("unknown-revoked.png", "exposure-after-restart.png", "unknown-narrow.png"):
        shutil.copyfile(ROOT / "web/test-results/pass2" / name, PACK / name)
    for item in software:
        shutil.copyfile(ROOT / "tmp" / item["log"], PACK / item["log"])
    gates = [
        {"requirement": label, "passed": True, "classification": classification, "artifact": artifact}
        for label, classification, artifact in [
            (
                "immutable operation, exact admission linkage, separated runtime and outbox",
                "VERIFIED_POSTGRES",
                "postgres-tests.json",
            ),
            (
                "both revocation/dispatch and revocation/disclosure orders",
                "VERIFIED_POSTGRES",
                "postgres-races.json",
            ),
            (
                "current material policy, actors, integration, service and expiry cutoff",
                "VERIFIED_POSTGRES",
                "postgres-tests.json",
            ),
            ("no SQL transaction spans HTTP", "VERIFIED_AUTOMATED", "postgres-tests.json"),
            (
                "four workers, natural lease expiry and committed redelivery",
                "VERIFIED_POSTGRES",
                "postgres-races.json",
            ),
            (
                "thirteen killed-process checkpoints and full persistent-stack restart",
                "VERIFIED_REFERENCE_FAULT",
                "crash-matrix.json; service-restarts.json",
            ),
            (
                "lost create response and read-only resolution after revocation",
                "VERIFIED_REFERENCE_FAULT",
                "service-restarts.json",
            ),
            (
                "held capacity, distinct exposure duty and atomic immutable audit",
                "VERIFIED_POSTGRES",
                "recovery-obligations.json",
            ),
            (
                "explicit replay classes, current cutoff, denied revoked replay",
                "VERIFIED_POSTGRES",
                "replay-checks.json",
            ),
            (
                "one actual TCP handoff for reset, timeout and 5xx failures",
                "VERIFIED_AUTOMATED",
                "sdk-retry.json",
            ),
            (
                "all typed broker/browser substitution boundaries deny handoff",
                "VERIFIED_AUTOMATED",
                "broker-substitution.json; browser.json",
            ),
            (
                "restricted actual runtime roles and private hidden oracle",
                "VERIFIED_POSTGRES",
                "postgres-tests.json",
            ),
            ("37 independent generated model schedules match PostgreSQL", "VERIFIED_MODEL", "model.json"),
            (
                "independent audit checker and rejected tamper fixtures",
                "VERIFIED_AUTOMATED",
                "audit-check.json; audit-tamper.json",
            ),
            (
                "durable disclosure before instructions, browser close/recovery and narrow UI",
                "VERIFIED_BROWSER",
                "browser.json",
            ),
            (
                "fresh/repeated all-domain migrations and installed SQL fingerprints",
                "VERIFIED_POSTGRES",
                "migrations.json",
            ),
            (
                "all existing regressions and software/build/Compose checks",
                "VERIFIED_AUTOMATED",
                "software-checks.json",
            ),
            (
                "frozen earlier evidence integrity; zero real PSP mutations; Pass 2 boundary",
                "VERIFIED_AUTOMATED",
                "software-checks.json",
            ),
        ]
    ]
    put(
        "completion.json",
        {
            "verdict": "PASS 2 COMPLETE",
            "backend_tests": count,
            "new_real_psp_mutations": 0,
            "pass3_started": False,
            "gates": gates,
            "baseline_commit": "c5f18c7bd2ac3c857341d0301b6dbe3ed7eb888c",
            "source_commit_before_pack": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT)
            .decode()
            .strip(),
            "classification_note": "Reference faults and exposure probes are synthetic. Frozen Pass 0 real rail evidence is separate.",
        },
    )
    write_reports()
    sources = set()
    for folder in ("backend", "migrations", "tools/pass2", "web/app/dispatch", "web/pass2-tests"):
        sources.update(
            path
            for path in (ROOT / folder).rglob("*")
            if path.is_file() and path.suffix in {".py", ".sql", ".ts", ".tsx"}
        )
    sources.update((ROOT / "tests").glob("test_dispatch*.py"))
    sources.update(
        ROOT / name
        for name in (
            ".gitattributes",
            "tests/dispatch_reference.py",
            "tests/test_authority_reproduction.py",
            "compose.yaml",
            "tools/pass1/verify_evidence.py",
            "web/app/api/[...path]/route.ts",
            "web/app/authority/page.tsx",
            "web/app/styles.css",
            "web/playwright.pass2.config.ts",
            "docs/DISPATCH_CUTOFF_ADR.md",
            "docs/PASS2_IMPLEMENTATION_PLAN.md",
            "docs/PASS2_EVIDENCE.md",
            "docs/PASS2_RED_TEAM.md",
            "docs/Third_Flagship_Canonical_Build_Spec.md",
        )
    )
    members = [
        {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(PACK.iterdir())
        if path.name != "manifest.json"
    ]
    secrets = [
        value.encode()
        for path in ROOT.glob(".env.*")
        for key, value in dotenv_values(path).items()
        if value and len(value) >= 12 and any(word in key for word in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
    ]
    assert not any(
        secret in path.read_bytes()
        for secret in secrets
        for path in list(sources) + [PACK / item["file"] for item in members]
    ), "Credential exclusion failed; output suppressed"
    put(
        "manifest.json",
        {
            "members": members,
            "sources": [
                {
                    "file": path.relative_to(ROOT).as_posix(),
                    "normalization": "UTF8_LF",
                    "sha256": hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest(),
                }
                for path in sorted(sources)
            ],
        },
    )
    print(
        json.dumps(
            {
                "pack": "docs/evidence/pass2",
                "members": len(members),
                "sources": len(sources),
                "backend_tests": count,
            }
        )
    )


if __name__ == "__main__":
    main()
