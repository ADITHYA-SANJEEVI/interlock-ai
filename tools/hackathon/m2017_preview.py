"""Prepare/start a fresh loopback preview; never touch the operator's browser/build."""

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
POINTER = PRIVATE / "m2017-preview.json"


def prepare():
    destination = ROOT / "tmp" / ("hackathon-m2017-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    shutil.copytree(ROOT / "web", destination,
                    ignore=shutil.ignore_patterns("node_modules", ".next", "test-results", "playwright-report", "*.tsbuildinfo", ".env*"))
    os.symlink(ROOT / "web/node_modules", destination / "node_modules", target_is_directory=True)
    # The isolated copy shares installed dependencies outside its own directory.
    config = destination / "next.config.ts"
    config.write_text(config.read_text(encoding="utf-8").replace('path.resolve(process.cwd())', 'path.resolve(process.cwd(), "../..")'), encoding="utf-8")
    profile = {"directory": str(destination), "port": 3018, "url": "http://127.0.0.1:3018"}
    POINTER.write_text(json.dumps(profile))
    print(json.dumps({"preview_directory": destination.relative_to(ROOT).as_posix(), "build_required": True}))


def start(dev=False):
    profile = json.loads(POINTER.read_text())
    directory = Path(profile["directory"]).resolve()
    if directory.parent != (ROOT / "tmp").resolve() or not directory.name.startswith("hackathon-m2017-"):
        raise RuntimeError("ISOLATED_PREVIEW_PATH_REQUIRED")
    if not dev and not (directory / ".next/BUILD_ID").is_file():
        raise RuntimeError("ISOLATED_PRODUCTION_BUILD_REQUIRED")
    licence = dotenv_values(PRIVATE / "ag-studio-trial.env").get("AG_STUDIO_LICENSE_KEY")
    if not licence or subprocess.run(["git", "check-ignore", "--quiet", str(PRIVATE / "ag-studio-trial.env")], cwd=ROOT).returncode:
        raise RuntimeError("IGNORED_LOCAL_TRIAL_LICENCE_REQUIRED")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 3018))
    allowed = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env.update({"NODE_ENV": "development" if dev else "production", "WEB_ORIGIN": profile["url"], "CONTROL_API_URL": "http://127.0.0.1:18100",
                "PAYPAL_API_URL": "http://127.0.0.1:18101", "INVESTIGATION_API_URL": "http://127.0.0.1:18104",
                "PAYPAL_WEB_PAYMENT_ACTIONS_ENABLED": "false", "AG_STUDIO_LICENSE_KEY": licence})
    log = PRIVATE / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-studio-preview.log")
    with log.open("xb") as output:
        process = subprocess.Popen([shutil.which("node"), "node_modules/next/dist/bin/next", "dev" if dev else "start",
                     "--hostname", "127.0.0.1", "--port", "3018"], cwd=directory, env=env,
                     stdout=output, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    profile.update(pid=process.pid, log=str(log), licence_registered_via="AgStudioProvider.licenseKey",
                   scope="LOCAL_EVALUATION_ONLY", public_deployment_authorized=False)
    POINTER.write_text(json.dumps(profile))
    print(json.dumps({k: v for k, v in profile.items() if k not in {"directory", "log"}}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "start", "dev"))
    try:
        command = parser.parse_args().command
        prepare() if command == "prepare" else start(command == "dev")
    except Exception:
        print("PREVIEW_SETUP_FAILED_SANITIZED")
        raise SystemExit(1) from None
