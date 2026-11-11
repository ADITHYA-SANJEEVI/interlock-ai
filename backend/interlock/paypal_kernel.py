"""Fixed SQL interface to the opt-in PayPal extension; no default DB destination."""

import json
import os
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from interlock.db import transaction
from interlock.paypal_flow import BoundaryFailure
from interlock.reconciliation import STATEMENTS as RECONCILIATION

STATEMENTS = {
    "binding": "SELECT paypal_v1.binding(:secret,:opid)",
    "commit_phase": "SELECT paypal_v1.commit_phase(:secret,:opid,:phase,:expected_digest)",
    "order_hint": "SELECT paypal_v1.order_hint(:secret,:opid,:orderid)",
    "record_read": "SELECT paypal_v1.record_read(:secret,:opid,:raw,:sig,:delivery)",
    "note": "SELECT paypal_v1.note(:secret,:opid,:code,CAST(:evidence AS jsonb))",
    "scope": "SELECT paypal_v1.proposal_scope(:token,:grantid)",
    "proposal": "SELECT paypal_v1.proposal(:token,CAST(:pid AS uuid))",
    "recover_proposal": "SELECT paypal_v1.recover_proposal(:token,:grantid,:idem,:intent_hash)",
    "record_proposal": "SELECT paypal_v1.record_proposal(:token,:grantid,:idem,:intent_hash,CAST(:result AS jsonb))",
    "quote": "SELECT authority.paypal_quote_v1(:token,:raw,:signature,:idem)",
    "link_quote": "SELECT paypal_v1.link_quote(:token,CAST(:pid AS uuid),:quoteid)",
    "command": "SELECT paypal_v1.command(:token,:family,CAST(:body AS jsonb),:expected,:idem)",
    "user": "SELECT to_jsonb(dispatch.user_operation(:token,:opid,:verb))",
    "consent": "SELECT paypal_v1.consent(:token,:opid,:expected,:idem)",
    "capture_request": "SELECT paypal_v1.capture_request(:token,:opid,:expected,:idem)",
    "inspect": "SELECT paypal_v1.inspect(:token,:opid)||jsonb_build_object('cancellation',paypal_v1.cancellation(:token,:opid))",
    "cancel_unsent": "SELECT paypal_v1.cancel_never_sent(:token,:opid,:expected,:dispatch_expected,:idem)",
    "read_cancellation": "SELECT paypal_v1.cancellation(:token,:opid)",
    "admit_replacement": "SELECT paypal_v1.admit_replacement(:token,:parentid,CAST(:body AS jsonb),:expected,:idem)",
    "snapshot": "SELECT paypal_v1.snapshot(:token)",
    "action": "SELECT paypal_v1.action_detail(:token,:actionid)",
    "process_receipt": "SELECT paypal_v1.process_receipt(CAST(:rid AS uuid))",
    "pending": "SELECT paypal_v1.pending()",
    "capability": "SELECT economic.capability(:opid)",
    "receive": "SELECT economic.receive(:channel,:raw,CAST(:headers AS jsonb),:delivery)",
    "post": "SELECT paypal_v1.post_fact(CAST(:fid AS uuid))",
}
STATEMENTS.update({"reconcile_" + name: statement for name, statement in RECONCILIATION.items()})
STATEMENTS.update({
    "continuation_seed": "SELECT paypal_v2.seed(:token,:opid,CAST(:cid AS uuid),:expected)",
    "continuation_register": "SELECT paypal_v2.register(:token,:opid,CAST(:cid AS uuid),:raw,:sig,:expected,:idem)",
    "continuation_approve": "SELECT paypal_v2.approve(:token,:opid,CAST(:cid AS uuid),:digest,CAST(:expires AS timestamptz),:expected,:idem)",
    "continuation_capture_request": "SELECT paypal_v2.request_capture(:token,:opid,CAST(:cid AS uuid),:expected,:idem)",
    "continuation_commit": "SELECT paypal_v2.commit_capture(:secret,:opid,CAST(:cid AS uuid),:expected_digest)",
    "continuation_inspect": "SELECT paypal_v2.inspect(:token,CAST(:cid AS uuid))",
    "continuation_revoke": "SELECT paypal_v2.revoke(:token,:opid,CAST(:cid AS uuid))",
})
PUBLIC_CODES = frozenset({
    "CANCELLATION_BINDING_MISMATCH", "CANCELLATION_ISSUER_REQUIRED", "INVALID_CANCELLATION_COMMAND",
    "CANCELLATION_ALREADY_RECORDED", "CANCELLATION_RESERVATION_MISMATCH", "NEVER_SENT_PROOF_UNAVAILABLE",
    "ACTIVE_CLAIM_PREVENTS_CANCELLATION", "INCOMPLETE_UNSENT_CANCELLATION_BUNDLE", "REPLACEMENT_REQUESTER_REQUIRED",
    "INVALID_REPLACEMENT_COMMAND", "CANCELLED_PARENT_REQUIRED", "REPLACEMENT_ALREADY_ADMITTED",
    "REPLACEMENT_EXACT_TERMS_REQUIRED", "REPLACEMENT_ACTION_ALREADY_ADMITTED", "EXACT_APPROVAL_AMBIGUOUS",
    "INSUFFICIENT_AUTHORITY", "POLICY_AMOUNT_DENIED", "GRANT_SCOPE_MISMATCH", "APPROVAL_INVALID",
    "SELF_APPROVAL_DENIED", "AUTHORITY_REVOKED", "AUTHORITY_EXPIRED", "AUTHORITY_INACTIVE",
    "POLICY_CHANGED", "REQUESTER_CHANGED", "REVISION_CONFLICT", "IDEMPOTENCY_CONFLICT", "ACTION_STALE",
    "EVIDENCE_STALE", "SCOPE_DENIED", "AUTHENTICATION_REQUIRED", "RESOURCE_LIMIT_EXCEEDED",
    "USD_TENANT_REQUIRED", "USD_PROPOSAL_SCOPE_DENIED", "USD_ACTION_NOT_VISIBLE", "PROPOSAL_NOT_VISIBLE",
    "SIGNED_QUOTE_EXCEEDS_PROPOSAL_OR_SCOPE", "MODEL_PROPOSAL_SIGNED_QUOTE_LINK_REQUIRED",
    "UNTRUSTED_PROPOSAL_REJECTED", "APPROVAL_REQUIRED",
    "PAYPAL_ALWAYS_REQUIRES_INDEPENDENT_APPROVAL", "PAYPAL_DISPOSABLE_ACCEPTANCE_REQUIRED",
    "PAYPAL_DISABLED_OR_BINDING_CONFLICT", "CAPTURE_COMMAND_CONFLICT", "FRESH_APPROVED_EXACT_ORDER_REQUIRED",
    "SEPARATE_EXACT_CAPTURE_AUTHORIZATION_REQUIRED", "BUYER_OWNER_CHANGED", "BUYER_OWNER_ROLE_REQUIRED",
    "CONSENT_NOT_AVAILABLE", "GRANT_EPOCH_CHANGED", "QUOTE_EXPIRED", "TENANT_MUTATIONS_PAUSED",
    "CONTINUATION_DISABLED", "CONTINUATION_SCOPE_DENIED", "CONTINUATION_POLICY_DENIED",
    "CONTINUATION_APPROVED_EXPOSED_ORDER_REQUIRED", "CONTINUATION_SELLER_SIGNATURE_INVALID",
    "CONTINUATION_ATTESTATION_INVALID", "CONTINUATION_REQUESTER_REQUIRED", "CONTINUATION_FRESH_ORDER_REQUIRED",
    "CONTINUATION_EXACT_BINDING_REQUIRED", "CONTINUATION_NOT_FOUND", "CONTINUATION_REVOKED",
    "CONTINUATION_STALE_OR_CONFLICT", "CONTINUATION_APPROVAL_REQUIRED", "CONTINUATION_APPROVER_CHANGED",
    "CONTINUATION_EXACT_APPROVAL_REQUIRED", "CONTINUATION_ALREADY_APPROVED", "CONTINUATION_CAPTURE_CONFLICT",
    "CONTINUATION_ORIGINAL_QUOTE_NOT_EXPIRED",
})
ENABLED_COMMANDS = frozenset({"command", "quote", "link_quote", "record_proposal", "consent", "capture_request", "commit_phase",
                              "cancel_unsent", "admit_replacement"})


