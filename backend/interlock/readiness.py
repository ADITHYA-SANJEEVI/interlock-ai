"""Local dependency/schema readiness; never claims provider business health."""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from interlock.config import settings
from interlock.db import transaction
from interlock.security import internal_headers

HEAD = "0020_pass6_boundaries"


class Ready(BaseModel):
    service: str
    ready: bool
    migration_head: str


def install(app: FastAPI, service: str) -> None:
    @asynccontextmanager
    async def lifespan(application):
        settings().require(service)
        yield

    app.router.lifespan_context = lifespan

    @app.get("/ready", response_model=Ready)
    def ready():
        try:
            settings().require(service)
            url = getattr(
                settings(),
                {
                    "api": "control_db",
                    "broker": "control_db",
                    "seller": "seller_db",
                    "reference": "reference_db",
                }[service],
            )
            with transaction(url) as db:
                if db.scalar(text("SELECT version_num FROM alembic_version")) != HEAD:
                    raise ValueError("incompatible")
                # Check critical definer API presence in addition to migration label.
                functions = {
                    "api": "reconciliation.inspect(text,text,uuid,uuid,integer)",
                    "broker": "dispatch.commit_handoff(text,text,text,text,boolean)",
                    "seller": "seller_truth.reconciliation_read(text,text)",
                    "reference": "ref_returns.read_refund(text,text)",
                }
                if (
                    db.scalar(text("SELECT to_regprocedure(:signature)"), {"signature": functions[service]})
                    is None
                ):
                    raise ValueError("incompatible")
            if service in {"api", "broker"}:
                destinations: list[tuple[str, dict[str, str]]] = [(settings().seller_url, {})]
                destinations.append(
                    (settings().broker_url, internal_headers())
                    if service == "api"
                    else (settings().reference_url, internal_headers())
                )
                with httpx.Client(timeout=2, follow_redirects=False) as client:
                    for destination, headers in destinations:
                        response = client.get(destination + "/ready", headers=headers)
                        if response.status_code != 200 or response.json().get("ready") is not True:
                            raise ValueError("unavailable")
        except Exception:
            raise HTTPException(503, "DEPENDENCY_UNAVAILABLE") from None
        return Ready(service=service, ready=True, migration_head=HEAD)
