"""Loopback planning/case sidecar. Existing narrow reads; no financial commands."""

import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Path
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response

from interlock.investigation_api import Limits, bearer
from interlock.investigation_reads import InvestigationError, ScopedReads
from interlock.investigator import InvestigatorModel
from interlock.operation_cases import case_file, readable_report
from interlock.proposer import ModelConfig
from interlock.purchase_planner import PlanningRequest, PurchasePlanner


def create_app(reads, model):
    @asynccontextmanager
    async def lifespan(app):
        yield
        reads.close()
        model.close()

    app = FastAPI(title="Interlock bounded planning and recorded operations", lifespan=lifespan)
    planning_limits = Limits()
    read_limits = Limits(max_requests=40, max_active=4)

    @app.middleware("http")
    async def bounded(request, call_next):
        length = request.headers.get("content-length", "0")
        if not length.isdigit() or int(length) > 6000:
            return JSONResponse({"detail": "COMMERCE_REQUEST_TOO_LARGE"}, status_code=413)
        if request.method == "POST" and "content-length" not in request.headers:
            return JSONResponse({"detail": "CONTENT_LENGTH_REQUIRED"}, status_code=411)
        result = await call_next(request)
        result.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            }
        )
        return result

    @app.exception_handler(InvestigationError)
    async def failure(request, error):
        return JSONResponse(
            {
                "detail": error.code,
                "financial_authority": False,
                "database_writes": False,
                "provider_contacted": False,
                "diagnostic": error.evidence,
            },
            status_code=error.status,
        )

    @app.exception_handler(RequestValidationError)
    async def validation(request, error):
        return JSONResponse({"detail": "COMMERCE_REQUEST_SCHEMA_REJECTED"}, status_code=422)

    @app.get("/health")
    def health():
        return {"service": "read-only-commerce", "pid": os.getpid(), "financial_authority": False}

    @app.post("/v1/commerce/plans")
    def plan(body: PlanningRequest, authorization: str = Header(default="")):
        token = bearer(authorization)
        identity = reads.identity(token)
        if "PURCHASE_REQUESTER" not in identity["roles"]:
            raise InvestigationError("PURCHASE_PLANNER_REQUESTER_REQUIRED", 403)
        planning_limits.take(identity)
        try:
            return PurchasePlanner(reads, model).run(token, body)
        finally:
            planning_limits.active.release()

    @app.get("/v1/commerce/transactions")
    def transactions(authorization: str = Header(default="")):
        token = bearer(authorization)
        identity = reads.identity(token)
        if not set(identity["roles"]) & {"PURCHASE_REQUESTER", "PURCHASE_ISSUER", "PURCHASE_APPROVER"}:
            raise InvestigationError("OPERATIONS_READER_REQUIRED", 403)
        return reads.transactions(token)

    def scoped_case(operation_id, authorization):
        token = bearer(authorization)
        identity = reads.identity(token)
        # Bounded four concurrent source packages; separate two-planner limit.
        read_limits.take(identity)
        try:
            return case_file(reads, token, operation_id)
        finally:
            read_limits.active.release()

    @app.get("/v1/commerce/cases/{operation_id}")
    def case(
        operation_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,100}$"), authorization: str = Header(default="")
    ):
        return scoped_case(operation_id, authorization)

    @app.get("/v1/commerce/cases/{operation_id}/export.{format}")
    def export(
        format: str,
        operation_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,100}$"),
        authorization: str = Header(default=""),
    ):
        if format not in {"json", "html"}:
            raise InvestigationError("UNSUPPORTED_EVIDENCE_FORMAT", 404)
        package = scoped_case(operation_id, authorization)
        headers = {
            "Content-Disposition": f'attachment; filename="interlock-{operation_id}.{format}"',
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; sandbox",
        }
        return (
            HTMLResponse(readable_report(package), headers=headers)
            if format == "html"
            else Response(json.dumps(package, indent=2), media_type="application/json", headers=headers)
        )

    return app


def factory():
    forbidden = (
        "PAYPAL_CLIENT_ID",
        "PAYPAL_CLIENT_SECRET",
        "SESSION_KEY",
        "BROKER_TOKEN",
        "PAYPAL_INTERNAL_TOKEN",
        "PAYPAL_QUOTE_REQUEST_KEY",
        "PAYPAL_CANONICAL_SIGNING_KEY",
        "DISPATCH_MUTATE_TOKEN",
        "DISPATCH_OBSERVE_TOKEN",
        "MIGRATION_DATABASE_URL",
        "PGPASSWORD",
        "OWNER_DATABASE_URL",
    )
    if any(os.environ.get(key) for key in forbidden):
        raise InvestigationError("PRIVILEGED_SECRET_IN_COMMERCE_RUNTIME")
    config = ModelConfig(
        os.environ.get("MODEL_PROVIDER", ""),
        os.environ.get("MODEL_API_BASE", ""),
        os.environ.get("MODEL_NAME", ""),
        os.environ.get("MODEL_API_KEY", ""),
        os.environ.get("MODEL_TOKEN_LIMIT_PARAMETER", "max_completion_tokens"),
    )
    return create_app(
        ScopedReads(
            os.environ.get("INVESTIGATION_DATABASE_URL", ""), os.environ.get("INVESTIGATION_API_URL", "")
        ),
        InvestigatorModel(config),
    )
