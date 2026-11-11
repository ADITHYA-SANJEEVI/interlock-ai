import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import Header, HTTPException

from interlock.config import settings
from interlock.contracts import canonical, signature, verify


def password_hash(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()


def issue_session(username: str) -> str:
    payload = base64.urlsafe_b64encode(canonical({"user": username, "exp": int(time.time()) + 3600})).decode()
    return payload + "." + signature(settings().session_key, payload.encode())


def session_user(authorization: str = Header(default="")) -> str:
    try:
        if not authorization.startswith("Bearer ") or len(authorization) > 2048:
            raise ValueError("invalid")
        token = authorization[7:]
        payload, sig = token.split(".")
        verify(settings().session_key, payload.encode(), sig)
        data = json.loads(base64.urlsafe_b64decode(payload))
        if (set(data) != {"user", "exp"} or type(data["exp"]) is not int
                or not isinstance(data["user"], str) or not 1 <= len(data["user"]) <= 80
                or data["exp"] <= time.time()):
            raise ValueError("expired")
        return data["user"]
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError):
        raise HTTPException(401, "Authenticate with your local account") from None


def internal(authorization: str = Header(default="")) -> None:
    expected = settings().internal_token
    if not expected or not authorization.isascii() or not hmac.compare_digest(authorization, "Bearer " + expected):
        raise HTTPException(401, "Service identity required")


def internal_headers() -> dict[str, str]:
    if not settings().internal_token:
        raise RuntimeError("Service identity missing")
    return {"Authorization": "Bearer " + settings().internal_token}


def local_password(tenant: str) -> str:
    value = os.getenv("OWNER_A_PASSWORD" if tenant == "tenant-a" else "OWNER_B_PASSWORD", "")
    if not value:
        raise RuntimeError("Local owner credentials missing")
    return value


def reference_controls() -> None:
    if settings().runtime_mode != "LOCAL" or os.getenv("ENABLE_REFERENCE_CONTROLS") != "true":
        raise HTTPException(404, "RESOURCE_UNAVAILABLE")
