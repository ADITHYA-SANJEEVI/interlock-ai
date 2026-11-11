# Failure, recovery and AI evidence

No new provider, financial mutation, grant, approval or migration was used to
generate these results. Current offline fixtures are synthetic and isolated.
Historical SQL results retain their original provenance; they were not rerun.

| Trigger | Safety behavior / actual evidence | Code and test reference | Recovery / gap |
| --- | --- | --- | --- |
| Expired/revoked or insufficient grant | Current offline preparation blocks without spending; exact expiry/conservation tests pass. | `hackathon_tests/test_commerce.py`: deterministic preview, changed revision, auth revocation tests; `purchase_planner.py` | Fresh legitimate issuer scope/terms and independent approval required; no renewal here. |
| Stale/self/duplicate exact approval | Historical SQL rejects missing/self/expired approvals; concurrent two-session duplicates cannot increase votes or spending. | `paypal_acceptance/test_approval_postgres.py`; `test_protected_postgres.py` | Historical 58-case SQL report linked below; not executed in this release. Never copy approval to another action. |
| Concurrent admission / duplicate create or capture | Historical atomic authority/provider-phase tests protect one logical effect and reservation conservation. | `paypal_acceptance/test_adversarial_postgres.py`, `test_protected_postgres.py` | Do not infer a global production concurrency guarantee from fixtures. Preserve original keys on uncertainty. |
| Revocation race at capture | Historical tests distinguish revocation winning from already committed capture retaining obligation. | `paypal_acceptance/test_capture_races_postgres.py` | No provider mutation or new race run here. |
| Lost response or unavailable/contradictory canonical evidence | Historical SQL tests retain unknown obligations, prevent new-identity retries and block conflicting fulfillment. | `paypal_acceptance/test_protected_postgres.py` lost response/mismatch/unavailable tests; `paypal_flow.py` | Investigate with original persisted identities and trustworthy evidence; no automatic release/retry. |
| APPROVED without capture | Genuine incomplete case shows no capture, booking or fulfillment; workbench says unverified, never failed/paid. | `investigation.ts`; new `investigation.test.cjs`; public browser tests | Current PayPal status remains unknown; no fresh provider read. |
| Missing accounting/fulfillment; COMPLETE run with open obligation | Offline case tests and genuine two-case comparison retain gaps. Completed capture still exposes separate dispatch exposure. | `test_commerce.py`: case/export and complete-reconciliation tests; `test_investigator.py`: reconciliation flags | No fabricated fact/posting/delivery; escalation remains operator review. |
| Cross-tenant / operation identity mismatch | Current offline scope/identity tests fail closed before conclusions/handoff. | `test_commerce.py`: cross-identity sources and role/tenant tests; `test_investigator.py`: foreign tool scope | Not a new penetration test of production tenancy; historical scoped SQL remains unchanged. |
| Arbitrary model tool / raw SQL / extra args | Current tests reject unknown financial tools, destinations, extra fields and foreign operation IDs. | `test_investigator.py`: financial-tools/foreign-scope test; `test_commerce.py`: escalation/mismatched args | Model instructions are not relied on for authorization. |
| Unsupported shopping objective | Native planner can stop after catalog with UNSUPPORTED; no forced substitute purchase. | `test_commerce.py`: unsupported objective test | One controlled product only; no general marketplace claim. |
| Model timeout / malformed response / budget exhaustion | Current offline tests stop with bounded error, without paid fallback, retry loop or financial effect. | `test_investigator.py`: timeout, HTTP failures, tool/turn budget; `test_commerce.py`: native failure/oversized batches | No fresh paid model failure induced. 90s server budget; individual transport calls bounded by remaining time. |
| Principal overload | Existing separate model/read semaphores and rate limits; current offline concurrency recovery tests pass; genuine bounded GET overload returns 429. | `test_commerce.py`: limits and concurrent capacity; `test_investigator.py`: principal limits; `read_benchmark.py` | Process-local counters, not distributed quotas. No mutation or PayPal load testing. |
| Public hash corruption / unavailable bundle | Existing fresh-browser hash test rejects tampered data with no synthetic/live fallback. | `judge.spec.ts`; `public-evidence.ts` | Bundle/manifest identify bytes, not an external certification authority. |
| Missing Studio source/page | Existing custom widget sets loading/noData/error; new recorded replay reports unavailable page/error rather than inventing report output. | `EvidenceStudio.tsx`, `RecordedStudio.tsx` | Native happy-path/filters tested; failure branches reviewed, not all fault-injected. Public Studio remains licence-restricted. |

Historical SQL reference:
[58 tests, zero failures/errors/skips, November 9](hackathon/evidence/20261109T222133790788Z-m2-postgres18-acceptance.json).
This is PostgreSQL with synthetic provider/model, **not 58 genuine payments**.
The frozen 707-case historical suite was not rerun during Directive 020.

## Genuine AI provenance and causality

The completed protected purchase used the earlier constrained proposal recorded
in the recorded completed-case source. The later genuine tool-selecting purchase
planner and seven-tool investigator are separate demonstrations. The public
workbench is deterministic source interpretation, not generated AI text.
Recorded Studio playback uses real prior tool results and the official SDK;
it does not issue fresh backend tools or inference. The original live private
agent implementation and genuine recorded traces remain available.

Current offline tests establish native model tool selection/feedback using
synthetic transport responses, not fresh model calls. Genuine prior traces
establish actual model requests. Neither alone establishes broad model quality,
production reliability or human comprehension. No extra paid inference was needed.
