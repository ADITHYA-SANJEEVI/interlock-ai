"""Local role launcher; never loads PSP keys into API/worker/web/seller."""

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
role = sys.argv[1]
profile = dotenv_values(ROOT / ".env.pass0")
if role in ("api", "broker", "worker"):
    os.environ["CONTROL_DATABASE_URL"] = (
        f"postgresql+psycopg://interlock_{role}:local_only@127.0.0.1:55432/control"
    )
names = {
    "api": {
        "INTERNAL_TOKEN",
        "SESSION_KEY",
        "RAZORPAY_WEBHOOK_SECRET",
        "RAZORPAY_ACCOUNT_ID",
        "ENABLE_REFUND_PROBES",
    },
    "broker": {"INTERNAL_TOKEN", "SELLER_SIGNING_KEY", "RAZORPAY_ACCOUNT_ID"},
    "worker": {"INTERNAL_TOKEN"},
    "seller": {"SELLER_SIGNING_KEY"},
    "reference": {"INTERNAL_TOKEN"},
}[role]
for key in names:
    os.environ[key] = profile.get(key) or ""
if role == "broker":
    for key, value in dotenv_values(ROOT / ".env.razorpay").items():
        if key in {"RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"}:
            os.environ[key] = value or ""
if role == "worker":
    import time

    from interlock.worker import once

    while True:
        once()
        time.sleep(3)
else:
    import uvicorn

    uvicorn.run(
        "interlock." + role + ":app",
        host="127.0.0.1",
        port={"api": 8000, "broker": 8001, "seller": 8002, "reference": 8003}[role],
    )
