"""Offline equivalence check for the bounded USD preparation/seller specializations.

This checks source preservation, NOT SQL parsing, installed privileges, locks,
conservation or PostgreSQL execution. Never repack historical release evidence.
"""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def function(source, name):
    match = re.search(r"CREATE FUNCTION " + re.escape(name) + r"\(.*?END \$\$;", source, re.S)
    if not match:
        raise ValueError("Expected frozen function missing")
    return match.group()


def frozen(path):
    actual = (ROOT / path).read_text(encoding="utf-8")
    original = subprocess.check_output(["git", "show", "pass-7-backend-release:" + path], cwd=ROOT).decode()
    if actual.replace("\r\n", "\n") != original.replace("\r\n", "\n"):
        raise ValueError("Frozen kernel source drift")
    return actual


def expected_quote(source):
    result = function(source, "authority.register_quote")
    result = result.replace("authority.register_quote(", "authority.paypal_quote_v1(", 1)
    result = result.replace("'INR'", "'USD'").replace("name='seller'", "name='paypal-seller'")
    return result.replace("q:=raw::jsonb;", """q:=raw::jsonb;
    IF raw IS DISTINCT FROM dispatch.canonical(q) OR q->>'provider' IS DISTINCT FROM 'PAYPAL_SANDBOX'
       OR NOT EXISTS(SELECT 1 FROM paypal_v1.tenants t WHERE t.tenant_id=person.tenant_id
           AND t.merchant_id=q->>'merchant_id' AND t.currency=q->>'currency') THEN
        PERFORM authority.fail('PAYPAL_QUOTE_BINDING_MISMATCH',403); END IF;""")


def expected_prepare(source):
    result = function(source, "authority.command")
    result = result.replace("authority.command(", "authority.paypal_prepare_v1(", 1)
    result = result.replace("command.family", "paypal_prepare_v1.family").replace("'INR'", "'USD'")
    needle = "    person:=authority.actor(token);"
    result = result.replace(needle, needle + """
    IF family NOT IN ('create-grant','propose') OR NOT EXISTS(
        SELECT 1 FROM paypal_v1.tenants t WHERE t.tenant_id=person.tenant_id AND t.currency='USD') THEN
        PERFORM authority.fail('USD_PREPARATION_ONLY',403); END IF;""", 1)
    return result.replace(
        "IF NOT FOUND THEN PERFORM authority.fail('SCOPE_DENIED',403); END IF;\n            SELECT * INTO qh",
        "IF NOT FOUND OR q.currency IS DISTINCT FROM 'USD' OR g.currency IS DISTINCT FROM 'USD' THEN "
        "PERFORM authority.fail('SCOPE_DENIED',403); END IF;\n            SELECT * INTO qh", 1)


def expected_seller(source):
    result = function(source, "seller_truth.finalize")
    result = result.replace("seller_truth.finalize(", "seller_truth.paypal_finalize_v1(", 1)
    result = result.replace("'INR'", "'USD'").replace("3000", "100")
    return result.replace("v:=raw::jsonb;", """v:=raw::jsonb;
 IF raw IS DISTINCT FROM seller_truth.canonical(v)
 OR v->>'payment_id' IS NULL OR v->>'payment_id' !~ '^[A-Z0-9]{10,32}$'
 OR v->>'provider_order' IS NULL OR v->>'provider_order' !~ '^[A-Z0-9]{10,32}$'
 OR v->'quote'->>'provider' IS DISTINCT FROM 'PAYPAL_SANDBOX'
 OR NOT EXISTS(SELECT 1 FROM seller_truth.paypal_tenants t WHERE t.tenant_id=v->>'tenant_id'
 AND t.merchant_id=v->>'account_id' AND t.merchant_id=v->'quote'->>'merchant_id') THEN
 RAISE EXCEPTION 'exact native PayPal USD capability required'; END IF;""", 1)


def verify():
    authority = frozen("migrations/sql/0002_authority_kernel.sql")
    seller = frozen("migrations/sql/0009_seller_truth.sql")
    prepare = (ROOT / "paypal_migrations/sql/0021_prepare.sql").read_text(encoding="utf-8")
    new_seller = (ROOT / "paypal_migrations/sql/0021_seller.sql").read_text(encoding="utf-8")
    for name, actual, expected in (
        ("authority.paypal_quote_v1", prepare, expected_quote(authority)),
        ("authority.paypal_prepare_v1", prepare, expected_prepare(authority)),
        ("seller_truth.paypal_finalize_v1", new_seller, expected_seller(seller)),
    ):
        if function(actual, name) != expected:
            raise ValueError("USD specialization changed beyond its reviewed bounded lift: " + name)
    return {"classification": "OFFLINE_SOURCE_EQUIVALENCE_ONLY", "functions_checked": 3,
            "legacy_sources_unchanged": True, "postgresql_executed": False}


if __name__ == "__main__":
    print(json.dumps(verify()))
