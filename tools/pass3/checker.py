"""Independent semantic verifier. Imports no production models or transitions."""

import base64
import hashlib
import json
from collections import Counter, defaultdict


def require(condition, message):
    if not condition:
        raise ValueError(message)


def indexed(rows, key="id"):
    result = {str(row[key]): row for row in rows}
    require(len(result) == len(rows), "duplicate identity")
    return result


def verify(data):
    channels = indexed(data["channels"])
    receipts = indexed(data["receipts"])
    states = indexed(data["receipt_states"], "receipt_id")
    observations = indexed(data["observations"])
    facts = indexed(data["facts"])
    operations = indexed(data["operations"])
    quotes = indexed(data["quotes"])
    postings = indexed(data["postings"])
    orders = indexed(data["seller_orders"])
    seats = indexed(data["entitlements"])
    finalizations = indexed(data["seller_finalizations"], "order_id")
    effect_keys = set()
    recognition_keys = set()
    for receipt in receipts.values():
        require(
            hashlib.sha256(base64.b64decode(receipt["body_b64"])).hexdigest() == receipt["body_hash"],
            "raw receipt digest mismatch",
        )
        require(receipt["channel_id"] in channels, "receipt channel missing")
    for ob in observations.values():
        receipt = receipts[ob["receipt_id"]]
        channel = channels[receipt["channel_id"]]
        require(states[receipt["id"]]["authentication"] == "AUTHENTICATED", "unauthenticated observation")
        raw = json.loads(base64.b64decode(receipt["body_b64"]))
        require(raw == ob["payload"], "observation not from retained bytes")
        require(
            ob["channel_id"] == channel["id"]
            and ob["account_id"] == channel["account_id"]
            and ob["provider"] == channel["provider"]
            and ob["integration"] == channel["integration"],
            "observation channel substitution",
        )
        require(raw["account_id"] == channel["account_id"], "evidence account mismatch")
        entity = raw if ob["provider"] == "CONTROLLED_SELLER" else raw["payload"]["payment"]["entity"]
        require(
            entity["amount"] == ob["amount"] and entity["currency"] == ob["currency"],
            "observation amount mismatch",
        )
        require(
            ob["object_id"] == entity["order_id" if ob["provider"] == "CONTROLLED_SELLER" else "id"]
            and ob["order_id"]
            == entity["provider_order" if ob["provider"] == "CONTROLLED_SELLER" else "order_id"],
            "observation external object substitution",
        )
        if ob["operation_id"]:
            op = operations[ob["operation_id"]]
            require(
                (
                    op["tenant_id"],
                    op["account_id"],
                    op["integration"],
                    op["domain_root"],
                    op["amount"],
                    op["currency"],
                )
                == (
                    ob["tenant_id"],
                    ob["account_id"],
                    ob["integration"],
                    ob["purchase_id"],
                    ob["amount"],
                    ob["currency"],
                ),
                "observation purchase substitution",
            )
            require(op["provider_order"] == ob["order_id"], "provider order substitution")
    for fact in facts.values():
        ob = observations[fact["observation_id"]]
        identity = (fact["provider"], fact["account_id"], fact["effect_id"], fact["kind"])
        require(identity not in effect_keys, "duplicate effect fact")
        effect_keys.add(identity)
        require(fact["derivation_version"] == "purchase-v1", "unsupported derivation")
        require(
            (fact["amount"], fact["currency"], fact["tenant_id"], fact["operation_id"], fact["purchase_id"])
            == (ob["amount"], ob["currency"], ob["tenant_id"], ob["operation_id"], ob["purchase_id"]),
            "fact source substitution",
        )
        require(fact["amount"] > 0 and fact["currency"] == "INR", "unsupported principal")
        require(
            fact["account_id"] == ob["account_id"] and fact["integration"] == ob["integration"],
            "fact evidence scope substitution",
        )
        if fact["kind"] == "CAPTURE":
            require(fact["provider"] == ob["provider"], "capture provider substitution")
            require(
                ob["kind"] == "CAPTURED" and fact["effect_id"] == ob["object_id"], "capture semantic source"
            )
            require(ob["provider"] != "CONTROLLED_SELLER", "seller invents capture")
            channel = channels[ob["channel_id"]]
            require(
                channel["provider"] != "RAZORPAY_TEST" or channel["source"] == "CANONICAL_READ",
                "Razorpay webhook invents fact",
            )
            require(fact["parent_fact_id"] is None, "capture parent")
        else:
            key = (fact["operation_id"], fact["kind"])
            require(key not in recognition_keys and key[0] is not None, "duplicate purchase recognition")
            recognition_keys.add(key)
            parent = facts[fact["parent_fact_id"]]
            require(
                (
                    fact["provider"],
                    fact["account_id"],
                    fact["integration"],
                    fact["tenant_id"],
                    fact["purchase_id"],
                    fact["amount"],
                    fact["currency"],
                    fact["effect_id"],
                    fact["operation_id"],
                )
                == (
                    parent["provider"],
                    parent["account_id"],
                    parent["integration"],
                    parent["tenant_id"],
                    parent["purchase_id"],
                    parent["amount"],
                    parent["currency"],
                    parent["effect_id"],
                    parent["operation_id"],
                ),
                "fact parent substitution",
            )
            if fact["kind"] == "ORDER_LIABILITY":
                require(parent["kind"] == "CAPTURE" and ob["kind"] == "CAPTURED", "liability needs capture")
            elif fact["kind"] == "SELLER_FULFILLED":
                require(
                    parent["kind"] == "CAPTURE" and ob["kind"] == "FULFILLED",
                    "fulfillment needs matched capture",
                )
                raw = ob["payload"]
                order = orders[raw["order_id"]]
                quote = quotes[order["quote_id"]]["body"]
                expected = sorted((x["allocation_id"], x["beneficiary"]) for x in quote["lines"])
                actual = sorted(
                    (x["id"], x["beneficiary"]) for x in seats.values() if x["order_id"] == order["id"]
                )
                require(
                    expected == actual == sorted((x["id"], x["beneficiary"]) for x in raw["seats"]),
                    "seller beneficiary mapping",
                )
                require(
                    raw["capture_fact_id"] == parent["id"] and raw["payment_id"] == parent["effect_id"],
                    "seller capture provenance",
                )
                require(
                    order["tenant_id"] == fact["tenant_id"] and order["operation_id"] == fact["operation_id"],
                    "seller tenant substitution",
                )
            elif fact["kind"] == "REVENUE":
                fulfillment = facts[fact["fulfillment_fact_id"]]
                require(
                    parent["kind"] == "ORDER_LIABILITY"
                    and fulfillment["kind"] == "SELLER_FULFILLED"
                    and fulfillment["parent_fact_id"] == parent["parent_fact_id"],
                    "revenue predicates",
                )
            else:
                raise ValueError("unsupported fact kind")
    mapping = {
        "CAPTURE": ("CAPTURE_TO_SUSPENSE", "ProviderClearing", "UnmatchedProviderSuspense"),
        "ORDER_LIABILITY": ("MATCH_TO_LIABILITY", "UnmatchedProviderSuspense", "CustomerOrderLiability"),
        "REVENUE": ("FULFILLMENT_TO_REVENUE", "CustomerOrderLiability", "SalesRevenue"),
    }
    posted_facts = set()
    totals = defaultdict(Counter)
    for posting in postings.values():
        fact = facts[posting["fact_id"]]
        require(fact["id"] not in posted_facts, "fact double posted")
        posted_facts.add(fact["id"])
        expected = mapping[fact["kind"]]
        require(
            posting["template"] == expected[0] and posting["template_version"] == 1,
            "semantic template mismatch",
        )
        require(
            (
                posting["provider"],
                posting["account_id"],
                posting["tenant_id"],
                posting["amount"],
                posting["currency"],
            )
            == (fact["provider"], fact["account_id"], fact["tenant_id"], fact["amount"], fact["currency"]),
            "posting scope substitution",
        )
        lines = sorted(
            (x for x in data["lines"] if x["posting_id"] == posting["id"]), key=lambda x: x["ordinal"]
        )
        require(len(lines) == 2 and [x["ordinal"] for x in lines] == [1, 2], "incomplete journal")
        require(
            [(x["account"], x["side"], x["amount"], x["currency"]) for x in lines]
            == [
                (expected[1], "DEBIT", fact["amount"], fact["currency"]),
                (expected[2], "CREDIT", fact["amount"], fact["currency"]),
            ],
            "balanced but semantically wrong journal",
        )
        if fact["kind"] in {"ORDER_LIABILITY", "REVENUE"}:
            require(fact["parent_fact_id"] in posted_facts, "posting dependency order")
        for line in lines:
            totals[(posting["provider"], posting["account_id"], posting["currency"])][line["account"]] += (
                line["amount"] * (1 if line["side"] == "DEBIT" else -1)
            )
    require(all(sum(scope.values()) == 0 for scope in totals.values()), "journal conservation")
    require(all(x["posting_id"] in postings for x in data["lines"]), "orphan journal line")
    require(len({x["operation_id"] for x in orders.values()}) == len(orders), "duplicate seller operation")
    for order in orders.values():
        cap = facts[order["capture_fact_id"]]
        quote = quotes[order["quote_id"]]
        require(cap["kind"] == "CAPTURE", "seller lacks economic capture")
        fields = (
            "tenant_id",
            "account_id",
            "integration",
            "operation_id",
            "purchase_id",
            "amount",
            "currency",
        )
        require(
            tuple(order[x] for x in fields) == tuple(cap[x] for x in fields)
            and order["payment_id"] == cap["effect_id"],
            "seller capture substitution",
        )
        require(
            order["terms"] == quote["body"]
            and quote["tenant_id"] == order["tenant_id"]
            and order["terms_digest"] == quote["body_digest"],
            "seller quote substitution",
        )
        expected = sorted((x["allocation_id"], x["beneficiary"]) for x in quote["body"]["lines"])
        actual = sorted((x["id"], x["beneficiary"]) for x in seats.values() if x["order_id"] == order["id"])
        require(expected == actual, "seller exact entitlement mapping")
        require(order["id"] in finalizations, "entitlements lack finalization")
        att = json.loads(finalizations[order["id"]]["raw"])
        require(
            sorted((x["id"], x["beneficiary"]) for x in att["seats"]) == actual
            and att["capture_fact_id"] == cap["id"]
            and att["operation_id"] == cap["operation_id"],
            "seller finalization provenance",
        )
    require(
        len({(x["order_id"], x["beneficiary"]) for x in seats.values()}) == len(seats),
        "duplicate entitlement",
    )
    for seat in seats.values():
        order = orders[seat["order_id"]]
        require(
            seat["tenant_id"] == order["tenant_id"]
            and seat["workspace_id"] == order["workspace_id"]
            and seat["product"] == "workspace-seat",
            "entitlement scope substitution",
        )
    return {
        "facts": len(facts),
        "postings": len(postings),
        "entitlements": len(seats),
        "facts_without_postings": sorted(
            f["id"] for f in facts.values() if f["kind"] != "SELLER_FULFILLED" and f["id"] not in posted_facts
        ),
    }
