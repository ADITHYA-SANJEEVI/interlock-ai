"""Standard-library-only integrity and acceptance verifier, with no domain imports."""

import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "docs/evidence/pass5"


def main():
    manifest = json.loads((FOLDER / "manifest.json").read_text())
    for path, expected in manifest["sources"].items():
        assert hashlib.sha256((ROOT / path).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == expected, path
    for name, expected in manifest["artifacts"].items():
        assert hashlib.sha256((FOLDER / name).read_bytes()).hexdigest() == expected, name
    completion = json.loads((FOLDER / "completion.json").read_text())
    for check in json.loads((FOLDER / "commands.json").read_text()):
        assert check["exit_code"] == (1 if check["name"] == "regression" else 0), check["name"]
    assert completion["results"]["regression"]["failures"] == 1
    assert completion["results"]["regression-fix"]["failures"] == 0
    for suite in ET.parse(FOLDER / "acceptance.xml").getroot().iter("testsuite"):
        assert int(suite.get("failures", 0)) == int(suite.get("errors", 0)) == int(suite.get("skipped", 0)) == 0
        assert int(suite.get("tests", 0)) == completion["results"]["acceptance"]["tests"]
    races = json.loads((FOLDER / "races.json").read_text())
    assert len(races) == completion["concurrency_cases"] == 8
    assert all(r["pids"]["winner"] != r["pids"]["contender"] and r["serialization_retry"] for r in races)
    crashes = json.loads((FOLDER / "crashes.json").read_text())
    assert len({c["stage"] for c in crashes}) == completion["crash_checkpoints"] == 7
    assert all(c["exit"] != 0 and c["restart_converged"] for c in crashes)
    migrations = json.loads((FOLDER / "migrations.json").read_text())
    assert len(migrations) == 3 and all(m["runs"] == 2 and m["equivalent"] for m in migrations)
    assert completion["new_genuine_provider_mutations"] == 0 and not completion["frontend_affected"]
    print(json.dumps({"status": "PASS", "tests": completion["results"]["acceptance"]["tests"],
                      "concurrency": 8, "crash_checkpoints": 7}))


if __name__ == "__main__":
    main()
