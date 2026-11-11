"""Read side for the analyst: existing scoped functions, PostgreSQL READ ONLY.

No owner credentials, provider client, mutations, arbitrary SQL or new grants.
"""

import json
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError


class InvestigationError(Exception):
    def __init__(self, code: str, status: int = 503):
        self.code, self.status = code, status
        self.evidence: dict = {}
        super().__init__(code)


def origin(value: str) -> str:
    try:
        p = urlsplit(value)
        if (p.username or p.password or p.query or p.fragment or p.path not in {"", "/"}
                or not p.hostname or (p.scheme != "https" and not (
                    p.scheme == "http" and p.hostname in {"localhost", "127.0.0.1", "::1"}))):
            raise ValueError()
        p.port
    except ValueError:
        raise InvestigationError("INVALID_INVESTIGATION_ORIGIN") from None
    return value.rstrip("/")


class ScopedReads:
    SQL = {
        "dispatch": "SELECT dispatch.inspect(:token,:opid)",
        "reconciliation": "SELECT reconciliation.inspect(:token,:kind,NULL,NULL,50)",
    }

    def __init__(self, database_url: str, api_origin: str):
        try:
            parsed = make_url(database_url)
            if parsed.drivername != "postgresql+psycopg" or parsed.username != "interlock_api":
                raise ValueError()
        except Exception:
            raise InvestigationError("INVESTIGATION_RUNTIME_ROLE_REQUIRED") from None
        self.engine = create_engine(database_url, hide_parameters=True, pool_size=2, max_overflow=0)
        self.api = origin(api_origin)
        self.http = httpx.Client(timeout=8, follow_redirects=False, trust_env=False,
                                 transport=httpx.HTTPTransport(retries=0))

    def close(self):
        self.http.close()
        self.engine.dispose()

    def sql(self, name: str, token: str, **params):
        try:
            with self.engine.connect() as db, db.begin():
                db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                db.execute(text("SET LOCAL statement_timeout='5s'"))
                if db.scalar(text("SELECT session_user")) != "interlock_api":
                    raise InvestigationError("INVESTIGATION_RUNTIME_ROLE_REQUIRED")
                return db.scalar(text(self.SQL[name]), {"token": token, **params})
        except DBAPIError as error:
            diagnostic = getattr(error.orig, "diag", None)
            detail = getattr(diagnostic, "message_detail", "")
            status = int(detail) if detail in {"401", "403", "429"} else 503
            raise InvestigationError("EVIDENCE_SCOPE_DENIED" if status == 403 else
                                     "EVIDENCE_AUTHENTICATION_REQUIRED" if status == 401 else
                                     "EVIDENCE_READ_UNAVAILABLE", status) from None

    def get(self, token: str, path: str):
        # Called only with fixed, validated paths built in this module.
        try:
            r = self.http.get(self.api + "/v1/paypal/" + path, headers={"Authorization": "Bearer " + token})
            if r.status_code in {401, 403}:
                raise InvestigationError("EVIDENCE_SCOPE_DENIED", r.status_code)
            if r.status_code != 200 or len(r.content) > 1_000_000:
                raise InvestigationError("EVIDENCE_READ_UNAVAILABLE")
            return r.json()
        except (httpx.HTTPError, ValueError):
            raise InvestigationError("EVIDENCE_READ_UNAVAILABLE") from None

    def identity(self, token: str):
        data = self.get(token, "authority")
        return {k: data[k] for k in ("principal", "tenant_id", "roles")}

    def transactions(self, token: str):
        data = self.sql("dispatch", token, opid=None)
        rows = []
        for item in data["operations"]:
            o = item["operation"]
            if o["provider_mode"] == "PAYPAL_SANDBOX" and o["currency"] == "USD":
                rows.append({"operation_id": o["id"], "amount_minor": o["amount"], "currency": o["currency"],
                             "merchant_id": o["account_id"], "created_at": o["created_at"]})
        return {"tenant_id": data["tenant_id"], "principal": data["principal"],
                "transactions": rows[-25:], "limited": len(rows) > 25}

    def proof(self, token: str, opid: str):
        from interlock.paypal_contracts import Proof

        p = Proof.model_validate(self.get(token, "evidence/" + opid)).model_dump(mode="json")
        if p["operation_id"] != opid or p["currency"] != "USD":
            raise InvestigationError("EVIDENCE_IDENTITY_MISMATCH")
        return p

    def authority(self, token: str, opid: str, proof: dict):
        snapshot = self.get(token, "authority")
        action = self.get(token, "actions/" + proof["action_id"])
        dispatch = self.sql("dispatch", token, opid=opid)
        if len(dispatch["operations"]) != 1:
            raise InvestigationError("EVIDENCE_SCOPE_DENIED", 403)
        item = dispatch["operations"][0]
        # No transport bodies, signatures, capabilities or arbitrary model destinations.
        return {"action": action,
                "monetary_units": "All action/reservation/grant-head monetary integers are USD MINOR units (100 = USD 1.00), not dollars.",
                "bound_approval_id": proof["approval_id"],
                "approval_record_visible": any(a["id"] == proof["approval_id"] for a in snapshot["approvals"]),
                "approval": [a for a in snapshot["approvals"] if a["id"] == proof["approval_id"]],
                "grant_head": {k: item["grant_head"][k] for k in ("status", "bound", "consumed", "reserved", "free")},
                "reservation": {k: item["reservation"][k] for k in ("id", "amount", "currency", "created_at")},
                "original_terms_expired": not action["fresh"],
                "note": "Approval/admission are historical authority, not current permission to capture. A bound approval whose details are not visible is permission-limited evidence, not proof that approval is missing. Reserved lifetime capacity is not free budget."}

    def reconciliation(self, token: str, opid: str, proof: dict):
        stored = {}
        try:
            runs = self.sql("reconciliation", token, kind="runs")
            findings = self.sql("reconciliation", token, kind="findings")
            stored = {"available": True,
                      "runs": [{**{k: r[k] for k in ("id", "state", "completed_at")},
                                "members": [m for m in r["members"] if m["operation_id"] == opid]}
                               for r in runs if any(m["operation_id"] == opid for m in r["members"])],
                      "findings": [{k: f[k] for k in ("id", "operation_id", "class", "state", "next_step", "last_seen_at")}
                                   for f in findings if f["operation_id"] == opid],
                      "bounded_inventory": True}
        except InvestigationError as error:
            stored = {"available": False, "reason": error.code}
        journals = proof["journals"]
        balanced = all(sum(line["amount_minor"] for line in j["lines"] if line["side"] == "DEBIT") ==
                       sum(line["amount_minor"] for line in j["lines"] if line["side"] == "CREDIT") for j in journals)
        gaps = []
        if proof["blocked"]:
            gaps.append("CONFLICT_HOLD")
        if not proof["capture_confirmed"]:
            gaps.append("CAPTURE_NOT_VERIFIED")
        if not journals:
            gaps.append("NO_JOURNAL")
        if not balanced:
            gaps.append("UNBALANCED_JOURNAL")
        if not proof["seller_fulfilled"]:
            gaps.append("FULFILLMENT_NOT_RECORDED")
        return {"mode": "READ_ONLY_COMPARISON_OF_RECORDED_EVIDENCE", "evaluated_at": datetime.now(UTC).isoformat(),
                "gaps": gaps, "journals_balanced": balanced if journals else None,
                "stored_reconciliation": stored, "provider_contacted": False,
                "database_writes": False, "current_provider_state_established": False}


def digest(value: object) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
