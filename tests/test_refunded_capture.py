from conftest import captured, login
from interlock.config import settings
from interlock.db import transaction
from interlock.models import Capture
from sqlalchemy import func, select


def test_full_refund_keeps_gross_capture_and_active_seat_probe_scope(clients):
    pid, owner = captured(clients)
    merchant = login(clients["api"], "merchant-a")
    response = clients["api"].post(
        "/refund-probes",
        json={"purchase_id": pid, "amount": 3000, "probe_key": "full-principal-probe-key-123"},
        headers=merchant,
    )
    assert response.status_code == 200 and response.json()["status"] == "processed"
    assert clients["api"].post(f"/purchases/{pid}/refresh", json={}, headers=owner).status_code == 200
    data = clients["api"].get("/purchases/" + pid, headers=owner).json()
    assert data["provider_status"] == "refunded" and data["canonical_status"] == "CAPTURE_VERIFIED"
    assert not data["conflict"]
    with transaction(settings().control_db) as db:
        assert db.scalar(select(func.count()).select_from(Capture)) == 1
