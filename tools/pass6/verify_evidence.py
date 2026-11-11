"""Offline standard-library evidence binding and gate verification."""

import hashlib
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "docs/evidence/pass6"


def main():
    manifest = json.loads((DEST / "manifest.json").read_text())
    for name, expected in manifest["sources"].items():
        assert hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == expected, (
            name
        )
    for name, expected in manifest["artifacts"].items():
        assert hashlib.sha256((DEST / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == expected, (
            name
        )
        assert not re.search(
            r"[A-Za-z0-9_=-]{20,}\.[a-f0-9]{64}", (DEST / name).read_text(encoding="utf-8")
        ), name
    completion = json.loads((DEST / "completion.json").read_text())
    commands = json.loads((DEST / "commands.json").read_text())
    required = {
        "focused-http",
        "focused-http-fix",
        "focused-matrix",
        "focused-postgres",
        "focused-postgres-fix",
        "regression",
        "regression-fix",
        "regression-fix-final",
        "acceptance",
        "migrations-fresh",
        "migrations-installed",
        "ruff",
        "mypy",
        "imports",
        "compileall",
        "compose",
        "diff",
        "browser",
        "browser-fix",
    }
    assert required <= {x["name"] for x in commands}
    assert all(
        x["exit_code"]
        == (
            1
            if x["name"] in {"focused-http", "focused-postgres", "browser", "regression", "regression-fix"}
            else 0
        )
        for x in commands
    )
    for name, expected in completion["results"].items():
        suites = list(ET.parse(DEST / (name + ".xml")).getroot().iter("testsuite"))
        actual = {
            key: sum(int(s.get(key, 0)) for s in suites) for key in ("tests", "failures", "errors", "skipped")
        }
        assert actual == expected
    assert completion["results"]["acceptance"]["tests"] > 481
    assert completion["full_regressions"] == 2 and completion["second_full_run_needed"]
    assert (
        completion["results"]["acceptance"]["failures"]
        == completion["results"]["acceptance"]["errors"]
        == completion["results"]["acceptance"]["skipped"]
        == 0
    )
    assert completion["new_genuine_provider_mutations"] == 0
    assert all(x["authority_semantics_unchanged"] for x in json.loads((DEST / "migrations.json").read_text()))
    assert all(x["passed"] for x in json.loads((DEST / "browser.json").read_text())["checks"])
    print(
        json.dumps(
            {
                "verified": True,
                "sources": len(manifest["sources"]),
                "artifacts": len(manifest["artifacts"]),
                "acceptance": completion["results"]["acceptance"],
            }
        )
    )


if __name__ == "__main__":
    main()
