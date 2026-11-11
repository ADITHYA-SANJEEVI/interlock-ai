"""Finite UI QA only. Stateless test sessions, no financial commands or desktop control."""

import argparse
import json
import os
import subprocess

from dotenv import dotenv_values

from tools.hackathon.disposable_postgres import PRIVATE, ROOT
from tools.hackathon.m2_preflight import DemoClient


def review(mode):
    client = DemoClient({})
    try:
        tokens = {role: client.token(role) for role in ("issuer", "requester")}
        # Directive 017 authorizes isolated signed-in role rendering tests.
        # This session is for read-only screenshots, NEVER an independent approval.
        profile = dotenv_values(PRIVATE / "m2-approver.env")
        response = client.http.post("http://127.0.0.1:18100/session", json={
            "username": profile["DEMO_APPROVER_USERNAME"], "password": profile["DEMO_APPROVER_PASSWORD"]})
        if response.status_code != 200:
            raise RuntimeError("READONLY_APPROVER_VISUAL_SESSION_UNAVAILABLE")
        tokens["approver"] = response.json()["token"]
        env = os.environ.copy()
        env["INTERLOCK_VISUAL_TEST_SESSIONS"] = json.dumps(tokens)
        result = subprocess.run(["node", "tools/hackathon/m2017_review.cjs", mode], cwd=ROOT, env=env)
        raise SystemExit(result.returncode)
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("before", "design", "final", "record"))
    try:
        review(parser.parse_args().mode)
    except Exception:
        print("VISUAL_REVIEW_FAILED_SANITIZED")
        raise SystemExit(1) from None
