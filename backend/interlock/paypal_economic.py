"""Finite USD economic cycle; no model or PSP mutation capabilities.

Network handoff occurs only after the capability transaction commits. Seller
retry uses its immutable exact capability and never re-captures a payment.
"""

import json

from interlock.contracts import canonical
from interlock.paypal_contracts import Signed
from interlock.paypal_kernel import PostgreSQLKernel
from interlock.paypal_services import local_caller


def cycle(kernel, seller):
    pending = kernel("pending", {})
    for receipt in pending["receipts"]:
        kernel("process_receipt", {"rid": receipt})
    for fact in pending["facts"]:
        kernel("post", {"fid": fact})
    for operation in pending["fulfillments"]:
        capability = Signed.model_validate(kernel("capability", {"opid": operation})).model_dump()
        result = Signed.model_validate(seller("finalize", capability))
        binding = json.loads(capability["raw"])
        # Fixed endpoint/channel derived from the immutable capability, not a URL
        # or destination chosen by a model, browser or seller response.
        receipt = kernel("receive", {"channel": "seller-" + binding["tenant_id"],
                                     "raw": result.raw.encode(),
                                     "headers": canonical({"signature": result.signature}).decode(),
                                     "delivery": "paypal-fulfilled-" + operation})
        kernel("process_receipt", {"rid": str(receipt)})


def configured_cycle():
    import os

    cycle(PostgreSQLKernel.configured("economic"),
          local_caller(os.environ.get("PAYPAL_SELLER_URL", ""), "/v1/paypal/seller/"))
