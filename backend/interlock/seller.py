import hashlib
import hmac
import json
import time
from datetime import timedelta
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from interlock.config import settings
from interlock.contracts import (
    Identifier,
    SeatLine,
    SellerTicket,
    WorkspaceNote,
    canonical,
    digest,
    signature,
    verify,
)
from interlock.db import transaction
from interlock.http_boundary import install as install_http
from interlock.http_boundary import request_schema
from interlock.models import (
    Seat,
    SellerConsumption,
    SellerEvent,
    SellerOrder,
    SellerQuote,
    SellerUser,
    WorkspaceEntry,
)
from interlock.read_contracts import Health, SignedCapability, TicketResult, Workspace, WorkspaceWrite
from interlock.readiness import install as install_readiness

app = FastAPI(title="Interlock controlled workspace seller")
install_http(app, browser=True)
install_readiness(app, "seller")
SELLER = "workspace-seller-v1"
CATALOG_REVISION = "workspace-catalog-1"
UNIT_PRICE = 3000


@app.post("/truth/finalize", response_model=SignedCapability, response_model_exclude_unset=True)
def finalize(body: SignedCapability):
    return truth_sql("SELECT seller_truth.finalize(:raw,:signature)", body.model_dump())


@app.post("/truth/reconciliation-read", response_model=SignedCapability, response_model_exclude_unset=True)
def reconciliation_read(body: SignedCapability):
    return truth_sql("SELECT seller_truth.reconciliation_read(:raw,:signature)", body.model_dump())


@app.post("/truth/return-lock", response_model=SignedCapability, response_model_exclude_unset=True)
def return_lock(body: SignedCapability):
    return truth_sql("SELECT seller_truth.return_lock(:raw,:signature)", body.model_dump())


@app.post("/truth/return-close", response_model=SignedCapability, response_model_exclude_unset=True)
def return_close(body: SignedCapability):
    return truth_sql("SELECT seller_truth.return_close(:raw,:signature)", body.model_dump())


def truth_sql(statement, parameters):
    try:
        with transaction(settings().seller_db) as db:
            return db.scalar(text(statement), parameters)
    except DBAPIError as error:
        raise HTTPException(403 if getattr(error.orig, "sqlstate", "") == "42501" else 409,
                            "SELLER_TRUTH_BOUNDARY_REJECTED") from None


@app.get("/truth/workspace/{seat_id}", response_model=Workspace, response_model_exclude_unset=True)
def truth_workspace(seat_id: Identifier, authorization: str = Header(default="")):
    return truth_sql("SELECT seller_truth.workspace(:token,:seat,NULL)",
                     {"token": authorization.removeprefix("Bearer "), "seat": seat_id})


@app.post("/truth/workspace/{seat_id}", response_model=Workspace, response_model_exclude_unset=True)
def truth_write(seat_id: Identifier, note: WorkspaceNote, authorization: str = Header(default="")):
    return truth_sql("SELECT seller_truth.workspace(:token,:seat,:note)",
                     {"token": authorization.removeprefix("Bearer "), "seat": seat_id, "note": note.text})


def now(db):
    return db.scalar(select(func.clock_timestamp()))


def event(db, tenant, kind, resource, detail):
    db.add(SellerEvent(id=uuid4().hex, tenant_id=tenant, kind=kind, resource_id=resource, detail=detail))


@app.get("/health", response_model=Health, response_model_exclude_unset=True)
def health():
    return {"service": "controlled-seller", "product": "protected named workspace seats"}


@app.post("/tickets", response_model=TicketResult, response_model_exclude_unset=True,
          openapi_extra=request_schema(SellerTicket))
