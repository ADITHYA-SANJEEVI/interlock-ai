"""Separate economic role: deterministic replay, bounded seller delivery, no PSP mutations."""

import os
import time

import httpx

from interlock.config import settings
from interlock.economic import kernel, process, receive


def cycle():
    url = os.environ["ECONOMIC_DATABASE_URL"]
    pending = kernel("pending", {}, url)
    for receipt in pending["receipts"]:
        from interlock.returns import process as refund_process

        if not refund_process(receipt, url):
            process(receipt, url)
    for fact in pending["facts"]:
        kernel("post", {"fact": fact}, url)
    for operation in pending["fulfillments"]:
        capability = kernel("capability", {"operation": operation}, url)
        # All SQL transactions have ended before remote seller handoff.
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            result = client.post(settings().seller_url + "/truth/finalize", json=capability)
            result.raise_for_status()
        attestation = result.json()
        body = attestation["raw"].encode()
        # Endpoint is resolved from the immutable capability, never seller-supplied routing.
        import json

        binding = json.loads(capability["raw"])
        receipt = receive(
            "seller-" + binding["tenant_id"], body, attestation["signature"], "fulfilled-" + operation, url
        )
        process(receipt, url)
    from interlock.returns import kernel as return_kernel

    refund_pending = return_kernel("pending", {}, url)
    for item in refund_pending["operations"]:
        facts = return_kernel("derive", {"operation": item["operation_id"], "observation": item["observation_id"]}, url)
        for fact in facts:
            return_kernel("post", {"fact": fact}, url)


if __name__ == "__main__":
    while True:
        try:
            cycle()
        except Exception:
            # Durable checkpoints recover on the next cycle; logs contain no evidence/credentials.
            print("Economic cycle unresolved; durable evidence retained", flush=True)
        time.sleep(2)
