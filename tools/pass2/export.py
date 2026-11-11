"""Owner/test read-only bounded audit export. No provider credentials or private oracle."""

import json
import os
from pathlib import Path

from sqlalchemy import create_engine, text


def export(db, operation_id=None):
    result = {"provenance": "POSTGRES_DISPATCH_AUDIT_EXPORT_NO_ECONOMIC_FACTS", "operations": []}

    def one(table, column, value):
        row = db.execute(
            text("SELECT to_jsonb(t) FROM " + table + " t WHERE " + column + "=:value"), {"value": value}
        ).scalar()
        return row

    def many(table, opid, order="id"):
        return list(
            db.execute(
                text("SELECT to_jsonb(t) FROM " + table + " t WHERE operation_id=:value ORDER BY " + order),
                {"value": opid},
            ).scalars()
        )

    for o in db.execute(
        text(
            "SELECT to_jsonb(t) FROM dispatch.business_operations t WHERE (CAST(:opid AS text) IS NULL OR id=:opid) ORDER BY id"
        ),
        {"opid": operation_id},
    ).scalars():
        bundle = {"operation": o}
        for name, table, column, value in [
            ("runtime", "dispatch.operation_runtime", "operation_id", o["id"]),
            ("action", "authority.action_revisions", "id", o["action_id"]),
            ("evidence", "authority.evidence_revisions", "id", o["evidence_id"]),
            ("decision", "authority.policy_decisions", "id", o["decision_id"]),
            ("grant_version", "authority.grant_versions", "id", o["grant_version_id"]),
            ("grant_head", "authority.grant_heads", "grant_id", o["grant_id"]),
            ("reservation", "authority.reservations", "id", o["reservation_id"]),
            ("reservation_head", "authority.reservation_heads", "reservation_id", o["reservation_id"]),
            ("reservation_exposure", "dispatch.reservation_exposure", "reservation_id", o["reservation_id"]),
            ("approval", "authority.approvals", "id", o["approval_id"]),
        ]:
            bundle[name] = one(table, column, value)
        for name, table, order in [
            ("attempts", "transport_attempts", "number"),
            ("dispatch_records", "dispatch_records", "attempt_number"),
            ("duties", "recovery_obligations", "id"),
            ("exposures", "checkout_exposures", "first_disclosed_at"),
            ("observations", "operation_observations", "observed_at,id"),
            ("audit", "audit_events", "recorded_at,id"),
        ]:
            bundle[name] = many("dispatch." + table, o["id"], order)
        for duty in bundle["duties"]:
            duty["runtime"] = one("dispatch.duty_runtime", "obligation_id", duty["id"])
        for attempt in bundle["attempts"]:
            attempt["runtime"] = one("dispatch.attempt_runtime", "attempt_id", attempt["id"])
        result["operations"].append(bundle)
    return result


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--operation-id")
    args = parser.parse_args()
    with create_engine(
        os.getenv(
            "MIGRATION_DATABASE_URL", "postgresql+psycopg://interlock:local_only@127.0.0.1:55432/control"
        ),
        hide_parameters=True,
    ).connect() as db:
        data = export(db, args.operation_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print("Read-only Pass 2 audit export written; no provider calls or credential fields")


if __name__ == "__main__":
    main()