async def tickets(request: Request, x_seller_signature: str = Header(default="")):
    raw = await request.body()
    if len(raw) > 16000:
        raise HTTPException(413, "Ticket too large")
    verify(settings().seller_key, raw, x_seller_signature)
    try:
        ticket = SellerTicket.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(422, "INVALID_REQUEST") from None
    wire_body = json.loads(raw)["body"]
    if ticket.expires_at < time.time() or ticket.body_digest != digest(wire_body):
        raise HTTPException(409, "Expired or mismatched ticket")
    result = execute(ticket, wire_body)
    return {"result": result, "signature": signature(settings().seller_key, canonical(result))}


def execute(ticket: SellerTicket, wire_body=None):
    with transaction(settings().seller_db) as db:
        # Serialize exact operation identity, including concurrent first deliveries.
        lock_key = int(hashlib.sha256(ticket.operation_id.encode()).hexdigest()[:15], 16)
        db.execute(select(func.pg_advisory_xact_lock(lock_key)))
        previous = db.get(SellerConsumption, ticket.operation_id)
        body = wire_body if wire_body is not None else ticket.body.model_dump()
        binding = digest({"tenant": ticket.tenant_id, "verb": ticket.verb, "body": body})
        if previous:
            if previous.tenant_id != ticket.tenant_id or previous.body_digest != binding:
                raise HTTPException(409, "Seller operation body substitution")
            return previous.result
        if body.get("seller") != SELLER:
            raise HTTPException(409, "Seller binding mismatch")
        if ticket.verb == "quote":
            lines = [SeatLine.model_validate(line).model_dump() for line in body["lines"]]
            if not 1 <= len(lines) <= 5 or len({x["beneficiary"] for x in lines}) != len(lines):
                raise HTTPException(422, "Exact distinct beneficiary seats required")
            quote_id = "quote_" + uuid4().hex
            expiry = now(db) + timedelta(minutes=30)
            result = {
                "id": quote_id,
                "tenant_id": ticket.tenant_id,
                "seller": SELLER,
                "catalog_revision": CATALOG_REVISION,
                "revision": 1,
                "currency": "INR",
                "lines": [
                    {**x, "unit_price": UNIT_PRICE, "allocation_id": "seat_" + uuid4().hex} for x in lines
                ],
                "amount": UNIT_PRICE * len(lines),
                "expires_at": expiry.isoformat(),
            }
            db.add(SellerQuote(id=quote_id, tenant_id=ticket.tenant_id, body=result, expires_at=expiry))
            event(db, ticket.tenant_id, "QUOTE_CREATED", quote_id, {"amount": result["amount"]})
        elif ticket.verb == "accept":
            quote_lock = int(hashlib.sha256(body["quote_id"].encode()).hexdigest()[:15], 16)
            db.execute(select(func.pg_advisory_xact_lock(quote_lock)))
            quote = db.scalar(
                select(SellerQuote).where(
                    SellerQuote.id == body["quote_id"], SellerQuote.tenant_id == ticket.tenant_id
                )
            )
            if not quote or quote.expires_at <= now(db) or digest(quote.body) != body["quote_digest"]:
                raise HTTPException(409, "Quote expired or exact terms mismatch")
            order_id = "seller_order_" + uuid4().hex
            order = SellerOrder(
                id=order_id,
                tenant_id=ticket.tenant_id,
                quote_id=quote.id,
                purchase_id=body["purchase_id"],
                terms=quote.body,
                revision=1,
            )
            db.add(order)
            db.flush()
            for line in quote.body["lines"]:
                db.add(
                    Seat(
                        id=line["allocation_id"],
                        tenant_id=ticket.tenant_id,
                        order_id=order_id,
                        beneficiary=line["beneficiary"],
                        active=False,
                        revision=1,
                    )
                )
            result = {
                "id": order_id,
                "purchase_id": order.purchase_id,
                "tenant_id": ticket.tenant_id,
                "seller": SELLER,
                "terms_digest": digest(quote.body),
                "revision": 1,
                "status": "ACCEPTED",
            }
            event(db, ticket.tenant_id, "ORDER_ACCEPTED", order_id, result)
        else:
            order = db.scalar(
                select(SellerOrder)
                .where(SellerOrder.id == body["order_id"], SellerOrder.tenant_id == ticket.tenant_id)
                .with_for_update()
            )
            if not order or order.purchase_id != body["purchase_id"]:
                raise HTTPException(404, "Seller order outside scope")
            seats = db.scalars(
                select(Seat).where(Seat.order_id == order.id).order_by(Seat.id).with_for_update()
            ).all()
            if ticket.verb == "fulfill":
                if body["expected_revision"] != order.revision or body["terms_digest"] != digest(order.terms):
                    raise HTTPException(409, "Seller revision or terms mismatch")
                if sorted(body["allocation_ids"]) != sorted(x.id for x in seats):
                    raise HTTPException(409, "Allocation substitution")
                # Trust comes from broker-signed exact instruction, never client paid=true.
                for seat in seats:
                    seat.active = True
                    seat.revision += 1
                order.revision += 1
                event(db, ticket.tenant_id, "SEATS_ACTIVATED", order.id, {"payment_id": body["payment_id"]})
            result = {
                "id": order.id,
                "tenant_id": ticket.tenant_id,
                "seller": SELLER,
                "purchase_id": order.purchase_id,
                "revision": order.revision,
                "terms_digest": digest(order.terms),
                "seats": [
                    {"id": x.id, "beneficiary": x.beneficiary, "active": x.active, "revision": x.revision}
                    for x in seats
                ],
            }
        if ticket.verb != "read":
            db.add(
                SellerConsumption(
                    id=ticket.operation_id, tenant_id=ticket.tenant_id, body_digest=binding, result=result
                )
            )
        return result


