"""Bounded GET-only local case benchmark. No model, provider or financial command.

Requires the explicit already-authorized postgres18/m2_demo selection. A
requester session is obtained using the existing local session service; never
an approver. Responses/tokens/private rows are not exported.
"""
import json
import math
import platform
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx

from tools.hackathon.m2_preflight import DemoClient
from tools.hackathon.m2016_snapshot import snapshot

ROOT = Path(__file__).resolve().parents[2]
OP = "op_3c78b42e074541dfac3a3ed8456fb59b"
URL = "http://127.0.0.1:18105/v1/commerce/cases/" + OP


def percentile(values, fraction):
    return round(sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)], 3) if values else None


def main():
    baseline = json.loads((ROOT / "tmp/hackathon-private/m2020-no-write-baseline.json").read_text())
    if snapshot() != baseline:
        raise RuntimeError("FINANCIAL_BASELINE_MISMATCH")
    client = DemoClient({})
    try:
        token = client.token("requester")
        with httpx.Client(timeout=15, trust_env=False, follow_redirects=False,
                          transport=httpx.HTTPTransport(retries=0)) as http:
            health = http.get("http://127.0.0.1:18105/health")
            if health.status_code != 200 or health.json().get("service") != "read-only-commerce":
                raise RuntimeError("EXACT_READ_SERVICE_REQUIRED")

            def read(index):
                start = time.perf_counter()
                try:
                    response = http.get(URL, headers={"Authorization": "Bearer " + token})
                    valid = response.status_code != 200 or response.json().get("operation_id") == OP
                    return {"index": index, "status": response.status_code,
                            "latency_ms": round((time.perf_counter() - start) * 1000, 3),
                            "response_bytes": len(response.content), "identity_valid": valid}
                except (httpx.HTTPError, ValueError):
                    return {"index": index, "status": "TRANSPORT_ERROR",
                            "latency_ms": round((time.perf_counter() - start) * 1000, 3), "identity_valid": False}

            warmup = read(0)
            groups, index = [], 1
            for count, concurrency in [(12, 1), (16, 4), (16, 8)]:
                start = time.perf_counter()
                with ThreadPoolExecutor(max_workers=concurrency) as executor:
                    samples = list(executor.map(read, range(index, index + count)))
                duration = time.perf_counter() - start
                good = [s["latency_ms"] for s in samples if s["status"] == 200]
                groups.append({"requests": count, "concurrency": concurrency, "wall_seconds": round(duration, 4),
                               "statuses": dict(Counter(str(s["status"]) for s in samples)),
                               "successful_requests_per_second": round(len(good) / duration, 3),
                               "successful_p50_ms": percentile(good, .5), "successful_empirical_p95_ms": percentile(good, .95),
                               "samples": samples})
                index += count
        unchanged = snapshot() == baseline
        report = {"classification": "BOUNDED_GENUINE_LOCAL_READ_BENCHMARK", "checked_at": datetime.now(UTC).isoformat(),
                  "platform": platform.platform(), "python": platform.python_version(), "endpoint": URL,
                  "method": "GET", "total_case_requests_including_warmup": index,
                  "warmup": warmup, "groups": groups, "financial_tables_unchanged": unchanged,
                  "financial_tables_compared": len(baseline["tables"]), "provider_and_model_calls": 0,
                  "limits": ["Single Windows host and small certified dataset; not horizontal scalability evidence.",
                             "Empirical percentiles have only 12/16 samples per group; no p99 claim.",
                             "40 requests/minute and 4 active case reads are existing per-principal bounds; 429 is expected overload behavior.",
                             "Other local services share CPU/IO; no isolated CPU/memory attribution or production capacity claim."]}
        target = ROOT / "tmp/hackathon-020/read-benchmark.json"
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if not unchanged or any(not s["identity_valid"] or s["status"] not in (200, 429)
                                for group in groups for s in group["samples"]):
            raise RuntimeError("READ_BENCHMARK_FAILED")
        print(json.dumps({k: v for k, v in report.items() if k not in {"warmup", "groups", "limits"}}))
        print(json.dumps([{k:v for k,v in group.items() if k != "samples"} for group in groups]))
    finally:
        client.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("READ_ONLY_BENCHMARK_FAILED_SANITIZED")
        raise SystemExit(1) from None
