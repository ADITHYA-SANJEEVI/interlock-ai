"""Opt-in API/broker/seller factories. Nothing is mounted in the frozen apps.

Do not start these services until disposable PostgreSQL acceptance is complete.
Model, broker and seller processes have distinct credentials and DB roles.
"""

import hashlib
import hmac
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
from dotenv import dotenv_values
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from interlock.authority import bearer
from interlock.contracts import Identifier, canonical, signature
from interlock.http_boundary import install, precondition, request_schema
from interlock.paypal_contracts import (
    COMMANDS,
    AIRequest,
    Cancellation,
    CancelUnsent,
    Operation,
    Proof,
    QuoteRequest,
    ReplacementAdmission,
    Signed,
    proof,
)
from interlock.paypal_flow import BoundaryFailure, BrokerIdentity, ProtectedPayPal, intent_digest
from interlock.paypal_kernel import PostgreSQLKernel
from interlock.paypal_sandbox import PayPalFailure, PayPalSandbox, SandboxCredentials, safe_order
from interlock.proposer import ModelConfig, ProposalFailure, Proposer


def app_base(title: str) -> FastAPI:
    app = FastAPI(title=title, version="paypal-v1")
    install(app, browser=True)

    @app.get("/health")
    def liveness():
        return {"process": "RUNNING", "service": title, "contract_version": "paypal-v1", "pid": os.getpid()}

    @app.exception_handler(BoundaryFailure)
    @app.exception_handler(ProposalFailure)
    async def failed(request, error):
        # Exception classes carry server-owned codes, never raw remote messages.
        code = str(error)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,100}", code):
            code = "PAYPAL_BOUNDARY_UNAVAILABLE"
        body = {"detail": code}
        if isinstance(error, ProposalFailure):
            allowed: dict[str, int | str] = {}
            for key in ("http_status", "latency_ms"):
                value = error.evidence.get(key)
                if type(value) is int and 0 <= value <= (599 if key == "http_status" else 300000):
                    allowed[key] = value
            digest = error.evidence.get("response_sha256")
            if isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest):
                allowed["response_sha256"] = digest
            if allowed:
                return JSONResponse({**body, "diagnostic": allowed}, status_code=503)
        return JSONResponse(body, status_code=getattr(error, "status_code", 503))

    return app


def identity_headers(idem: str, expected: str | None = None):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{8,100}", idem):
        raise HTTPException(422, "INVALID_COMMAND_KEY")
    if expected is not None and not re.fullmatch(r'(?:\d+|"\d+")', expected):
        raise HTTPException(422, "INVALID_PRECONDITION")


