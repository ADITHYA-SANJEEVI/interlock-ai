"""Small ASGI boundary: bounded buffering, safe errors and request correlation."""

import logging
import re
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from interlock.config import settings
from interlock.error_codes import SAFE_DETAILS

LOG = logging.getLogger("interlock.http")
MAX_BODY = 256_000
MAX_HEADER = 4096
SAFE_CODES = re.compile(r"[A-Z][A-Z0-9_]{1,100}")


class NoRawAccess(logging.Filter):
    def filter(self, record):
        return False


class ErrorDescription(BaseModel):
    code: str = Field(max_length=100)
    summary: str = Field(max_length=200)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class ErrorEnvelope(BaseModel):
    detail: str = Field(max_length=200)
    error: ErrorDescription


def envelope(status: int, detail: object, request_id: str) -> dict:
    # Existing uppercase domain errors stay stable. Never reflect arbitrary text.
    safe_detail = detail if isinstance(detail, str) and detail in SAFE_DETAILS else None
    code = (
        safe_detail
        if safe_detail and SAFE_CODES.fullmatch(safe_detail)
        else {
            400: "MALFORMED_REQUEST",
            401: "AUTHENTICATION_REQUIRED",
            403: "SCOPE_DENIED",
            404: "RESOURCE_UNAVAILABLE",
            409: "STATE_CONFLICT",
            412: "STALE_PRECONDITION",
            413: "BODY_LIMIT_EXCEEDED",
            422: "INVALID_REQUEST",
            429: "RESOURCE_LIMIT_EXCEEDED",
            503: "DEPENDENCY_UNAVAILABLE",
        }.get(status, "REQUEST_FAILED")
    )
    summary = {
        400: "Malformed request",
        401: "Authentication required",
        403: "Access denied",
        404: "Resource unavailable",
        409: "State conflict",
        412: "Stale precondition",
        413: "Request body exceeds limit",
        422: "Invalid request",
        429: "Resource limit exceeded",
    }.get(status, "Service unavailable" if status >= 500 else "Request rejected")
    return {
        "detail": safe_detail or code,
        "error": {"code": code, "summary": safe_detail or summary, "request_id": request_id},
    }


class Boundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid4().hex  # Never trust/log a client-controlled correlation value.
        scope.setdefault("state", {})["request_id"] = request_id
        headers = scope.get("headers", [])

        async def secured_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-request-id", request_id.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cache-control", b"no-store"),
                ]
                # Fixed route templates only: no tokens, payloads, query strings or paths.
                route = scope.get("route")
                try:
                    LOG.info(
                        "request_complete request_id=%s route=%s status=%s",
                        request_id,
                        getattr(route, "path", "unmatched"),
                        message["status"],
                    )
                except Exception:
                    pass  # Telemetry is never authority or a prerequisite for a response.
            await send(message)

        async def reject(status):
            response = JSONResponse(envelope(status, None, request_id), status_code=status)
            await response(scope, receive, secured_send)

        if len(scope.get("query_string", b"")) > MAX_HEADER or any(len(v) > MAX_HEADER for _, v in headers):
            return await reject(422)
        size = next((v for k, v in headers if k == b"content-length"), None)
        if size is not None:
            if not size.isdigit():
                return await reject(400)
            if len(size) > 8 or int(size) > MAX_BODY:
                return await reject(413)
        chunks = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > MAX_BODY:
                return await reject(413)
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        started = False

        async def tracking_send(message):
            nonlocal started
            started |= message["type"] == "http.response.start"
            await secured_send(message)

        try:
            await self.app(scope, bounded_receive, tracking_send)
        except Exception:
            # ASGI's exception handler sends the safe 500 before re-raising. Prevent
            # server traceback logging from exposing transport/SQL/secret details.
            if not started:
                await reject(500)


def install(app: FastAPI, *, browser: bool = False) -> None:
    # Uvicorn's default access record includes the untrusted raw URL/query.
    # A filter survives logging reconfiguration even if the logger is reenabled.
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, NoRawAccess) for item in access.filters):
        access.addFilter(NoRawAccess())
    if not LOG.handlers:
        LOG.addHandler(logging.StreamHandler())
    LOG.setLevel(logging.INFO)
    for status in (400, 401, 403, 404, 409, 412, 413, 422, 429, 500, 503):
        app.router.responses.setdefault(status, {"model": ErrorEnvelope})

    async def rejected(request: Request, error: Exception):
        assert isinstance(error, StarletteHTTPException)
        return JSONResponse(
            envelope(error.status_code, error.detail, request.state.request_id), status_code=error.status_code
        )

    async def invalid(request: Request, error: Exception):
        return JSONResponse(envelope(422, None, request.state.request_id), status_code=422)

    async def failed(request: Request, error: Exception):
        return JSONResponse(envelope(500, None, request.state.request_id), status_code=500)

    app.add_exception_handler(StarletteHTTPException, rejected)
    app.add_exception_handler(RequestValidationError, invalid)
    app.add_exception_handler(Exception, failed)
    if browser:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings().cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "If-Match"],
            expose_headers=["X-Request-ID"],
        )
    app.add_middleware(Boundary)


def precondition(value: str) -> int:
    if not re.fullmatch(r'(?:\d{1,19}|"\d{1,19}")', value) or int(value.strip('"')) > 2**63 - 1:
        raise HTTPException(422, "INVALID_PRECONDITION")
    return int(value.strip('"'))


def request_schema(*models: type[BaseModel]) -> dict:
    """Document signed/raw and family-specific requests without buffering twice."""

    def inline(schema, definitions):
        if isinstance(schema, dict):
            if "$ref" in schema:
                return inline(definitions[schema["$ref"].split("/")[-1]], definitions)
            return {k: inline(v, definitions) for k, v in schema.items() if k != "$defs"}
        if isinstance(schema, list):
            return [inline(x, definitions) for x in schema]
        return schema

    schemas = [model.model_json_schema() for model in models]
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"anyOf": [inline(schema, schema.get("$defs", {})) for schema in schemas]}
                }
            },
        }
    }