class PostgreSQLKernel:
    def __init__(self, url: str, role: str):
        parsed = urlsplit(url)
        if (parsed.scheme != "postgresql+psycopg" or parsed.username != role or not parsed.hostname
                or role not in {"interlock_api", "interlock_broker", "interlock_economic"}):
            raise BoundaryFailure("EXPLICIT_PAYPAL_RUNTIME_DATABASE_REQUIRED")
        self._url, self._role = url, role

    @classmethod
    def configured(cls, service: str):
        role = {"api": "interlock_api", "broker": "interlock_broker", "economic": "interlock_economic"}[service]
        return cls(os.environ.get("PAYPAL_" + service.upper() + "_DATABASE_URL", ""), role)

    def __call__(self, command: str, parameters: dict[str, Any]) -> Any:
        statement = STATEMENTS[command]  # Names are internal constants, never HTTP route fragments.
        try:
            with transaction(self._url) as db:
                if command in {"inspect", "snapshot", "action", "reconcile_evaluate"}:
                    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                if db.scalar(text("SELECT session_user")) != self._role:
                    raise BoundaryFailure("PAYPAL_RUNTIME_ROLE_MISMATCH")
                if command in ENABLED_COMMANDS:
                    db.execute(text("SELECT paypal_v1.assert_enabled()"))
                approval_scope = None
                if command == "command" and parameters.get("family") in {"approve", "admit"}:
                    # Public scoped reads only; no new private-table privileges.
                    # Cross-process/session serialization precedes the unchanged
                    # kernel's identity/domain locks. Hash collisions add contention.
                    body = json.loads(parameters["body"])
                    approval_scope = db.scalar(text(STATEMENTS["snapshot"]), {"token": parameters["token"]})
                    lock_key = json.dumps(["paypal-authority-action-v1", approval_scope["tenant_id"], body.get("action_id")])
                    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": lock_key})
                result = db.scalar(text(statement), parameters)
                if approval_scope is not None:
                    # The kernel first validates exact terms, roles, expiry and
                    # same-key recovery. Any newly inserted duplicate and its
                    # request/audit rows roll back together on this exception.
                    snapshot = db.scalar(text(STATEMENTS["snapshot"]), {"token": parameters["token"]})
                    matches = [ap for ap in snapshot["approvals"]
                               if ap["action_id"] == body.get("action_id")
                               and ap["evidence_id"] == body.get("evidence_id")
                               and ap["decision_id"] == body.get("decision_id")
                               and ap["effective_status"] == "AVAILABLE"]
                    if parameters["family"] == "approve" and sum(
                            ap["approver"] == approval_scope["principal"] for ap in matches) > 1:
                        raise BoundaryFailure("EXACT_APPROVAL_ALREADY_RECORDED", 409)
                    # Admission has consumed the explicitly selected approval.
                    # Any other available exact approval makes selection ambiguous;
                    # roll back reservation, operation, consumption and audit rows.
                    if parameters["family"] == "admit" and matches:
                        raise BoundaryFailure("EXACT_APPROVAL_AMBIGUOUS", 409)
                return result
        except DBAPIError as error:
            diagnostic = getattr(error.orig, "diag", None)
            code = getattr(diagnostic, "message_primary", None)
            detail = getattr(diagnostic, "message_detail", None)
            if getattr(error.orig, "sqlstate", None) == "P0001" and code in PUBLIC_CODES:
                status = int(detail) if detail in {"401", "403", "409", "422", "429", "503"} else 409
                raise BoundaryFailure(code, status) from None
            # Unknown transaction outcome is not permission to retry a mutation.
            raise BoundaryFailure("PAYPAL_DATABASE_BOUNDARY_REJECTED_OR_UNAVAILABLE") from None