def create_api(kernel, proposer: Proposer, broker: Callable[[str, dict], dict], reconcile=None,
               *, continuations_enabled: bool = False) -> FastAPI:
    app = app_base("Interlock protected PayPal sandbox API")

    @app.get("/v1/paypal/authority")
    def authority(authorization: str = Header(default="")):
        return kernel("snapshot", {"token": bearer(authorization)})

    @app.get("/v1/paypal/actions/{action_id}")
    def action(action_id: Identifier, authorization: str = Header(default="")):
        return kernel("action", {"token": bearer(authorization), "actionid": action_id})

    @app.get("/v1/paypal/proposals/{proposal_id}")
    def recorded_proposal(proposal_id: UUID, authorization: str = Header(default="")):
        # Existing SQL enforces the original requester, tenant and active grant.
        # Reading an immutable proposal never invokes the model or broker.
        return kernel("proposal", {"token": bearer(authorization), "pid": str(proposal_id)})

    @app.post("/v1/paypal/proposals")
    def propose(body: AIRequest, authorization: str = Header(default=""),
                idempotency_key: str = Header(default="")):
        token = bearer(authorization)
        identity_headers(idempotency_key)
        args = {"token": token, "grantid": body.grant_id, "idem": idempotency_key,
                "intent_hash": intent_digest(body.intent)}
        prior = kernel("recover_proposal", args)
        if prior is not None:
            return prior
        scope = kernel("scope", {"token": token, "grantid": body.grant_id})
        result = proposer.propose(body.intent, scope["beneficiaries"])
        if result["extraction"]["status"] != "PROPOSED":
            return {"result": result, "financial_authority": False}
        # Model I/O completed outside SQL. The kernel independently rechecks scope.
        return kernel("record_proposal", {**args, "result": json.dumps(result)})

    def resolve_quote(body: QuoteRequest, authorization: str, idempotency_key: str, *, renewal: bool):
        token = bearer(authorization)
        identity_headers(idempotency_key)
        p = kernel("proposal", {"token": token, "pid": str(body.proposal_id)})
        suggestion = p["result"]["extraction"]["proposal"]
        # Original request identities remain unchanged. Explicit renewals have
        # their own durable seller identity; retries cannot extend an old quote.
        request_id = "proposal_" + str(body.proposal_id)
        if renewal:
            request_id = "renewal_" + hashlib.sha256(canonical({
                "proposal_id": str(body.proposal_id), "idempotency_key": idempotency_key})).hexdigest()
        sealed = broker("quote", {"schema": "paypal-quote-request-v1", "tenant_id": p["tenant_id"],
                                  "request_id": request_id,
                                  "product_id": suggestion["product_id"], "beneficiary": suggestion["beneficiary"],
                                  "quantity": 1, "currency": "USD",
                                  "expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat()})
        sealed = Signed.model_validate(sealed).model_dump()
        q = kernel("quote", {"token": token, **sealed, "idem": idempotency_key})
        return kernel("link_quote", {"token": token, "pid": str(body.proposal_id), "quoteid": q["id"]})

    @app.post("/v1/paypal/quotes")
    def quote(body: QuoteRequest, authorization: str = Header(default=""),
              idempotency_key: str = Header(default="")):
        return resolve_quote(body, authorization, idempotency_key, renewal=False)

    @app.post("/v1/paypal/quote-renewals")
    def renew_quote(body: QuoteRequest, authorization: str = Header(default=""),
                    idempotency_key: str = Header(default="")):
        # Scoped stored proposal + authentic seller signature + existing SQL
        # checks. No approval transfer, action mutation or financial admission.
        return resolve_quote(body, authorization, idempotency_key, renewal=True)

    @app.post("/v1/paypal/commands/{family}", openapi_extra=request_schema(*COMMANDS.values()))
    async def command(family: str, request: Request, authorization: str = Header(default=""),
                      idempotency_key: str = Header(default=""), if_match: str = Header(default="")):
        if family not in COMMANDS:
            raise HTTPException(404, "COMMAND_NOT_EXPOSED")
        token = bearer(authorization)
        identity_headers(idempotency_key, if_match)
        try:
            body = COMMANDS[family].model_validate_json(await request.body()).model_dump(mode="json")
        except ValidationError:
            raise HTTPException(422, "INVALID_COMMAND_SCHEMA") from None
        body.pop("currency")  # Explicit v1 facade supplies USD; old kernel DTOs remain INR.
        return kernel("command", {"token": token, "family": family, "body": json.dumps(body),
                                  "expected": precondition(if_match), "idem": idempotency_key})

    @app.post("/v1/paypal/create")
    def create(body: Operation, authorization: str = Header(default="")):
        token = bearer(authorization)
        kernel("user", {"token": token, "opid": body.operation_id, "verb": "VIEW"})
        return broker("create", body.model_dump())

    @app.post("/v1/paypal/cancellations", response_model=Cancellation)
    def cancel_unsent(body: CancelUnsent, authorization: str = Header(default=""),
                      idempotency_key: str = Header(default=""), if_match: str = Header(default="")):
        identity_headers(idempotency_key, if_match)
        return kernel("cancel_unsent", {"token": bearer(authorization), "opid": body.operation_id,
                      "expected": precondition(if_match), "dispatch_expected": body.dispatch_revision, "idem": idempotency_key})

    @app.get("/v1/paypal/cancellations/{operation_id}", response_model=Cancellation)
    def cancellation(operation_id: Identifier, authorization: str = Header(default="")):
        result = kernel("read_cancellation", {"token": bearer(authorization), "opid": operation_id})
        if result is None:
            raise HTTPException(404, "CANCELLATION_NOT_FOUND")
        return result

    @app.post("/v1/paypal/replacement-admissions")
    def admit_replacement(body: ReplacementAdmission, authorization: str = Header(default=""),
                          idempotency_key: str = Header(default=""), if_match: str = Header(default="")):
        identity_headers(idempotency_key, if_match)
        value = body.model_dump(mode="json")
        parent = value.pop("cancelled_operation_id")
        value.pop("currency")
        return kernel("admit_replacement", {"token": bearer(authorization), "parentid": parent,
                      "body": json.dumps(value), "expected": precondition(if_match), "idem": idempotency_key})

    @app.post("/v1/paypal/observe")
    def observe(body: Operation, authorization: str = Header(default="")):
        kernel("user", {"token": bearer(authorization), "opid": body.operation_id, "verb": "OBSERVE"})
        return broker("observe", body.model_dump())

    @app.post("/v1/paypal/consent")
    def consent(body: Operation, authorization: str = Header(default=""),
                idempotency_key: str = Header(default=""), if_match: str = Header(default="")):
        identity_headers(idempotency_key, if_match)
        return kernel("consent", {"token": bearer(authorization), "opid": body.operation_id,
                                  "expected": precondition(if_match), "idem": idempotency_key})

    @app.post("/v1/paypal/capture-requests")
    def capture(body: Operation, authorization: str = Header(default=""),
                idempotency_key: str = Header(default=""), if_match: str = Header(default="")):
        identity_headers(idempotency_key, if_match)
        token = bearer(authorization)
        kernel("user", {"token": token, "opid": body.operation_id, "verb": "DISCLOSE"})
        # UI must refresh evidence first and bind If-Match to the displayed exact
        # runtime revision. Capture performs another canonical read + cutoff later.
        kernel("capture_request", {"token": token, "opid": body.operation_id,
                                   "expected": precondition(if_match), "idem": idempotency_key})
        return broker("capture", body.model_dump())

    @app.get("/v1/paypal/evidence/{operation_id}", response_model=Proof)
    def evidence(operation_id: Identifier, authorization: str = Header(default="")):
        return proof(kernel("inspect", {"token": bearer(authorization), "opid": operation_id}))

    if reconcile is not None:
        from interlock.reconciliation import Run, RunRequest

        @app.post("/v1/paypal/reconciliation/runs", response_model=Run)
        def reconciliation_run(body: RunRequest, authorization: str = Header(default="")):
            return reconcile(bearer(authorization), body)

    if continuations_enabled:
        install_continuation_routes(app, kernel, broker)
    return app


def install_continuation_routes(app, kernel, broker):
    from interlock.paypal_continuation import ContinuationApprove, ContinuationPrepare

    @app.post("/v2/paypal/capture-continuations")
    def prepare(body: ContinuationPrepare, authorization: str = Header(default=""),
                idempotency_key: str = Header(default=""), if_match: str = Header(default="")):
        identity_headers(idempotency_key, if_match)
        token = bearer(authorization)
        # Caller refreshes canonical evidence first. Seed authenticates/snapshots
        # its exact revision BEFORE seller HTTP; seed retries reuse frozen terms.
        args = {"token": token, "opid": body.operation_id, "cid": str(body.continuation_id),
                "expected": precondition(if_match)}
        seed = kernel("continuation_seed", args)
        sealed = Signed.model_validate(broker("continuation-attestation", seed))
        return kernel("continuation_register", {**args, "raw": sealed.raw, "sig": sealed.signature,
                                                "idem": idempotency_key})

    @app.get("/v2/paypal/capture-continuations/{continuation_id}")
    def inspect_continuation(continuation_id: UUID, authorization: str = Header(default="")):
        return kernel("continuation_inspect", {"token": bearer(authorization), "cid": str(continuation_id)})

    @app.post("/v2/paypal/capture-continuations/{continuation_id}/approvals")
    def approve_continuation(continuation_id: UUID, body: ContinuationApprove,
                            authorization: str = Header(default=""), idempotency_key: str = Header(default=""),
                            if_match: str = Header(default="")):
        identity_headers(idempotency_key, if_match)
        return kernel("continuation_approve", {"token": bearer(authorization), "opid": body.operation_id,
                      "cid": str(continuation_id), "digest": body.continuation_digest,
                      "expires": body.expires_at.isoformat(), "expected": precondition(if_match), "idem": idempotency_key})

    @app.post("/v2/paypal/capture-continuations/{continuation_id}/capture-requests")
    def capture_continuation(continuation_id: UUID, body: Operation, authorization: str = Header(default=""),
                             idempotency_key: str = Header(default=""), if_match: str = Header(default="")):
        identity_headers(idempotency_key, if_match)
        kernel("continuation_capture_request", {"token": bearer(authorization), "opid": body.operation_id,
               "cid": str(continuation_id), "expected": precondition(if_match), "idem": idempotency_key})
        return broker("continuation-capture", {**body.model_dump(), "continuation_id": str(continuation_id)})

    @app.post("/v2/paypal/capture-continuations/{continuation_id}/revocations")
    def revoke_continuation(continuation_id: UUID, body: Operation, authorization: str = Header(default="")):
        return kernel("continuation_revoke", {"token": bearer(authorization), "opid": body.operation_id,
                                             "cid": str(continuation_id)})


def broker_app(driver: ProtectedPayPal, internal_token: str, seller: Callable[[str, dict], dict],
               quote_request_key: str, *, mutations_enabled: bool = True,
               continuations_enabled: bool = False) -> FastAPI:
    if len(internal_token) < 32 or len(quote_request_key) < 32:
        raise BoundaryFailure("PAYPAL_SERVICE_CREDENTIALS_REQUIRED")

    def internal(authorization: str = Header(default="")):
        if not authorization.isascii() or not hmac.compare_digest(authorization, "Bearer " + internal_token):
            raise HTTPException(401, "AUTHENTICATION_REQUIRED")

    app = app_base("Interlock credential-isolated PayPal sandbox broker")

    @app.post("/v1/internal/provider-preflight", dependencies=[Depends(internal)])
    def provider_preflight():
        # Fixed, previously completed M0 order. This diagnostic cannot create,
        # capture, register a demo receipt or satisfy a new operation's evidence.
        try:
            data = safe_order(driver.provider.get_order("79106124XW461335C"))
        except PayPalFailure as error:
            return JSONResponse({"detail": "PAYPAL_PREFLIGHT_UNAVAILABLE", "provider_evidence": error.evidence}, status_code=503)
        except (ValueError, KeyError, TypeError):
            raise BoundaryFailure("PAYPAL_PREFLIGHT_SCHEMA_REJECTED") from None
        units = data["purchase_units"]
        valid = (data["id"] == "79106124XW461335C" and len(units) == 1
                 and units[0].get("merchant_id") == driver.identity.merchant_id
                 and units[0].get("amount") == {"currency_code": "USD", "value": "1.00"})
        return JSONResponse({"classification": "PRIOR_ORDER_CANONICAL_BINDING_DIAGNOSTIC", "verified": valid,
                             "order": data, "demo_operation_settlement_proven": False,
                             "provider_mutations": 0}, status_code=200 if valid else 409)

    @app.post("/v1/internal/create", dependencies=[Depends(internal)])
    def create(body: Operation):
        if not mutations_enabled:
            raise HTTPException(423, "PAYPAL_PROVIDER_MUTATIONS_LOCKED")
        return driver.create(body.operation_id)

    @app.post("/v1/internal/capture", dependencies=[Depends(internal)])
    def capture(body: Operation):
        if not mutations_enabled:
            raise HTTPException(423, "PAYPAL_PROVIDER_MUTATIONS_LOCKED")
        return driver.capture(body.operation_id)

    @app.post("/v1/internal/observe", dependencies=[Depends(internal)])
    def observe(body: Operation):
        return driver.observe(body.operation_id)

    @app.post("/v1/internal/quote", dependencies=[Depends(internal)])
    def quote(body: dict):
        # This authenticated broker route is not exposed to the model/browser.
        raw = canonical(body)
        return seller("quote", {"raw": raw.decode(), "signature": signature(quote_request_key, raw)})

    if continuations_enabled:
        from interlock.paypal_continuation import ContinuationOperation

        @app.post("/v1/internal/continuation-attestation", dependencies=[Depends(internal)])
        def continuation_attestation(body: dict):
            raw = canonical(body)
            return seller("continuation-attestation", {"raw": raw.decode(),
                                                        "signature": signature(quote_request_key, raw)})

        @app.post("/v1/internal/continuation-capture", dependencies=[Depends(internal)])
        def continuation_capture(body: ContinuationOperation):
            from interlock.paypal_continuation import capture

            if not mutations_enabled:
                raise HTTPException(423, "PAYPAL_PROVIDER_MUTATIONS_LOCKED")
            return capture(driver, body.operation_id, str(body.continuation_id))

    return app


def local_caller(origin: str, prefix: str, token: str = ""):
    from urllib.parse import urlsplit

    try:
        p = urlsplit(origin)
        p.port
    except ValueError:
        raise BoundaryFailure("EXPLICIT_LOCAL_PAYPAL_SERVICE_REQUIRED") from None
    if (p.scheme != "http" or p.hostname not in {"localhost", "127.0.0.1", "::1"}
            or p.path not in {"", "/"} or p.query or p.fragment or p.username or p.password):
        raise BoundaryFailure("EXPLICIT_LOCAL_PAYPAL_SERVICE_REQUIRED")

    def call(verb: str, body: dict) -> dict:
        if verb not in {"create", "capture", "observe", "quote", "finalize", "reconciliation-read",
                        "continuation-attestation", "continuation-capture"}:
            raise BoundaryFailure("SERVICE_VERB_DENIED")
        try:
            with httpx.Client(timeout=60, trust_env=False, follow_redirects=False,
                              transport=httpx.HTTPTransport(retries=0)) as client:
                r = client.post(origin.rstrip("/") + prefix + verb, json=body,
                                headers={"Authorization": "Bearer " + token} if token else {})
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict):
                raise ValueError("schema")
            return data
        except (httpx.HTTPError, ValueError):
            raise BoundaryFailure("SERVICE_OUTCOME_UNKNOWN_RECOVER_EXISTING_COMMAND") from None

    return call


def api_factory():
    # Load no PayPal profile/secret in this process. Model settings are separate.
    config = ModelConfig(os.environ.get("MODEL_PROVIDER", ""), os.environ.get("MODEL_API_BASE", ""),
                         os.environ.get("MODEL_NAME", ""), os.environ.get("MODEL_API_KEY", ""),
                         os.environ.get("MODEL_TOKEN_LIMIT_PARAMETER", "max_completion_tokens"))
    from interlock.paypal_reconciliation import execute

    kernel = PostgreSQLKernel.configured("api")
    seller = local_caller(os.environ.get("PAYPAL_SELLER_URL", ""), "/v1/paypal/seller/")
    return create_api(kernel, Proposer(config),
                      local_caller(os.environ.get("PAYPAL_BROKER_URL", ""), "/v1/internal/",
                                   os.environ.get("PAYPAL_INTERNAL_TOKEN", "")),
                      lambda token, body: execute(kernel, seller, token, body),
                      continuations_enabled=os.environ.get("PAYPAL_CAPTURE_CONTINUATION_ENABLED") == "true")


def broker_factory():
    # Only this factory opens the explicitly separate US merchant profile.
    kernel = PostgreSQLKernel.configured("broker")
    path = Path(os.environ.get("PAYPAL_CREDENTIAL_PROFILE_PATH") or
                Path(__file__).resolve().parents[2] / "tmp/hackathon-private/paypal-us.env")
    p = dotenv_values(path)
    required = {"PAYPAL_SELLER_COUNTRY": "US", "PAYPAL_BUYER_COUNTRY": "US", "PAYPAL_CURRENCY": "USD",
                "PAYPAL_ACCOUNT_TYPE": "BUSINESS", "PAYPAL_APP_TYPE": "MERCHANT", "PAYPAL_APP_BINDING_CONFIRMED": "true"}
    if any(p.get(k) != v for k, v in required.items()):
        raise BoundaryFailure("VERIFIED_US_BROKER_CONFIGURATION_REQUIRED")
    identity = BrokerIdentity(os.environ.get("DISPATCH_MUTATE_TOKEN", ""), os.environ.get("DISPATCH_OBSERVE_TOKEN", ""),
                              os.environ.get("PAYPAL_CANONICAL_SIGNING_KEY", ""), p.get("PAYPAL_MERCHANT_ID") or "")
    provider = PayPalSandbox(SandboxCredentials(p.get("PAYPAL_CLIENT_ID") or "", p.get("PAYPAL_CLIENT_SECRET") or ""))
    return broker_app(ProtectedPayPal(kernel, provider, identity), os.environ.get("PAYPAL_INTERNAL_TOKEN", ""),
                      local_caller(os.environ.get("PAYPAL_SELLER_URL", ""), "/v1/paypal/seller/"),
                      os.environ.get("PAYPAL_QUOTE_REQUEST_KEY", ""),
                      mutations_enabled=os.environ.get("PAYPAL_PROVIDER_MUTATIONS_ENABLED") == "true",
                      continuations_enabled=os.environ.get("PAYPAL_CAPTURE_CONTINUATION_ENABLED") == "true")


def session_factory():
    """Expose unchanged authentication handlers in their own secret-scoped process."""
    from interlock.api import app as existing

    app = app_base("Interlock local scoped authentication")
    for route in existing.routes:
        if getattr(route, "path", None) in {"/session", "/me"}:
            app.router.routes.append(route)
    return app


def seller_factory():
    from urllib.parse import urlsplit

    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from interlock.db import transaction

    url = os.environ.get("PAYPAL_SELLER_DATABASE_URL", "")
    parsed = urlsplit(url)
    if parsed.scheme != "postgresql+psycopg" or parsed.username != "interlock_seller" or not parsed.hostname:
        raise BoundaryFailure("EXPLICIT_PAYPAL_SELLER_DATABASE_REQUIRED")
    app = app_base("Interlock USD controlled workspace seller")

    def sql(statement, params):
        try:
            with transaction(url) as db:
                if db.scalar(text("SELECT session_user")) != "interlock_seller":
                    raise BoundaryFailure("PAYPAL_RUNTIME_ROLE_MISMATCH")
                return db.scalar(text(statement), params)
        except DBAPIError:
            raise BoundaryFailure("SELLER_BOUNDARY_REJECTED_OR_UNAVAILABLE") from None

    @app.post("/v1/paypal/seller/quote", response_model=Signed)
    def quote(body: Signed):
        return sql("SELECT seller_truth.paypal_quote_v1(:raw,:signature)", body.model_dump())

    @app.post("/v1/paypal/seller/finalize", response_model=Signed)
    def finalize(body: Signed):
        return sql("SELECT seller_truth.paypal_finalize_v1(:raw,:signature)", body.model_dump())

    if os.environ.get("PAYPAL_CAPTURE_CONTINUATION_ENABLED") == "true":
        @app.post("/v1/paypal/seller/continuation-attestation", response_model=Signed)
        def continuation_attestation(body: Signed):
            return sql("SELECT seller_truth.paypal_continuation_v2(:raw,:signature)", body.model_dump())

    @app.post("/v1/paypal/seller/reconciliation-read", response_model=Signed)
    def reconciliation_read(body: Signed):
        return sql("SELECT seller_truth.reconciliation_read(:raw,:signature)", body.model_dump())

    @app.get("/v1/paypal/seller/workspace/{seat_id}")
    def workspace(seat_id: Identifier, authorization: str = Header(default="")):
        return sql("SELECT seller_truth.workspace(:token,:seat,NULL)",
                   {"token": authorization.removeprefix("Bearer "), "seat": seat_id})

    return app
