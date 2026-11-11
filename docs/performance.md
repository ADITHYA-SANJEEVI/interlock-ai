# Bounded performance evidence

Measured November 11 on Windows 11, Node 22.19.0 / Python 3.12.10. Raw samples,
browser version, CPU model, timestamps and limits are preserved with the final
Directive 020 evidence. Scripts are reproducible; no PayPal or model load test
was performed. This is not production capacity, horizontal scale or an SLA.

## Static public application

`node tools/release/static-benchmark.cjs` against the separately served static
build at 127.0.0.1:3020. Five fresh anonymous contexts; 1440x900, reduced motion,
loopback, no CPU/network throttling. The OS/server cache is warm. Browser
prefetch contributes to the measured resource set.

| Measurement | Actual result | Sample / interpretation |
| --- | --- | --- |
| Ready to source-backed workbench | 458, 391, 443, 421, 384 ms | Five fresh contexts, not five Internet cold starts; no tail claim. |
| Case selection | p50 49.8 ms; empirical p95 53.6 ms | 20 alternating selections, including Playwright/assertion overhead. |
| Linked fact/journal filter | p50 17.9 ms; empirical p95 21.5 ms | 20 changes with rendered related-row assertions. |
| JSON/HTML export | Both downloaded successfully | Two actual download durations in raw results; too few samples for percentiles. |
| First-visit task proxy | 540 ms | Deterministic mobile browser selects the incomplete case, asks what is unresolved and opens its reservation source. **Not human comprehension testing.** |

Resource body sizes are recorded per load (encoded-body bytes as reported by
Chromium; the local host does not demonstrate CDN compression). Do not call
these a gzip transfer budget. No p99 or production load-time guarantee is given.
All observed application traffic remained same-origin GET/HEAD; no inference
or financial request exists in this standalone build.

## Authenticated read-only case service

Run only with the approved local PostgreSQL selection:

```powershell
$env:PAYPAL_DISPOSABLE_CLUSTER='postgres18'
$env:PAYPAL_DISPOSABLE_DATASET='m2_demo'
$env:PYTHONPATH='backend'
.venv/Scripts/python.exe -B -m tools.release.read_benchmark
```

The harness independently verifies the cluster, compares all 135 financial
tables, obtains a requester session (never approver), checks the exact read-only
service and performs **45 GET case reads**, including one warmup. No response
bodies, bearer token or private row data are exported.

| Requests / concurrency | HTTP results | Successful p50 / empirical p95 | Successful throughput |
| --- | --- | --- | --- |
| 12 / 1 | 12 × 200 | 121.5 / 361.7 ms | 7.12/s |
| 16 / 4 | 16 × 200 | 231.3 / 273.7 ms | 16.39/s |
| 16 / 8 | 6 × 200, 10 × 429 | 397.7 / 565.8 ms | 9.35/s |

Warmup returned 200. Existing per-principal limits are 40 requests/minute and
four active case reads. The combined overload group exercises both controls;
the result cannot attribute every 429 to a particular one. No 5xx/transport
failure or identity mismatch occurred. All financial fingerprints matched after
the run. The selected case is genuine historical data; requests contact local
scoped services/SQL, never PayPal or the seller's external effects.

The dataset is tiny, all services share one machine, and sample groups have
only 12/16 observations (six successful overload samples). These are empirical
measurements, not reliable tail estimates. CPU/memory saturation was not
attributed or established. The burst proves bounded refusal, not maximum
throughput or distributed rate limiting. Do not repeatedly run this benchmark
to overwhelm the operator's development environment.

## Harness corrections

The first static filter benchmark used an exact implicit select label and
timed out. Source filters now have explicit accessible names. A later harness
guard incorrectly treated harmless Next.js HEAD prefetches as mutations; it now
accepts GET/HEAD while continuing to reject all external/mutating requests.
Those attempts are not counted as passing measurements. Public tests initially
matched a hidden comparison copy of a capture ID; their locators now require
visible evidence. No financial source or success state was changed to fix a test.

These are Directive 020 measurements, before the final UI refinement, not a new Directive 021 benchmark.
