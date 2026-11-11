"""Executable actor matrix against protected PostgreSQL functions, never role mocks."""

import pytest
from fastapi.routing import APIRoute
from interlock import api, broker, reference, seller
from interlock.security import issue_session
from pass6_matrix import ACTORS, COMMANDS, READS, RETURN_COMMANDS, ROUTES
from test_authority import auth as auth
from test_authority import deadline
from test_dispatch import d as d
from test_economic import truth as truth
from test_returns import refund as refund


@pytest.fixture(autouse=True)
def isolated_refund_approver(auth):
    auth.sql("""INSERT INTO public.identities(username,tenant_id,beneficiary,role,password_hash)
        VALUES('refund-approver-a','tenant-a','merchant','refund','test-only-unused-password-hash')""")
    auth.sql("""INSERT INTO authority.memberships(principal,tenant_id,roles)
        VALUES('refund-approver-a','tenant-a',ARRAY['REFUND_APPROVER'])""")


def narrow_combined_owner(auth, actor):
    if actor in {"purchase_issuer", "reconciliation_operator"}:
        auth.sql("UPDATE authority.memberships SET roles=ARRAY['PURCHASE_ISSUER'] WHERE principal='owner-a'")
    elif actor == "refund_issuer":
        auth.sql("UPDATE authority.memberships SET roles=ARRAY['REFUND_ISSUER'] WHERE principal='merchant-a'")


def identity(actor):
    principal = ACTORS[actor]
    return {} if principal is None else {"Authorization": "Bearer " + issue_session(principal)}


def test_route_inventory_requires_matrix_review():
    for service, app in (
        ("api", api.app),
        ("broker", broker.app),
        ("seller", seller.app),
        ("reference", reference.app),
    ):
        actual = {
            method + " " + route.path
            for route in app.routes
            if isinstance(route, APIRoute)
            for method in route.methods
        }
        expected = [path for group in ROUTES[service].values() for path in group]
        assert len(expected) == len(set(expected))
        assert actual == set(expected), service


@pytest.mark.parametrize("actor", ACTORS)
def test_read_role_matrix(auth, actor):
    for path, allowed in READS.items():
        response = auth.clients["api"].get(path, headers=identity(actor))
        assert response.status_code == (
            200
            if actor in allowed
            else 401
            if ACTORS[actor] is None
            else 403
        ), (actor, path, response.text)


@pytest.mark.parametrize("actor", ACTORS)
@pytest.mark.parametrize("family", COMMANDS)
def test_purchase_command_role_matrix(auth, actor, family):
    auth.policy(approval_above=0 if family == "approve" else 1000000)
    grant = auth.grant()
    action = auth.action(grant)
    bodies = {
        "create-grant": {
            "kind": "PURCHASE",
            "subject": "requester-a",
            "lifetime_bound": 1000,
            "beneficiaries": ["alice"],
            "expires_at": deadline(),
        },
        "activate-grant": {"grant_id": grant["grant_id"]},
        "revoke-grant": {"grant_id": grant["grant_id"]},
        "expire-grant": {"grant_id": grant["grant_id"]},
        "revise-grant": {"grant_id": grant["grant_id"], "beneficiaries": ["alice"], "expires_at": deadline()},
        "publish-policy": {"kind": "PURCHASE", "approval_above": 0, "max_amount": 1000000},
        "propose": {"grant_id": grant["grant_id"], "quote_id": auth.quote()["id"]},
        "approve": {**auth.exact(action), "expires_at": deadline()},
        "admit": {**auth.exact(action), "approval_id": None},
    }
    revision = (
        2
        if family in {"activate-grant", "revoke-grant", "revise-grant", "expire-grant"}
        else 2
        if family == "publish-policy"
        else 1
        if family in {"approve", "admit"}
        else 0
    )
    narrow_combined_owner(auth, actor)
    response = auth.clients["api"].post(
        "/authority/commands/" + family,
        json=bodies[family],
        headers={**identity(actor), "If-Match": str(revision), "Idempotency-Key": "pass6_matrix_" + actor},
    )
    if actor in COMMANDS[family]:
        # Already active / not yet expired are intentional state conflicts after
        # successful issuer authorization, rather than permission denials.
        assert response.status_code == (409 if family in {"activate-grant", "expire-grant"} else 200), (
            response.text
        )
    else:
        assert response.status_code in {401, 403}, (actor, family, response.text)


@pytest.mark.parametrize("actor", ACTORS)
def test_refund_command_role_matrix(refund, actor):
    h = refund
    h.prepare(approval=True)
    action = h.propose()
    narrow_combined_owner(h.d.auth, actor)
    for family, body, revision in (
        (
            "create-grant",
            {
                "purchase_operation": h.truth.op["id"],
                "subject": "refund-requester-a",
                "limit": 3000,
                "expires_at": deadline(),
                "beneficiaries": ["alice"],
            },
            0,
        ),
        ("propose", {"lock_id": h.lock, "grant_id": h.grant["grant_id"], "amount": 1000}, 0),
        ("approve", {**h.exact(action), "expires_at": deadline()}, 1),
    ):
        response = h.api.post(
            "/returns/commands/" + family,
            json=body,
            headers={
                **identity(actor),
                "If-Match": str(revision),
                "Idempotency-Key": "pass6_refund_" + family + "_" + actor,
            },
        )
        assert response.status_code == (
            200
            if actor in RETURN_COMMANDS[family]
            else 401
            if ACTORS[actor] is None
            else 403
        ), (actor, family, response.text)
    approved = h.ok("approve", {**h.exact(action), "expires_at": deadline()}, "refund-approver-a", revision=1)
    response = h.api.post(
        "/returns/commands/admit",
        json={**h.exact(action), "approval_id": approved["approval_id"]},
        headers={**identity(actor), "If-Match": "1", "Idempotency-Key": "pass6_admit_" + actor},
    )
    assert response.status_code == (
        200
        if actor in RETURN_COMMANDS["admit"]
        else 401
        if ACTORS[actor] is None
        else 403
    ), (actor, "admit", response.text)


@pytest.mark.parametrize("actor", ACTORS)
def test_internal_wire_is_not_interactive_identity(clients, actor):
    import re

    for target in ("broker", "reference"):
        for routes in ROUTES[target].values():
            for route in routes:
                method, path = route.split(" ")
                path = re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000001", path)
                response = clients[target].request(method, path, json={}, headers=identity(actor))
                assert response.status_code == 401, (actor, target, path, response.text)
