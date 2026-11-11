"""Mocked frozen Razorpay wire shapes with actual PostgreSQL broker/economic roles."""

import pytest
from fastapi import HTTPException
from interlock import economic, economic_broker
from interlock.db import transaction
from interlock.dispatch import ExactOperation
from test_authority import auth as auth
from test_dispatch import d as d
from test_economic import KEYS, record
from test_economic import save_pass3 as save_pass3
from test_economic import truth as truth


@pytest.mark.parametrize("attack", [None, "order", "payment", "amount", "currency", "multiple"])
def test_exact_canonical_read_preserves_frozen_provider_contract(truth, monkeypatch, attack):
    truth.d.sql("""UPDATE dispatch.integrations SET adapter='RAZORPAY_TEST',account_id='acc_TEST',
    replay_contract_id='razorpay-create-v1' WHERE tenant_id='tenant-a'""")
    op = truth.operation(send=False, publish_policy=False)
    truth.d.sql(
        "UPDATE dispatch.operation_runtime SET provider_order='order_CANONICAL' WHERE operation_id=:id",
        {"id": op["id"]},
    )
    payment = {
        "id": "pay_CANONICAL",
        "order_id": "order_CANONICAL",
        "amount": 3000,
        "currency": "INR",
        "captured": True,
        "status": "captured",
    }

    class FrozenWire:
        def get_order(self, order):
            assert order == "order_CANONICAL"
            return {
                "id": "order_OTHER" if attack == "order" else order,
                "amount": 3000,
                "currency": "INR",
                "receipt": op["operation_key"],
            }

        def order_payments(self, order):
            assert order == "order_CANONICAL"
            return {"items": [payment, payment] if attack == "multiple" else [payment]}

        def get_payment(self, identifier):
            assert identifier == "pay_CANONICAL"
            result = dict(payment)
            if attack == "payment":
                result["order_id"] = "order_FOREIGN"
            if attack == "amount":
                result["amount"] += 1
            if attack == "currency":
                result["currency"] = "USD"
            return result

    broker_url = truth.urls["control"].replace("interlock:local_only@", "interlock_broker:local_only@")
    monkeypatch.setattr(economic_broker, "transaction", lambda _url: transaction(broker_url))
    monkeypatch.setattr(economic_broker, "RazorpayTest", FrozenWire)
    monkeypatch.setattr(economic_broker, "receive", lambda *args: economic.receive(*args, url=broker_url))
    monkeypatch.setenv("ECONOMIC_CANONICAL_SIGNING_KEY", KEYS["canonical"])
    if attack:
        with pytest.raises(HTTPException) as error:
            economic_broker.canonical_read(ExactOperation(operation_id=op["id"]))
        assert error.value.status_code == 409
        assert truth.count("facts") == 0
    else:
        result = economic_broker.canonical_read(ExactOperation(operation_id=op["id"]))
        assert len(truth.process(result["receipt_id"])) == 2
        assert result["new_real_mutations"] == 0
    record(
        "canonical_wire_" + str(attack),
        truth,
        provider_evidence="MODELED_FROZEN_RAZORPAY_WIRE_NO_EXTERNAL_CALL",
    )