def workspace_user(authorization: str = Header(default="")):
    token_hash = hashlib.sha256(authorization.removeprefix("Bearer ").encode()).hexdigest()
    with transaction(settings().seller_db) as db:
        users = db.scalars(select(SellerUser)).all()
        for user in users:
            if hmac.compare_digest(user.token_hash, token_hash):
                return user
    raise HTTPException(401, "Seller beneficiary identity required")


def active_seat(db, seat_id, user):
    seat = db.scalar(
        select(Seat)
        .where(Seat.id == seat_id, Seat.tenant_id == user.tenant_id, Seat.beneficiary == user.beneficiary)
        .with_for_update()
    )
    if not seat:
        raise HTTPException(404, "Seat outside beneficiary scope")
    if not seat.active:
        raise HTTPException(403, "Workspace access requires an active seat")
    return seat


@app.get("/workspace/{seat_id}", response_model=Workspace, response_model_exclude_unset=True)
def workspace(seat_id: Identifier, user=Depends(workspace_user)):
    with transaction(settings().seller_db) as db:
        seat = active_seat(db, seat_id, user)
        entries = db.scalars(
            select(WorkspaceEntry)
            .where(WorkspaceEntry.seat_id == seat.id)
            .order_by(WorkspaceEntry.created_at.desc(), WorkspaceEntry.id.desc()).limit(50)
        ).all()
        event(db, user.tenant_id, "WORKSPACE_ACCESSED", seat.id, {"beneficiary": user.beneficiary})
        return {
            "workspace": "Named seat notebook",
            "seat_id": seat.id,
            "beneficiary": seat.beneficiary,
            "entries": [{"id": x.id, "text": x.text} for x in entries],
        }


@app.post("/workspace/{seat_id}", response_model=WorkspaceWrite, response_model_exclude_unset=True)
def write_workspace(seat_id: Identifier, note: WorkspaceNote, user=Depends(workspace_user)):
    with transaction(settings().seller_db) as db:
        seat = active_seat(db, seat_id, user)
        entry = WorkspaceEntry(id=uuid4().hex, seat_id=seat.id, text=note.text)
        db.add(entry)
        event(db, user.tenant_id, "WORKSPACE_ENTRY_CREATED", seat.id, {"entry_id": entry.id})
        return {"id": entry.id, "seat_id": seat.id, "text": entry.text}
