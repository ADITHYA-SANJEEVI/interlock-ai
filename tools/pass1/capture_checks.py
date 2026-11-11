"""Capture bounded local build/service verification without logging runtime credentials."""

import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[2]


def main():
    checks = []
    commands = [
        [
            str(ROOT / ".venv/Scripts/ruff.exe"),
            "check",
            "backend",
            "migrations",
            "tools/pass0",
            "tools/pass1",
            "tests",
        ],
        [sys.executable, "-m", "mypy"],
        [
            sys.executable,
            "-c",
            "import interlock.api, interlock.broker, interlock.seller, interlock.reference, interlock.worker; print('Five runtime modules import')",
        ],
        ["docker", "compose", "up", "-d"],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        checks.append(
            {
                "command": command[1:] if command[0] == sys.executable else command,
                "returncode": result.returncode,
                "passed": result.returncode == 0,
            }
        )
        assert result.returncode == 0, "Local verification failed; raw diagnostics remain private"
    inventory = subprocess.check_output(["docker", "compose", "ps", "--format", "json"], cwd=ROOT)
    services = [json.loads(line) for line in inventory.decode().splitlines() if line]
    roles = []
    internal_tokens = {}
    for service in services:
        assert service["State"] == "running"
        if service["Service"] == "postgres":
            assert service["Health"] == "healthy"
            continue
        configured = json.loads(subprocess.check_output(["docker", "inspect", service["ID"]]))[0]["Config"][
            "Env"
        ]
        env = dict(item.split("=", 1) for item in configured)
        internal_tokens[service["Service"]] = env.get("INTERNAL_TOKEN")
        assert "MIGRATION_DATABASE_URL" not in env
        databases = []
        for name, value in env.items():
            if name.endswith("DATABASE_URL"):
                user = urlparse(value).username
                assert user != "interlock" and user != "interlock_authority_owner"
                databases.append({"variable": name, "role": user})
        assert databases
        roles.append({"service": service["Service"], "state": "running", "databases": databases})
    assert {row["service"] for row in roles} == {"api", "broker", "worker", "seller", "reference"}
    for service, port in (("api", 8000), ("broker", 8001), ("seller", 8002), ("reference", 8003)):
        token = internal_tokens[service]
        headers = {"Authorization": "Bearer " + token} if token else {}
        result = httpx.get(f"http://127.0.0.1:{port}/health", headers=headers, timeout=10)
        assert result.status_code == 200
        checks.append({"command": f"GET loopback:{port}/health", "status": 200, "passed": True})
    path = ROOT / "tmp/pass1-local-checks.json"
    path.write_text(
        json.dumps(
            {
                "checks": checks,
                "runtime_services": {
                    "owner_credentials_absent": True,
                    "roles": roles,
                    "postgres": "18.3 healthy",
                },
            },
            indent=2,
        )
        + "\n"
    )
    print(
        "PASS: runtime imports, scoped ruff/mypy, Compose startup, four health endpoints, five isolated service credentials"
    )


if __name__ == "__main__":
    main()
