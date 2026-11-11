"""Isolated analyst API. No provider, broker, issuer, session-signing or owner secrets."""

import os
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse

from interlock.investigation_reads import InvestigationError, ScopedReads
from interlock.investigator import Investigation, InvestigationRequest, InvestigatorModel
from interlock.proposer import ModelConfig


class Limits:
    def __init__(self, *, max_requests=4, max_active=2):
        self.lock = threading.Lock()
        self.active = threading.BoundedSemaphore(max_active)
        self.max_requests = max_requests
        self.requests = defaultdict(deque)

    def take(self, identity):
        key = (identity["tenant_id"], identity["principal"])
        with self.lock:
            now = time.monotonic()
            for old in list(self.requests):
                while self.requests[old] and self.requests[old][0] < now-60:
                    self.requests[old].popleft()
                if not self.requests[old]:
                    del self.requests[old]
            if len(self.requests[key]) >= self.max_requests or not self.active.acquire(blocking=False):
                raise InvestigationError("INVESTIGATION_RATE_LIMIT", 429)
            self.requests[key].append(now)


def bearer(value: str):
    if not value.startswith("Bearer ") or not 16 <= len(value) <= 2048 or not value.isascii():
        raise InvestigationError("AUTHENTICATION_REQUIRED", 401)
    return value[7:]


def create_app(reads, model):
    @asynccontextmanager
    async def lifecycle(app):
        yield
        reads.close()
        model.close()

    app = FastAPI(title="Interlock read-only payment investigation", lifespan=lifecycle)
    limits = Limits()

    @app.middleware("http")
    async def bounded(request, call_next):
        if request.method == "POST" and "content-length" not in request.headers:
            return JSONResponse({"detail": "CONTENT_LENGTH_REQUIRED"}, status_code=411)
        length = request.headers.get("content-length", "0")
        if not length.isdigit() or int(length) > 6000:
            return JSONResponse({"detail": "INVESTIGATION_REQUEST_TOO_LARGE"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(InvestigationError)
    async def failure(request, error):
        return JSONResponse({"detail": error.code, "financial_authority": False,
                             "provider_contacted": False, "database_writes": False,
                             "diagnostic": error.evidence}, status_code=error.status)

    @app.get("/health")
    def health():
        return {"service": "read-only-investigator", "financial_authority": False, "pid": os.getpid()}

    @app.get("/v1/investigations/transactions")
    def transactions(authorization: str = Header(default="")):
        token = bearer(authorization)
        reads.identity(token)
        return reads.transactions(token)

    @app.post("/v1/investigations")
    def investigate(body: InvestigationRequest, authorization: str = Header(default="")):
        token = bearer(authorization)
        identity = reads.identity(token)
        limits.take(identity)
        try:
            return Investigation(reads, model).run(token, body, identity=identity)
        finally:
            limits.active.release()

    return app


def factory():
    forbidden = ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "SESSION_KEY", "BROKER_TOKEN",
                 "PAYPAL_CANONICAL_SIGNING_KEY", "DISPATCH_MUTATE_TOKEN", "DISPATCH_OBSERVE_TOKEN",
                 "MIGRATION_DATABASE_URL", "PGPASSWORD", "OWNER_DATABASE_URL")
    if any(os.environ.get(key) for key in forbidden):
        raise InvestigationError("PRIVILEGED_SECRET_IN_INVESTIGATION_RUNTIME")
    model = InvestigatorModel(ModelConfig(os.environ.get("MODEL_PROVIDER", ""),
                 os.environ.get("MODEL_API_BASE", ""), os.environ.get("MODEL_NAME", ""),
                 os.environ.get("MODEL_API_KEY", ""), os.environ.get("MODEL_TOKEN_LIMIT_PARAMETER", "max_completion_tokens")))
    return create_app(ScopedReads(os.environ.get("INVESTIGATION_DATABASE_URL", ""),
                                 os.environ.get("INVESTIGATION_API_URL", "")), model)
