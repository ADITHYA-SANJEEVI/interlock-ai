"""Separate loopback-only production evaluation; never touches existing previews."""
import argparse
import json
import os
import shutil
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "tmp/hackathon-private"
PORT = 3026 if os.environ.get("INTERLOCK_021_PREVIEW") == "1" else 3024
POINTER = PRIVATE / ("m2021-preview.json" if PORT == 3026 else "m2020-preview.json")


def environment():
    profile = PRIVATE / "ag-studio-trial.env"
    if subprocess.run(["git", "check-ignore", "--quiet", str(profile)], cwd=ROOT).returncode:
        raise RuntimeError("IGNORED_PROFILE_REQUIRED")
    licence = dotenv_values(profile).get("AG_STUDIO_LICENSE_KEY")
    if not licence:
        raise RuntimeError("LOCAL_EVALUATION_LICENCE_REQUIRED")
    allowed = {"SYSTEMROOT", "SYSTEMDRIVE", "PROGRAMDATA", "ALLUSERSPROFILE", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env.update(NODE_ENV="production", WEB_ORIGIN=f"http://127.0.0.1:{PORT}",
               CONTROL_API_URL="http://127.0.0.1:18100", PAYPAL_API_URL="http://127.0.0.1:18101",
               INVESTIGATION_API_URL="http://127.0.0.1:18104", COMMERCE_API_URL="http://127.0.0.1:18105",
               PAYPAL_WEB_PAYMENT_ACTIONS_ENABLED="false", AG_STUDIO_LICENSE_KEY=licence,
               NEXT_TELEMETRY_DISABLED="1")
    return env


def main(command):
    if command == "prepare":
        directory = ROOT / "tmp" / ("hackathon-m2020-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
        shutil.copytree(ROOT / "web", directory, ignore=shutil.ignore_patterns("node_modules", ".next", "test-results", "playwright-report", "*.tsbuildinfo", ".env*"))
        os.symlink(ROOT / "web/node_modules", directory / "node_modules", target_is_directory=True)
        config = directory / "next.config.ts"
        config.write_text(config.read_text().replace("path.resolve(process.cwd())", 'path.resolve(process.cwd(), "../..")'))
        POINTER.write_text(json.dumps({"directory": str(directory), "port": PORT}))
        print("ISOLATED_020_SOURCE_PREPARED")
        return
    state = json.loads(POINTER.read_text())
    directory = Path(state["directory"]).resolve()
    if directory.parent != (ROOT / "tmp").resolve() or not directory.name.startswith("hackathon-m2020-") or state["port"] != PORT:
        raise RuntimeError("ISOLATED_DIRECTORY_REQUIRED")
    node = shutil.which("node")
    if command == "build":
        subprocess.run([node, "node_modules/next/dist/bin/next", "build"], cwd=directory, env=environment(), check=True)
        return
    if not (directory / ".next/BUILD_ID").is_file():
        raise RuntimeError("PRODUCTION_BUILD_REQUIRED")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", PORT))
    log = PRIVATE / "m2020-preview.log"
    with log.open("ab") as output:
        process = subprocess.Popen([node, "node_modules/next/dist/bin/next", "start", "--hostname", "127.0.0.1", "--port", str(PORT)], cwd=directory, env=environment(), stdout=output, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    state.update(pid=process.pid, url=f"http://127.0.0.1:{PORT}", financial_actions_enabled=False, local_evaluation_only=True)
    POINTER.write_text(json.dumps(state))
    print(json.dumps({k:v for k,v in state.items() if k != "directory"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "build", "start"))
    try:
        main(parser.parse_args().command)
    except Exception:
        print("ISOLATED_020_PREVIEW_FAILED_SANITIZED")
        raise SystemExit(1) from None
