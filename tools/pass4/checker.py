"""Independent, standard-library-only verifier. Imports no production transitions."""

import base64
import hashlib
import json


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def verify(pack):
    t = pack["returns"]

    def index(name):
        result = {x["id"]: x for x in t[name]}
        require(len(result) == len(t[name]), "duplicate source identity " + name)
        return result

    locks, obligations, ops, facts = (index(name) for name in ("locks", "obligations", "operations", "facts"))
    captures = {x["id"]: x for x in pack["purchase_facts"] if x["kind"] == "CAPTURE"}
    purchase_observations = {x["id"]: x for x in pack["purchase_observations"]}
    actions = {x["id"]: x for x in pack["actions"]}
    evidence = {x["id"]: x for x in pack["evidence"]}
    grants = {x["id"]: x for x in pack["grants"]}
    reservations = {x["id"]: x for x in pack["reservations"]}
    reservation_heads = {x["reservation_id"]: x for x in pack["reservation_heads"]}
    for reservation in reservations.values():
        source = next((e for e in evidence.values() if e["action_id"] == reservation["action_id"]), None)
        if source and source.get("return_lock_id"):
            require(
                sum(b["reservation_id"] == reservation["id"] for b in obligations.values()) == 1,
                "merchant return reservation requires complete commercial bundle",
            )
    observations = index("observations")
    receipts = {x["id"]: x for x in pack["receipts"]}
    channels = {x["id"]: x for x in pack["channels"]}
    states = {x["receipt_id"]: x for x in pack["receipt_states"]}
    seller_locks = {x["id"]: x for x in pack["seller_locks"]}
    seller_orders = {x["id"]: x for x in pack["seller_orders"]}
    entitlements = {x["id"]: x for x in pack["entitlements"]}
    for row in pack["receipts"]:
        raw = base64.b64decode(row["body_b64"])
        require(hashlib.sha256(raw).hexdigest() == row["body_hash"], "raw envelope hash")
    for lid, lock in locks.items():
        require(lid in seller_locks, "missing durable seller lock")
        source = seller_locks[lid]
        require(source["raw"] == lock["raw"], "seller lock provenance")
        v = json.loads(lock["raw"])
        require(v["kind"] == "RETURN_LOCKED" and v["lock_id"] == lid, "seller lock identity")
        require(lock["capture_fact_id"] in captures, "matched capture required")
        cap = captures[lock["capture_fact_id"]]
        revenue = next(
            (
                f
                for f in pack["purchase_facts"]
                if f["kind"] == "REVENUE" and f["operation_id"] == cap["operation_id"]
            ),
            None,
        )
        require(
            revenue is not None
            and revenue["amount"] == cap["amount"]
            and revenue["currency"] == cap["currency"],
            "original gross revenue source",
        )
        posting = next((p for p in pack["purchase_postings"] if p["fact_id"] == revenue["id"]), None)
        require(
            posting is not None and posting["template"] == "FULFILLMENT_TO_REVENUE",
            "original gross revenue must be booked",
        )
        original_lines = sorted(
            [x for x in pack["purchase_lines"] if x["posting_id"] == posting["id"]],
            key=lambda x: x["ordinal"],
        )
        require(
            [(x["account"], x["side"]) for x in original_lines]
            == [("CustomerOrderLiability", "DEBIT"), ("SalesRevenue", "CREDIT")]
            and all(
                x["amount"] == cap["amount"] and x["currency"] == cap["currency"] for x in original_lines
            ),
            "original gross revenue journal semantics",
        )
        require(
            cap["tenant_id"] == lock["tenant_id"] and cap["operation_id"] == lock["purchase_operation"],
            "capture owner",
        )
        order = seller_orders[v["order_id"]]
        final = next(x for x in pack["seller_finalizations"] if x["order_id"] == order["id"])
        require(v["seller_finalization_hash"] == sha(final["raw"]), "seller finalization provenance")
        for key in (
            "tenant_id",
            "account_id",
            "integration",
            "purchase_id",
            "capture_fact_id",
            "workspace_id",
            "amount",
            "currency",
            "terms_digest",
        ):
            require(v[key] == order[key], "seller lock exact " + key)
        require(v["payment_id"] == cap["effect_id"], "exact captured payment")
        require(
            v["seats"]
            == sorted(
                [
                    {"id": e["id"], "beneficiary": e["beneficiary"]}
                    for e in entitlements.values()
                    if e["order_id"] == order["id"]
                ],
                key=lambda x: x["id"],
            ),
            "exact beneficiaries",
        )
    for b in obligations.values():
        require(b["lock_id"] in locks, "obligation lock")
        require(b["capture_fact_id"] in captures, "obligation trusted capture")
        cap = captures[b["capture_fact_id"]]
        for key in ("tenant_id", "account_id", "integration", "purchase_id", "currency"):
            require(cap[key] == b[key], "obligation capture " + key)
        require(
            b["payment_id"] == cap["effect_id"] and b["provider"] == cap["provider"],
            "obligation payment/provider",
        )
        require(
            b["provider_order"] == purchase_observations[cap["observation_id"]]["order_id"],
            "original provider order",
        )
        require(0 < b["amount"] <= cap["amount"], "obligation principal bounds")
        a, e = actions[b["action_id"]], evidence[b["evidence_id"]]
        require(a["kind"] == "REFUND" and grants[a["grant_id"]]["kind"] == "REFUND", "separate refund grant")
        require(
            a["amount"] == b["amount"]
            and a["terms"]["seller_lock"] == json.loads(locks[b["lock_id"]]["raw"]),
            "exact final action",
        )
        require(e["return_lock_id"] == b["lock_id"] and e["action_id"] == a["id"], "exact evidence")
        require(
            a["created_at"] >= locks[b["lock_id"]]["confirmed_at"] and b["accepted_at"] >= a["created_at"],
            "critical sequence",
        )
        require(
            len(
                [
                    r
                    for r in t["principal_reservations"]
                    if r["obligation_id"] == b["id"]
                    and r["capture_fact_id"] == cap["id"]
                    and r["amount"] == b["amount"]
                ]
            )
            == 1,
            "principal reservation identity",
        )
        require(
            len([o for o in ops.values() if o["obligation_id"] == b["id"]]) == 1,
            "commercial operation identity",
        )
    for o in ops.values():
        b = obligations[o["obligation_id"]]
        require(sha(canonical(o["body"])) == o["request_digest"], "operation request digest")
        expected = {
            "payment_id": b["payment_id"],
            "amount": b["amount"],
            "currency": b["currency"],
            "notes": {
                "interlock_operation": o["id"],
                "interlock_obligation": b["id"],
                "interlock_account": b["account_id"],
                "interlock_integration": b["integration"],
            },
        }
        require(
            o["body"] == expected and o["operation_key"] == "refund_" + o["id"],
            "typed operation exact binding",
        )
    effect_keys = set()
    fact_keys = set()
    for f in facts.values():
        require(f["kind"] in ("REFUND_ACCEPTED", "REFUND_PROCESSED"), "typed refund economic fact")
        if f["kind"] == "REFUND_ACCEPTED":
            require(
                f["observation_id"] is None and f["refund_id"] is None, "commercial obligation fact source"
            )
        b = obligations[f["obligation_id"]]
        require((f["obligation_id"], f["kind"]) not in fact_keys, "duplicate economic fact")
        fact_keys.add((f["obligation_id"], f["kind"]))
        require(
            f["operation_id"] in ops and ops[f["operation_id"]]["obligation_id"] == b["id"], "fact operation"
        )
        require(
            f["capture_fact_id"] == b["capture_fact_id"]
            and f["amount"] == b["amount"]
            and f["currency"] == b["currency"],
            "exact fact principal",
        )
        if f["kind"] == "REFUND_PROCESSED":
            require(
                any(d["operation_id"] == f["operation_id"] for d in t["dispatch_records"]),
                "processed fact cutoff provenance",
            )
            ob = observations[f["observation_id"]]
            rc = receipts[ob["receipt_id"]]
            ch = channels[rc["channel_id"]]
            require(states[rc["id"]]["authentication"] == "AUTHENTICATED", "authenticated refund source")
            require(
                ch["provider"] == b["provider"]
                and ch["account_id"] == b["account_id"]
                and ch["integration"] == b["integration"],
                "source channel binding",
            )
            require(
                ch["provider"] != "RAZORPAY_TEST" or ch["source"] == "CANONICAL_READ",
                "canonical real refund required",
            )
            raw = json.loads(base64.b64decode(rc["body_b64"]))
            require(raw == ob["body"], "observation provenance")
            entity = raw["payload"]["refund"]["entity"]
            require(
                entity["id"] == f["refund_id"] == ob["refund_id"] and entity["status"] == "processed",
                "processed refund identity",
            )
            require(
                entity["payment_id"] == b["payment_id"]
                and entity["amount"] == b["amount"]
                and entity["currency"] == b["currency"],
                "provider exact principal",
            )
            require(
                entity["notes"] == ops[f["operation_id"]]["body"]["notes"]
                and ob["obligation_id"] == b["id"]
                and ob["operation_id"] == f["operation_id"],
                "provider exact operation correlation",
            )
            ek = (b["provider"], b["account_id"], f["refund_id"])
            require(ek not in effect_keys, "duplicate provider effect")
            effect_keys.add(ek)
    for p in t["principal"]:
        require(p["captured"] == captures[p["capture_fact_id"]]["amount"], "captured principal source")
        processed = sum(
            f["amount"]
            for f in facts.values()
            if f["capture_fact_id"] == p["capture_fact_id"] and f["kind"] == "REFUND_PROCESSED"
        )
        unresolved = sum(
            b["amount"]
            for b in obligations.values()
            if b["capture_fact_id"] == p["capture_fact_id"] and (b["id"], "REFUND_PROCESSED") not in fact_keys
        )
        require(
            p["processed"] == processed
            and p["unresolved"] == unresolved
            and 0 <= processed + unresolved <= p["captured"],
            "D+U<=P reconstruction",
        )
    for h in pack["grant_heads"]:
        movements = [m for m in pack["movements"] if m["grant_id"] == h["grant_id"]]
        require(h["bound"] == h["consumed"] + h["reserved"] + h["free"], "authority conservation")
        require(
            h["consumed"] == sum(m["delta_consumed"] for m in movements)
            and h["reserved"] == sum(m["delta_reserved"] for m in movements)
            and h["free"] == h["bound"] + sum(m["delta_free"] for m in movements),
            "authority movement reconstruction",
        )
        if grants[h["grant_id"]]["kind"] == "PURCHASE":
            require(
                not any(m["kind"] == "REFUND_PROCESSED" for m in movements),
                "purchase authority never replenished",
            )
        else:
            require(
                not any(m["kind"] == "REFUND_PROCESSED" and m["delta_free"] != 0 for m in movements),
                "refund authority never replenished",
            )
    for b in obligations.values():
        reservation = reservations[b["reservation_id"]]
        require(
            reservation["action_id"] == b["action_id"]
            and reservation["amount"] == b["amount"]
            and reservation["grant_id"] == actions[b["action_id"]]["grant_id"],
            "exact merchant reservation",
        )
        processed = (b["id"], "REFUND_PROCESSED") in fact_keys
        require(
            reservation_heads[b["reservation_id"]]["status"]
            == ("REFUND_PROCESSED" if processed else "RESERVED"),
            "UNKNOWN/failure retain Q; processed spends Q",
        )
        movements = [m for m in pack["movements"] if m["reservation_id"] == reservation["id"]]
        require(
            sum(m["delta_reserved"] for m in movements) == (0 if processed else b["amount"])
            and sum(m["delta_consumed"] for m in movements) == (b["amount"] if processed else 0)
            and sum(m["delta_free"] for m in movements) == -b["amount"],
            "exact refund A/Q/S transitions",
        )
    posted = set()
    for p in t["postings"]:
        require(p["fact_id"] not in posted, "duplicate refund posting")
        posted.add(p["fact_id"])
        f = facts[p["fact_id"]]
        require(
            p["template"]
            == ("RETURN_TO_PAYABLE" if f["kind"] == "REFUND_ACCEPTED" else "PAYABLE_TO_PROVIDER"),
            "fixed refund template",
        )
        accounts = (
            ["SalesReturns", "RefundPayable"]
            if f["kind"] == "REFUND_ACCEPTED"
            else ["RefundPayable", "ProviderClearing"]
        )
        lines = sorted([x for x in t["lines"] if x["posting_id"] == p["id"]], key=lambda x: x["ordinal"])
        require(
            [x["account"] for x in lines] == accounts and [x["side"] for x in lines] == ["DEBIT", "CREDIT"],
            "refund journal semantics",
        )
        require(
            all(x["amount"] == f["amount"] and x["currency"] == f["currency"] for x in lines),
            "refund journal principal",
        )
    for attempt in t["attempts"]:
        require(
            len(
                [
                    x
                    for x in t["dispatch_records"]
                    if x["attempt_id"] == attempt["id"] and x["operation_id"] == attempt["operation_id"]
                ]
            )
            == 1,
            "dispatch record",
        )
        require(
            len([x for x in t["recovery_duties"] if x["operation_id"] == attempt["operation_id"]]) == 1,
            "durable recovery duty",
        )
    revocations = {x["entitlement_id"]: x for x in pack["revocations"]}
    seller_closures = {x["lock_id"]: x for x in pack["seller_closures"]}
    for revocation in revocations.values():
        if revocation["reason"].startswith("COMMERCIAL_RETURN:"):
            lid = revocation["reason"].split(":", 1)[1]
            require(lid in seller_closures and lid in seller_locks, "forged entitlement revocation")
            v = json.loads(seller_locks[lid]["raw"])
            require(revocation["entitlement_id"] in [s["id"] for s in v["seats"]], "revocation seat mapping")
    for closure in t["closures"]:
        lid = closure["lock_id"]
        require(
            lid in seller_closures and seller_closures[lid]["raw"] == closure["raw"],
            "signed seller closure provenance",
        )
        v = json.loads(locks[lid]["raw"])
        require(
            all(
                s["id"] in revocations and revocations[s["id"]]["reason"] == "COMMERCIAL_RETURN:" + lid
                for s in v["seats"]
            ),
            "exact entitlement revocation",
        )
        p = next(p for p in t["principal"] if p["capture_fact_id"] == locks[lid]["capture_fact_id"])
        require(p["processed"] == p["captured"] and p["unresolved"] == 0, "full commercial closure")
        require(
            all(f["id"] in posted for f in facts.values() if f["capture_fact_id"] == p["capture_fact_id"]),
            "closure requires booked refund economics",
        )
    return {"verified": True, "obligations": len(obligations), "facts": len(facts), "postings": len(posted)}
