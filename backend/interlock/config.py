import os
from dataclasses import dataclass, fields
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True, repr=False)
class Settings:
    runtime_mode: str = "LOCAL"
    cors_origins: tuple[str, ...] = ()
    control_db: str = os.getenv(
        "CONTROL_DATABASE_URL", "postgresql+psycopg://interlock_api:local_only@127.0.0.1:55432/control"
    )
    seller_db: str = os.getenv(
        "SELLER_DATABASE_URL", "postgresql+psycopg://interlock_seller:local_only@127.0.0.1:55432/seller"
    )
    reference_db: str = os.getenv(
        "REFERENCE_DATABASE_URL",
        "postgresql+psycopg://interlock_reference:local_only@127.0.0.1:55432/reference",
    )
    broker_url: str = os.getenv("BROKER_URL", "http://127.0.0.1:8001")
    seller_url: str = os.getenv("SELLER_URL", "http://127.0.0.1:8002")
    reference_url: str = os.getenv("REFERENCE_URL", "http://127.0.0.1:8003")
    internal_token: str = os.getenv("INTERNAL_TOKEN", "")
    seller_key: str = os.getenv("SELLER_SIGNING_KEY", "")
    session_key: str = os.getenv("SESSION_KEY", "")
    webhook_secret: str = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    account_id: str = os.getenv("RAZORPAY_ACCOUNT_ID", "acc_TStyPZcYqDkNbI")

    def __post_init__(self):
        if self.runtime_mode not in {"LOCAL", "DEPLOYED"}:
            raise RuntimeError("Invalid runtime configuration")
        for value in (self.broker_url, self.seller_url, self.reference_url, *self.cors_origins):
            parsed = urlsplit(value)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                    or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
                    or "*" in value):
                raise RuntimeError("Invalid configured destination")
            try:
                parsed.port
            except ValueError:
                raise RuntimeError("Invalid configured destination") from None
        if self.runtime_mode == "DEPLOYED":
            if any("local_only" in value for value in (self.control_db, self.seller_db, self.reference_db)):
                raise RuntimeError("Explicit deployment credentials required")
            if os.getenv("ENABLE_REFUND_PROBES") == "true" or os.getenv("ENABLE_REFERENCE_CONTROLS") == "true":
                raise RuntimeError("Test controls forbidden in deployment mode")

    def require(self, service: str) -> None:
        required = {
            "api": (self.session_key, self.internal_token, self.webhook_secret),
            "broker": (self.internal_token, self.seller_key),
            "seller": (self.seller_key,), "reference": (self.internal_token,),
        }[service]
        if not all(required):
            raise RuntimeError("Required service credentials unavailable")
        if service == "reference" and self.runtime_mode != "LOCAL":
            raise RuntimeError("Reference service is local test infrastructure")


@lru_cache
def settings() -> Settings:
    names = {
        "control_db": "CONTROL_DATABASE_URL",
        "seller_db": "SELLER_DATABASE_URL",
        "reference_db": "REFERENCE_DATABASE_URL",
        "account_id": "RAZORPAY_ACCOUNT_ID",
        "webhook_secret": "RAZORPAY_WEBHOOK_SECRET",
        "seller_key": "SELLER_SIGNING_KEY",
    }
    values: dict[str, Any] = {
        field.name: os.getenv(names.get(field.name, field.name.upper()), str(field.default))
        for field in fields(Settings) if field.name != "cors_origins"
    }
    values["cors_origins"] = tuple(x.strip() for x in os.getenv("CORS_ORIGINS", "").split(",") if x.strip())
    return Settings(**values)
