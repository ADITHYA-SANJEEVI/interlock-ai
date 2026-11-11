"""Independent, read-only checker of exported Pass 2 binding and sequence invariants.

It does not authorize a handoff, infer economic facts, or prove external exactly-once behavior.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path


def check(data):
    issues = []

    def require(condition, code):
        if not condition:
            issues.append(code)

    for bundle in data["operations"]:
        o = bundle["operation"]
        rt = bundle["runtime"]
        a, e, r, gv = (bundle[name] for name in ("action", "evidence", "reservation", "grant_version"))
        require(
            a is not None and e is not None and r is not None and gv is not None, "MISSING_AUTHORITY_SOURCE"
        )
        if any(value is None for value in (a, e, r, gv)):
            continue
        canonical = json.dumps(o["body"], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        require(hashlib.sha256(canonical).hexdigest() == o["request_digest"], "ALTERED_REQUEST_DIGEST")
        require(
            o["body"]
            == {
                "amount": o["amount"],
                "currency": o["currency"],
                "receipt": o["operation_key"],
                "partial_payment": False,
                "notes": {
                    "interlock_operation": o["operation_key"],
                    "interlock_account": o["account_id"],
                    "interlock_integration": o["integration"],
                },
            },
            "ALTERED_EXACT_BODY_BINDING",
        )
        require(
            a["id"] == o["action_id"] == e["action_id"] == r["action_id"] and e["id"] == o["evidence_id"],
            "ACTION_EVIDENCE_MISMATCH",
        )
        require(
            o["action_digest"] == a["request_digest"] == r["source_terms_digest"]
            and o["evidence_digest"] == e["digest"],
            "SOURCE_DIGEST_MISMATCH",
        )
        require(
            gv["id"] == o["grant_version_id"] == e["grant_version_id"] == r["grant_version_id"]
            and gv["grant_id"] == o["grant_id"] == r["grant_id"]
            and gv["epoch"] == o["grant_epoch"] == e["grant_epoch"] == r["epoch"],
            "GRANT_EPOCH_MISMATCH",
        )
        require(
            r["id"] == o["reservation_id"]
            and a["amount"] == r["amount"] == o["amount"]
            and a["currency"] == r["currency"] == o["currency"],
            "RESERVATION_BINDING_MISMATCH",
        )
        require(all(source["tenant_id"] == o["tenant_id"] for source in (a, e, r, gv)), "CROSS_TENANT_SOURCE")
        attempts, records, duties, exposures = (
            bundle[name] for name in ("attempts", "dispatch_records", "duties", "exposures")
        )
        require(len(attempts) == len(records) == rt["attempt_count"], "MISSING_DISPATCH_OR_ATTEMPT")
        require(
            [item["number"] for item in attempts] == list(range(1, len(attempts) + 1)),
            "INVALID_ATTEMPT_NUMBERING",
        )
        for attempt in attempts:
            matches = [item for item in records if item["attempt_id"] == attempt["id"]]
            require(len(matches) == 1, "MISSING_DISPATCH_RECORD")
            if len(matches) != 1:
                continue
            record = matches[0]
            require(
                record["attempt_number"] == attempt["number"]
                and attempt["request_digest"] == o["request_digest"] == record["request_digest"],
                "ATTEMPT_DIGEST_MISMATCH",
            )
            require(
                attempt["operation_key"] == o["operation_key"]
                and record["operation_id"] == attempt["operation_id"] == o["id"],
                "ATTEMPT_OPERATION_MISMATCH",
            )
            require(
                all(
                    record[name] == o[name]
                    for name in (
                        "tenant_id",
                        "grant_id",
                        "grant_version_id",
                        "grant_epoch",
                        "action_id",
                        "evidence_id",
                        "action_digest",
                        "evidence_digest",
                        "approval_id",
                        "reservation_id",
                        "decision_id",
                        "config_epoch",
                        "replay_contract_id",
                    )
                ),
                "STALE_DISPATCH_PERMISSION_BINDING",
            )
            require(record["policy_version_id"] == e["policy_version_id"], "DISPATCH_POLICY_MISMATCH")
            at = datetime.fromisoformat(record["committed_at"])
            require(
                at < datetime.fromisoformat(record["grant_expires_at"])
                and at < datetime.fromisoformat(record["service_expires_at"]),
                "EXPIRED_DISPATCH_PERMISSION",
            )
            if record["approval_expires_at"]:
                require(
                    at < datetime.fromisoformat(record["approval_expires_at"]), "EXPIRED_DISPATCH_APPROVAL"
                )
            require(
                record["committed_at"] == attempt["committed_at"]
                and at >= datetime.fromisoformat(o["created_at"]),
                "IMPOSSIBLE_LOCAL_SEQUENCE",
            )
            require(
                any(
                    event["reference_id"] == record["id"] and event["event"] == "HANDOFF_COMMITTED"
                    for event in bundle["audit"]
                ),
                "MISSING_DISPATCH_AUDIT",
            )
        if attempts:
            require(rt["state"] not in {"QUEUED", "READY", "CANCEL_UNSENT"}, "COMMITTED_RESET_TO_UNSENT")
            require(
                len([d for d in duties if d["origin_kind"] == "OPERATION"]) == 1, "MISSING_COMMITTED_DUTY"
            )
        if rt["knowledge"] == "UNKNOWN":
            require(bundle["reservation_head"]["status"] == "RESERVED", "UNKNOWN_CAPACITY_FREED")
        for exposure in exposures:
            require(
                all(
                    exposure[name] == o[name]
                    for name in (
                        "tenant_id",
                        "domain_root",
                        "action_id",
                        "evidence_id",
                        "grant_id",
                        "grant_version_id",
                        "grant_epoch",
                        "reservation_id",
                        "integration",
                        "provider_mode",
                        "account_id",
                        "amount",
                        "currency",
                    )
                )
                and exposure["provider_order"] == rt["provider_order"],
                "EXPOSURE_PROVIDER_BINDING_MISMATCH",
            )
            require(bundle["reservation_exposure"]["state"] == "EXPOSED_UNRESOLVED", "EXPOSURE_LOST")
            require(
                len([d for d in duties if d["exposure_id"] == exposure["id"]]) == 1, "MISSING_EXPOSURE_DUTY"
            )
            require(
                datetime.fromisoformat(exposure["first_disclosed_at"])
                >= min(datetime.fromisoformat(a["committed_at"]) for a in attempts),
                "EXPOSURE_BEFORE_HANDOFF",
            )
        observations = {item["id"]: item for item in bundle["observations"]}
        for observation in observations.values():
            if observation["classification"] == "EXACT_ZERO_EFFECT":
                proof = observation["body"]
                require(
                    observation["source"] == "CANONICAL_EXACT_READ"
                    and o["adapter"] == "REFERENCE_SYNTHETIC"
                    and proof["coverage"] == "PINNED_REFERENCE_TERMINAL_REJECTION_V1"
                    and proof["receipt"] == o["operation_key"]
                    and proof["request_digest"] == o["request_digest"]
                    and proof["account_id"] == o["account_id"],
                    "UNBOUND_TERMINAL_ZERO_PROOF",
                )
        for duty in duties:
            require(
                duty["reservation_id"] == o["reservation_id"]
                and duty["operation_id"] == o["id"]
                and duty["mutation_requires_current_authority"],
                "DUTY_BINDING_MISMATCH",
            )
            if duty["runtime"]["state"] == "DISCHARGED":
                observation = observations.get(duty["runtime"]["discharge_observation_id"])
                require(
                    observation is not None
                    and observation["classification"] in {"EXACT_EFFECT", "EXACT_ZERO_EFFECT"}
                    and duty["predicate"] == "EXACT_ORDER_EFFECT",
                    "UNSUPPORTED_DUTY_DISCHARGE",
                )
        head = bundle["grant_head"]
        require(
            head["bound"] == head["consumed"] + head["reserved"] + head["free"],
            "AUTHORITY_CONSERVATION_BROKEN",
        )
    return {
        "passed": not issues,
        "issues": sorted(set(issues)),
        "operations_checked": len(data["operations"]),
    }


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("export", type=Path)
    args = parser.parse_args()
    result = check(json.loads(args.export.read_text(encoding="utf-8")))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
