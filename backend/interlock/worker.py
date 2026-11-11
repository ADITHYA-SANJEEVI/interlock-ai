"""Pass 0 trusted fulfillment worker. No PSP credentials or financial ledger."""

import logging
import os
import time

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from interlock.config import settings
from interlock.db import transaction
from interlock.dispatch import kernel
from interlock.models import Purchase, PurchaseState
from interlock.security import internal_headers

log = logging.getLogger("interlock.worker")


def once():
    with transaction(settings().control_db) as db:
        rows = db.execute(
            select(Purchase.id, Purchase.tenant_id)
            .join(PurchaseState)
            .where(
                PurchaseState.canonical_status == "CAPTURE_VERIFIED",
                PurchaseState.entitlement != "ACTIVE",
                PurchaseState.conflict.is_(False),
            )
            .limit(10)
        ).all()
    for pid, tenant in rows:
        try:
            with httpx.Client(timeout=20) as client:
                response = client.post(
                    settings().broker_url + "/fulfill",
                    json={"tenant_id": tenant, "purchase_id": pid},
                    headers=internal_headers(),
                )
            if response.status_code != 200:
                log.warning("Seller fulfillment pending HTTP %s", response.status_code)
        except httpx.HTTPError:
            log.warning("Seller fulfillment transport unavailable; exact seller key retained")
    return len(rows)


def dispatch_once():
    secret = os.getenv("DISPATCH_CLAIM_TOKEN", "")
    if not secret:
        return 0
    claim = kernel("claim", {"secret": secret, "lease": 30, "opid": None})
    if not claim:
        return 0
    # The queue transaction ended. The broker independently checks current mutation authority.
    path = "/dispatch/handoff" if claim["purpose"] == "SEND" else "/dispatch/observe"
    body = {"operation_id": claim["operation_id"]}
    if claim["purpose"] == "SEND":
        body["claim_id"] = claim["claim_id"]
    try:
        with httpx.Client(
            timeout=20, follow_redirects=False, transport=httpx.HTTPTransport(retries=0)
        ) as client:
            response = client.post(settings().broker_url + path, json=body, headers=internal_headers())
        if response.status_code >= 300:
            log.warning("Dispatch cutoff/observation denied HTTP %s", response.status_code)
    except httpx.HTTPError:
        log.warning("Dispatch response unavailable; only durable queue identity retained")
    return 1


def refund_once():
    from interlock.returns import kernel as return_kernel

    secret = os.getenv("DISPATCH_CLAIM_TOKEN", "")
    if not secret:
        return 0
    claim = return_kernel("claim", {"secret": secret, "operation": None})
    if not claim:
        return 0
    try:
        with httpx.Client(timeout=20, follow_redirects=False, transport=httpx.HTTPTransport(retries=0)) as client:
            client.post(settings().broker_url + "/returns/handoff", json=claim, headers=internal_headers())
    except httpx.HTTPError:
        log.warning("Refund response unavailable; original commitment and duty retained")
    return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            once()
            dispatch_once()
            refund_once()
        except (SQLAlchemyError, HTTPException):
            log.warning("Local kernel unavailable; durable claims and commitments retained")
        time.sleep(3)
