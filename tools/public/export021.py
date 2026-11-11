"""Prepare an independent public source tree; preserve original Git/history/data.

No publishing, credentials, databases or provider calls. Excludes original
operational dumps and unapproved evaluation media by explicit documentation
allowlists. All source files otherwise retain original bytes.
"""
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = {
    "README.md", "architecture.md", "agents.md", "reliability.md", "performance.md",
    "reproduction.md", "provenance.md", "sponsors.md", "evaluation.md", "HACKATHON_RELEASE_RIGHTS.md",
}
INPUTS = {"completed-case.json", "incomplete-case.json", "final-api-planning-trace.json", "incomplete-investigation-trace.json", "manifest.json"}
PRIOR = {"read-benchmark.json", "static-benchmark.json", "financial-preservation.json", "validation-summary.json", "clean-clone-result.json", "npm-production-audit.json", "source-integrity.json"}


def allowed(name):
    p = Path(name)
    if p.parts[0] != "docs":
        return True
    if len(p.parts) == 2:
        return p.name in DOCS
    if name.startswith("docs/screenshots/"):
        return p.suffix in {".png", ".md", ".json"}
    if name.startswith("docs/hackathon/evidence/20261111-m2018-product/"):
        return p.name in INPUTS
    if name.startswith("docs/hackathon/evidence/20261111-engineering020/"):
        return p.name in PRIOR
    if name.startswith("docs/evidence/pass7/"):
        return p.name in {"manifest.json", "frozen-backend.json", "check-acceptance-final.txt"}
    return name == "docs/hackathon/evidence/20261109T222133790788Z-m2-postgres18-acceptance.json"


def main():
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT):
        raise RuntimeError("COMMITTED_CLEAN_SOURCE_REQUIRED")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    target = Path(os.environ["LOCALAPPDATA"]) / "InterlockAI" / ("interlock-public-021-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S"))
    target.mkdir(parents=True, exist_ok=False)
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().strip("\0").split("\0")
    kept, withheld = [], []
    for name in paths:
        source = ROOT / name
        if source.is_symlink() or not source.resolve().is_relative_to(ROOT):
            raise RuntimeError("UNREVIEWED_LINK")
        if not allowed(name):
            withheld.append(name)
            continue
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = subprocess.check_output(["git", "show", head + ":" + name], cwd=ROOT)
        original_hash = hashlib.sha256(data).hexdigest()
        if name == ".gitattributes":
            # Snapshot hashes bind committed bytes on Windows and Linux alike.
            data = b"# Public source snapshot: preserve exported bytes across platforms.\n* -text\n" + data
        dest.write_bytes(data)
        kept.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "original_blob_sha256": original_hash})
    publication = target / "docs/publication"
    publication.mkdir()
    (publication / "source-manifest.json").write_text(json.dumps({
        "classification": "REVIEWED_PUBLIC_SOURCE_SNAPSHOT", "original_source_commit": head,
        "original_history_distributed": False, "original_history_preserved": True,
        "frozen_foundation_commit": "bf6f98dfd614061f1c5937dfc206fd5f2ee84cf8", "files": kept,
    }, indent=2) + "\n", encoding="utf-8", newline="\n")
    (publication / "omissions.json").write_text(json.dumps({
        "classification": "DISCLOSED_SOURCE_EXPORT_OMISSIONS", "original_source_commit": head,
        "reason": "Withhold internal operational dumps, private publication worklogs and unapproved local commercial-evaluation media. No original source history or evidence was rewritten.",
        "paths": withheld,
    }, indent=2) + "\n", encoding="utf-8", newline="\n")
    pointer = ROOT / "tmp/hackathon-021/public-source.json"
    pointer.write_text(json.dumps({"directory": str(target), "original_source_commit": head, "files": len(kept), "withheld": len(withheld)}))
    print(json.dumps({"export_ready": True, "files": len(kept), "withheld": len(withheld), "original_history_untouched": True}))


if __name__ == "__main__":
    main()
