"""Opt-in same-order continuation. Cannot CREATE, reserve, approve or choose a payee."""

from uuid import UUID

from pydantic import AwareDatetime, Field

from interlock.paypal_contracts import Operation
from interlock.paypal_flow import BoundaryFailure, ProtectedPayPal


class ContinuationPrepare(Operation):
    continuation_id: UUID = Field(strict=False)


class ContinuationApprove(Operation):
    continuation_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    # FastAPI validates decoded JSON objects; allow ISO timestamps on the wire
    # while retaining the timezone-aware type and SQL deadline bounds.
    expires_at: AwareDatetime = Field(strict=False)


class ContinuationOperation(Operation):
    continuation_id: UUID = Field(strict=False)


def capture(driver: ProtectedPayPal, operation_id: str, continuation_id: str) -> dict:
    """Reuse the proven one-send transport with a distinct protected commit entry."""
    continuation_id = str(UUID(continuation_id))

    def restricted(command, parameters):
        if command == "commit_phase":
            if parameters.get("phase") != "CAPTURE" or parameters.get("opid") != operation_id:
                raise BoundaryFailure("CONTINUATION_CAPTURE_ONLY")
            return driver.kernel("continuation_commit", {
                "secret": parameters["secret"], "opid": operation_id, "cid": continuation_id,
                "expected_digest": parameters["expected_digest"],
            })
        return driver.kernel(command, parameters)

    # No financial transaction remains open across the inherited provider I/O.
    return ProtectedPayPal(restricted, driver.provider, driver.identity).capture(operation_id)
