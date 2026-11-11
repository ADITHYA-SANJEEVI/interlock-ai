"""Reuse the existing protected reconciliation functions with explicit v1 I/O.

No provider mutations, approvals, reservation releases, or executable findings.
"""

import httpx

from interlock.paypal_flow import BoundaryFailure
from interlock.reconciliation import Run, RunRequest


def execute(kernel, seller, token: str, body: RunRequest) -> Run:
    run = str(body.request_id)
    kernel("reconcile_start", {"token": token, "run": run, "cursor": body.after_operation, "limit": body.batch_size})
    params = {"token": token, "kind": "runs", "resource": run, "cursor": None, "limit": 1}
    current = Run.model_validate(kernel("reconcile_inspect", params)[0])
    for member in current.members:
        if member.state != "PENDING":
            continue
        args = {"token": token, "run": run, "operation": member.operation_id}
        capability = kernel("reconcile_prepare", args)
        try:
            attestation = seller("reconciliation-read", capability)
            kernel("reconcile_seller", {**args, **attestation})
        except (BoundaryFailure, httpx.HTTPError, ValueError):
            pass  # Missing seller evidence stays inconclusive, never fabricated absence.
        kernel("reconcile_evaluate", args)
    kernel("reconcile_finish", {"token": token, "run": run})
    return Run.model_validate(kernel("reconcile_inspect", params)[0])
