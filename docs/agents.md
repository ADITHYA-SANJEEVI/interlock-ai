# Agent contracts and evidence

The model chooses native typed tool calls. The server validates arguments and
scope before execution. Fixed identity preflight and deterministic result
assembly are not counted as model-selected tools.

| Workflow | Tools | Bounds |
| --- | --- | --- |
| Planner | inspect_products, inspect_commercial_terms, inspect_spending_authority, preview_purchase, finish_purchase_plan | Six turns, ten tools, 90 seconds |
| Investigator | inspect_authority, inspect_provider, inspect_economic_facts, inspect_ledger, inspect_fulfillment, reconcile_records, select_studio_view, finish_investigation | Six turns, twelve tools, 90 seconds |

Source: backend/interlock/purchase_planner.py, investigator.py,
investigation_reads.py, commerce_api.py, investigation_api.py and
web/app/paypal/studio-analyst.ts.

The planner fixes the controlled product, merchant and USD scope. Beneficiary
and maximum must match the user. Its commercial tool reads authenticated
historical terms, not live availability. The exact draft is assembled from
returned sources. Preview is preparation, not formal SQL admission; a copied
draft cannot authorize spending.

The investigator uses fixed scoped endpoints and READ ONLY SQL. Unknown tools,
raw SQL/URLs, foreign operations and extra arguments fail. Its conclusion must
cite returned finding IDs. Model or source text cannot confer financial powers.

The shared configurable native-tool transport bounds requests by remaining
execution time. Tool/turn budgets, rate limits and concurrency slots constrain
execution. A bounded schema repair does not create a financial effect. No
silent paid fallback exists.

## Genuine and recorded

The completed purchase used an earlier constrained proposal. The later genuine
four-tool planner and seven-tool investigator are separate executions. Their
public traces retain model, timing, arguments, returned findings and source IDs.
The later planner did not generate the earlier payment.

Public questions are deterministic source interpretations, not generated AI
answers. Local Studio playback applies earlier model choices through the real
Agent Framework without fresh inference or backend tool execution. The original
private live investigator remains implemented separately.

Offline tests in hackathon_tests/test_commerce.py and test_investigator.py cover
alternate tool order, feedback, unsupported objectives, scope/argument rejection,
changed identity/authority, ungrounded citations and bounded failures. Their
transports are synthetic. Genuine earlier traces independently establish real
model requests; neither proves broad model quality or production reliability.
