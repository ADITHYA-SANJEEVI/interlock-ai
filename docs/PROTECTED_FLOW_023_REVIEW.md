# Protected flow: skeptical source and evidence review

No financial workflow was executed for this review. The current private runtime
was exercised with a legitimate requester session and fixed read-only pages;
no approver impersonation, new model invocation or PayPal contact occurred.

## Trust, evidence and failure matrix

References are actual source paths/lines at the existing financial baseline.
SQL acceptance references below are historical synthetic-provider/model results,
not new tests against certified demo data and not real payments.

| Link | Implementation / principal boundary | Original evidence | Associated test / expected refusal |
| --- | --- | --- | --- |
| Human request and constrained proposal | backend/interlock/proposer.py:119; paypal_services.py:101. Requester scope is established before model result recording; model has no financial command client. | Genuine earlier constrained HTTP 200 proposal, 5,088ms, referenced by HACKATHON_GENUINE_PURCHASE.md. | hackathon_tests/test_proposer.py and test_paypal_flow.py: invalid schema/tool/beneficiary/destination cannot grant authority. Current transport tests use mocks. |
| Signed seller terms | paypal_services.py:453; paypal_migrations/sql/0021_prepare.sql:3. Seller signs canonical bytes; requester ingests exact tenant/merchant/USD/revision/expiry. | Completed case's original action/quote source IDs and canonical completion snapshot. | test_protected_postgres.py; signature/scope/amount mismatches reject. This quote cannot be renewed by interpreting model prose. |
| Deterministic policy | paypal_migrations/sql/0021_authority.sql:94; authority.command remains the financial kernel. USD purchase policy always requires independent approval. | Recorded SQL policy and exact action before admission. | test_protected_postgres.py and current test_commerce.py deterministic-preview negatives. Public preview is interpretation, not SQL admission. |
| Independent exact approval | paypal_kernel.py:95; frozen authority command. Runtime wrapper serializes/rejects duplicate exact approvals; original principal separation remains authoritative. | approval_38846da8-68b1-4c1e-b1ef-2ebb1dae9057, historically consumed. | test_approval_postgres.py: distinct command keys/same principal, two sessions/concurrent commands, same-key refresh and expiry. Historical results; no new human approval here. |
| Atomic USD reservation | paypal_kernel.py:27 command boundary; authority/dispatch SQL lock and conservation guards. Authenticated requester admits exact approved action. | reservation_2935ea91-d572-4657-a1c2-77014316cd69; original operation linkage. | test_adversarial_postgres.py and test_protected_postgres.py: over-budget, beneficiary, duplicate/concurrent admission fail closed. No implicit currency conversion. |
| Credential-isolated broker/order | paypal_services.py:291–321; paypal_flow.py:122; paypal_sandbox.py:257. Broker-only internal credentials and persisted CREATE phase commit precede network I/O. | Order 4FM556283T043144X; original CREATE request 2933db48-4859-405f-8341-a37be6ce7038. | Current test_paypal_flow.py and test_paypal_sandbox.py mock transports; historical SQL concurrency/identity tests. API/model/browser cannot select arbitrary provider destinations. |
| Buyer consent | paypal_services.py:200; paypal_sandbox.py:77. Human PayPal checkout and stored operation binding are distinct from settlement proof. | Human US buyer consent followed by independent historical APPROVED observation. | Missing/incorrect native order/merchant/amount rejects; redirects alone cannot establish capture. No checkout repeated here. |
| Separate capture authority | paypal_services.py:207; paypal_migrations/sql/0021_transport.sql:171,204; paypal_flow.py:113. Issuer authorization, current exact authority and SQL send cutoff required. | Explicit original human capture decision; CAPTURE identity 3fe8039e-1e9e-469c-88e7-e5e2999feefd. | test_capture_races_postgres.py, test_preflight_expiry_postgres.py: revocation/expiry before cutoff deny while commitments retain obligations. No fresh capture authorization here. |
| Canonical PayPal verification | paypal_flow.py:82,122; paypal_sandbox.py:119,260. Separate authenticated GET validates order/merchant/USD/amount and signed canonical envelope before facts. | Capture 6C4956371B715513Y, independently verified COMPLETED on November 10. | Current verify_order mismatch tests and historical contradictory/unavailable-evidence tests. An HTTP timeout becomes UNKNOWN, not success or a fresh-identity retry. |
| Facts and balanced booking | paypal_economic.py:15; paypal_migrations/sql/0023_economic_commit.sql:3,20. Separate economic role processes authenticated receipts and controlled posting templates. | Four economic facts, three balanced journal groups; original references preserved in completed-case.json. | Historical protected SQL economic assertions; public Node tests preserve original fact/journal identities. Separate economic effects are not additional spending. |
| Controlled seller fulfillment | paypal_economic.py:22; paypal_services.py:457. Exact committed capability reaches a fixed seller endpoint; signed seller result remains independent evidence. | Exactly one workspace-seat entitlement for alice in original completed result. | Historical contradictory/missing-capture cases block fulfillment. No seller provisioning/finalization called here. |
| Reconciliation and uncertainty | paypal_reconciliation.py:12; investigation_reads.py:66; operation_cases.py. Read-only inspection is separate from execute(), which records runs and was not called here. | Completed reconciliation plus separate retained legacy exposure; incomplete OPEN MUTATION_OUTCOME_UNKNOWN remains visible. | Historical unknown/revocation/contradiction acceptance; current investigator/commerce tests preserve uncertainty. Run COMPLETE is not a global all-resolved result. |
| Later native-tool AI and reporting | purchase_planner.py:281; investigator.py:122; investigation_reads.py:40; studio-analyst.ts:221; RecordedStudio.tsx. Fixed scoped READ ONLY tools, then an allowlisted report-state change. | Separate genuine four-tool planner and seven-tool investigator on November 11; not initiators of the November 10 completed payment. | Current 82 AI tests; native Studio test now checks actual changed provider-grid cells as well as host state. A report edit never writes financial truth. |

## Current legitimate runtime examination

Requester-authenticated headless review at localhost:3026 passed: four accessible
operations, completed/incomplete selection, correctly bound analytics links,
prefilled operation identity and an explicit read-only evidence load. No browser
command other than GET/HEAD and no page error occurred. No approver session was
used. All 135 financial table fingerprints matched before and after.

This proves current scoped read access and navigation, not fresh model execution,
fresh seller availability, valid expired grants, another payment or full portable
financial-stack bootstrap. The existing broker mutation gate remains untouched.

## Five classifications used consistently

- LIVE PRIVATE RUNTIME: current authenticated read pages and scoped source reads.
- HISTORICAL GENUINE PROVIDER EVIDENCE: original PayPal observations and capture.
- HISTORICAL GENUINE MODEL INFERENCE: original constrained proposal and later
  separate native-tool runs, with preserved time/tool/source identities.
- DETERMINISTIC SOURCE INTERPRETATION: public questions, comparisons and reports.
- SYNTHETIC TEST FIXTURE: mocked transports, isolated unit tests and historical
  SQL acceptance data; none is presented as a real provider event.

The incomplete order 5NV48695TN707743S remains capture-unverified, not FAILED.
Its capacity cannot be released merely because a browser returned or a model
described the gap. No financial remedy or new architecture is introduced here.
