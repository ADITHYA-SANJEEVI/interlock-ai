"""Finite Playwright fixture: actual API/SQL, SYNTHETIC model/provider only.

Never deploy this module. Test controls are loopback-only and require explicit
disposable acceptance configuration. No PayPal/model credentials are loaded.
"""

import json
import os

from sqlalchemy.engine import make_url

from tools.hackathon.disposable_postgres import DATABASES, environment


def main():
    if os.environ.get("PAYPAL_SQL_BROWSER_ACCEPTANCE") != "SYNTHETIC_ON_VERIFIED_DISPOSABLE_POSTGRES":
        raise RuntimeError("EXPLICIT_SYNTHETIC_SQL_BROWSER_ACCEPTANCE_REQUIRED")
    env = environment()
    if os.environ.get("PAYPAL_BROWSER_LEGACY_ROLE_PASSWORD_ACTIVE") == "true":
        for role in ("API", "BROKER", "ECONOMIC", "SELLER"):
            name = "PAYPAL_TEST_" + role + "_URL"
            env[name] = make_url(env[name]).set(password="local_only").render_as_string(hide_password=False)
    os.environ.update(env)
    from paypal_acceptance.harness import FIXTURE_CAPABILITY, FIXTURE_QUOTE_REQUEST, FIXTURE_SESSION, Harness

    h = Harness().prepare(approve=False, admit=False)
    password = "synthetic-sql-browser-password"
    unused_reference = make_url(h.urls["api"]).set(username="interlock_reference",
        password="UNUSED_REFERENCE_FIXTURE_PASSWORD", database=DATABASES["reference"]).render_as_string(hide_password=False)
    os.environ.update(CONTROL_DATABASE_URL=h.urls["api"], SELLER_DATABASE_URL=h.urls["seller"],
                      REFERENCE_DATABASE_URL=unused_reference, BROKER_URL="http://127.0.0.1:3117",
                      SELLER_URL="http://127.0.0.1:3117", REFERENCE_URL="http://127.0.0.1:3117", SESSION_KEY=FIXTURE_SESSION,
                      PAYPAL_SELLER_DATABASE_URL=h.urls["seller"],
                      INTERNAL_TOKEN="synthetic-browser-internal-token", RAZORPAY_WEBHOOK_SECRET="synthetic-unused-webhook")
    from interlock.config import settings
    from interlock.contracts import signature
    from interlock.security import password_hash

    settings.cache_clear()
    for person in h.people.values():
        h.sql("UPDATE public.identities SET password_hash=:hash WHERE username=:user RETURNING username",
              {"hash": password_hash(password, person), "user": person})

    from interlock.api import app as session_app
    from interlock.paypal_reconciliation import execute
    from interlock.paypal_services import broker_app, create_api, local_caller, seller_factory
    from interlock.proposer import Proposer

    class SyntheticProposer(Proposer):
        def __init__(self):
            pass

        def propose(self, intent, beneficiaries):
            return {"financial_authority": False, "extraction": {"status": "PROPOSED", "reason": "SUPPORTED_PURCHASE",
                "proposal": {"product_id": "workspace-seat", "beneficiary": beneficiaries[0], "quantity": 1,
                             "spending_limit_minor": 100, "currency": "USD"}},
                    "provenance": {"actual_model_invocation": False, "model": "SYNTHETIC_SQL_BROWSER_TEST",
                                   "classification": "SYNTHETIC_SQL_BROWSER_TEST"}}

    # Exercise the ACTUAL HTTP caller, broker and seller contracts over loopback.
    # Apps share this test process only; this is not a process-isolation claim.
    seller = local_caller("http://127.0.0.1:3117", "/v1/paypal/seller/")
    internal_token = "synthetic-sql-browser-internal-token-never-deploy"
    broker = local_caller("http://127.0.0.1:3117", "/v1/internal/", internal_token)
    app = create_api(h.api, SyntheticProposer(), broker,
                     lambda token, body: execute(h.api, seller, token, body))
    for service in (broker_app(h.driver, internal_token, seller, FIXTURE_QUOTE_REQUEST), seller_factory()):
        app.router.routes.extend(service.routes)
    # Reuse the ACTUAL unchanged session/me endpoints, without exposing legacy
    # financial routes or substituting fake authentication tokens.
    for route in session_app.routes:
        if getattr(route, "path", None) in {"/session", "/me"}:
            app.router.routes.append(route)

    @app.get("/__fixture")
    def metadata():
        return {"classification": "ACTUAL_SQL_SYNTHETIC_MODEL_PROVIDER", "people": h.people,
                "password": password, "grant_id": h.grant["grant_id"],
                "creates": h.provider.creates, "captures": h.provider.captures}

    @app.post("/__fixture/consent")
    def synthetic_consent():
        h.provider.status = "APPROVED"
        return {"classification": "SYNTHETIC_BUYER_CONSENT"}

    @app.post("/__fixture/fulfill")
    def synthetic_worker(body: dict):
        op = body["operation_id"]
        rid = h.sql("SELECT latest_receipt_id FROM paypal_v1.runtime WHERE operation_id=:id", {"id": op})
        h.economic("process_receipt", {"rid": str(rid)})
        cap = h.economic("capability", {"opid": op})
        assert signature(FIXTURE_CAPABILITY, cap["raw"].encode()) == cap["signature"]
        att = seller("finalize", cap)
        rid = h.economic("receive", {"channel": "seller-" + h.tenant, "raw": att["raw"].encode(),
            "headers": json.dumps({"signature": att["signature"]}), "delivery": "synthetic-browser-fulfillment"})
        h.economic("process_receipt", {"rid": str(rid)})
        return {"classification": "SYNTHETIC_PROVIDER_ACTUAL_SQL_WORKER_AND_SELLER"}

    import uvicorn
    try:
        uvicorn.run(app, host="127.0.0.1", port=3117, log_level="warning")
    finally:
        h.close()


if __name__ == "__main__":
    main()
