"""Actual configured model call, with no payment or database credentials loaded."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values
from interlock.proposer import ModelConfig, ProposalFailure, Proposer

PRIVATE = Path(__file__).resolve().parents[2] / "tmp/hackathon-private/model.env"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", required=True, help="Use synthetic beneficiary data only")
    parser.add_argument("--beneficiary", default="alice", help="Probe scope only, not a financial identity")
    parser.add_argument("--export", action="store_true", help="Save a new sanitized actual-model evidence snapshot")
    parser.add_argument("--token-limit-parameter", choices=("max_tokens", "max_completion_tokens"),
                        help="Use the selected endpoint's documented completion-limit field")
    args = parser.parse_args()
    client = None
    try:
        values = dotenv_values(PRIVATE)
        config = ModelConfig(values.get("MODEL_PROVIDER") or "", values.get("MODEL_API_BASE") or "",
                             values.get("MODEL_NAME") or "", values.get("MODEL_API_KEY") or "",
                             args.token_limit_parameter or values.get("MODEL_TOKEN_LIMIT_PARAMETER") or "max_completion_tokens")
        client = Proposer(config)
        result = client.propose(args.intent, [args.beneficiary])
        model_key = values.get("MODEL_API_KEY") or ""
        if model_key and model_key in json.dumps(result):
            raise ProposalFailure("MODEL_EVIDENCE_SANITIZATION_REQUIRED")
        if args.export:
            now = datetime.now(UTC)
            evidence = {"classification": "ACTUAL_MODEL_HTTP_WITH_SYNTHETIC_INTENT", "recorded_at": now.isoformat(),
                        "database_operations": 0, "provider_payment_operations": 0,
                        "integrated_financial_authority_flow": False, "result": result}
            root = PRIVATE.parents[2]
            directory = root / "docs/hackathon/evidence"
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / (now.strftime("%Y%m%dT%H%M%S%fZ") + "-model.json")
            with destination.open("x", encoding="utf-8") as handle:
                json.dump(evidence, handle, indent=2)
                handle.write("\n")
            print(json.dumps({"exported": destination.relative_to(root).as_posix(), "evidence": evidence}, indent=2))
        else:
            print(json.dumps(result, indent=2))
        return 0
    except ProposalFailure as error:
        print(json.dumps({"blocked": True, "code": str(error), "actual_model_result": False,
                          "provider_evidence": error.evidence}))
        return 2
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
