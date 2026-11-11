"""Authoritative route inventory and role matrix; test_pass6_matrix enforces it."""

ACTORS = {
    "requester": "requester-a",
    "approver": "approver-a",
    "purchase_issuer": "owner-a",
    "refund_issuer": "merchant-a",
    "broker_runtime": "broker-runtime",
    # There is no independent admin membership in the frozen model: existing
    # issuers are the reconciliation operators. This column tests that alias.
    "reconciliation_operator": "owner-a",
    "economic_worker": "economic-worker",
    "seller_integration": "seller-integration",
    "reference_test_actor": "reference-test-actor",
    "unauthenticated": None,
    "refund_requester": "refund-requester-a",
    "refund_approver": "refund-approver-a",
}
ISSUER = {"purchase_issuer", "reconciliation_operator"}
REQUESTER = {"requester"}
APPROVER = {"approver"}
PURCHASE = ISSUER | REQUESTER | APPROVER
REFUND = {"refund_issuer", "refund_requester", "refund_approver"}
INTERACTIVE = PURCHASE | REFUND

COMMANDS = {
    "create-grant": ISSUER,
    "activate-grant": ISSUER,
    "revoke-grant": ISSUER,
    "expire-grant": ISSUER,
    "revise-grant": ISSUER,
    "publish-policy": ISSUER,
    "propose": REQUESTER,
    "approve": APPROVER,
    "admit": REQUESTER,
}
RETURN_COMMANDS = {
    "create-grant": {"refund_issuer"},
    "propose": {"refund_requester"},
    "approve": {"refund_approver"},
    "admit": {"refund_requester"},
}
READS = {
    "/authority": INTERACTIVE,
    "/dispatch": PURCHASE,
    "/returns": REFUND,
    "/reconciliation/runs": ISSUER | {"refund_issuer"},
    "/reconciliation/findings": ISSUER | {"refund_issuer"},
}

# Classification is independent of permission. Signed seller and internal wire
# credentials are not interchangeable with interactive session identities.
ROUTES = {
    "api": {
        "PUBLIC READ": ["GET /health", "GET /ready"],
        "PUBLIC INPUT": ["POST /session", "POST /webhooks/razorpay", "POST /economic/webhooks/{channel_id}"],
        "AUTHENTICATED READ": [
            "GET /me",
            "GET /purchases",
            "GET /purchases/{purchase_id}",
            "GET /authority",
            "GET /authority/actions/{action_id}",
            "GET /authority/requests/{family}/{idempotency_key}",
            "GET /dispatch",
            "GET /dispatch/{operation_id}",
            "GET /dispatch/requests/{family}/{idempotency_key}",
            "GET /economic/{operation_id}",
            "GET /returns",
            "GET /reconciliation/runs",
            "GET /reconciliation/runs/{run_id}",
            "GET /reconciliation/findings",
            "GET /reconciliation/findings/{finding_id}",
        ],
        "AUTHENTICATED MUTATION": [
            "POST /purchases",
            "POST /purchases/{purchase_id}/prepare",
            "POST /purchases/{purchase_id}/checkout",
            "POST /purchases/{purchase_id}/callback",
            "POST /purchases/{purchase_id}/dismiss",
            "POST /purchases/{purchase_id}/refresh",
            "POST /purchases/{purchase_id}/refund-read",
            "POST /authority/commands/{family}",
            "POST /authority/quotes",
            "POST /dispatch/disclose",
            "POST /dispatch/observe",
            "POST /returns/lock",
            "POST /returns/close",
            "POST /returns/observe",
            "POST /returns/commands/{family}",
            "POST /reconciliation/runs",
        ],
        "TEST-ONLY": ["POST /purchases/{purchase_id}/synthetic-capture", "POST /refund-probes"],
    },
    "broker": {
        "INTERNAL SERVICE": [
            "GET /health",
            "GET /ready",
            "POST /quote/{tenant_id}",
            "POST /authority-quote/{tenant_id}",
            "POST /prepare",
            "POST /checkout",
            "POST /callback",
            "POST /refresh",
            "POST /fulfill",
            "POST /refund-read",
        ],
        "BROKER-ONLY": [
            "POST /dispatch/handoff",
            "POST /dispatch/observe",
            "POST /economic/canonical-read",
            "POST /returns/handoff",
            "POST /returns/observe",
        ],
        "TEST-ONLY": ["POST /synthetic-capture", "POST /refund-probe"],
    },
    "seller": {
        "PUBLIC READ": ["GET /health", "GET /ready"],
        "SELLER-ONLY": [
            "POST /tickets",
            "POST /truth/finalize",
            "POST /truth/reconciliation-read",
            "POST /truth/return-lock",
            "POST /truth/return-close",
        ],
        "AUTHENTICATED READ": ["GET /workspace/{seat_id}", "GET /truth/workspace/{seat_id}"],
        "AUTHENTICATED MUTATION": ["POST /workspace/{seat_id}", "POST /truth/workspace/{seat_id}"],
    },
    "reference": {
        "INTERNAL SERVICE": [
            "GET /health",
            "GET /ready",
            "POST /dispatch/orders",
            "GET /dispatch/orders",
            "POST /orders",
            "GET /orders",
            "GET /orders/{order_id}",
            "GET /payments/{payment_id}",
            "GET /orders/{order_id}/payments",
            "POST /payments/{payment_id}/refund",
            "GET /refunds/{refund_id}",
            "POST /returns/refunds",
            "GET /returns/refunds",
            "GET /payments/{payment_id}/refunds",
        ],
        "TEST-ONLY": [
            "POST /economic/orders/{order_id}/synthetic-capture",
            "GET /economic/orders/{order_id}/deliveries/{ordinal}",
            "POST /orders/{order_id}/synthetic-capture",
        ],
    },
}
