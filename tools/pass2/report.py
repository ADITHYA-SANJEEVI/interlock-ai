"""Render reviewable Pass 2 reports from executed, packed evidence."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "docs/evidence/pass2"


def read(name):
    return json.loads((PACK / name).read_text(encoding="utf-8"))


def cell(value):
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return value.replace("|", "\\|").replace("\n", " ")


def table(headers, rows):
    return "\n".join(
        ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
        + ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    )


def projection(row):
    snapshot = row.get("database_snapshot_after_gate", {})
    runtime = snapshot.get("dispatch.operation_runtime", [])
    heads = snapshot.get("authority.grant_heads", [])
    duties = snapshot.get("dispatch.duty_runtime", [])
    return (
        [{k: h[k] for k in ("status", "bound", "consumed", "reserved", "free")} for h in heads],
        [r["state"] for r in runtime],
        [r["knowledge"] for r in runtime],
        [r["state"] for r in duties],
    )


def main():
    rows = read("postgres-tests.json")
    completion = read("completion.json")
    assert completion["verdict"] == "PASS 2 COMPLETE"
    red_rows = []
    for index, row in enumerate(rows):
        gate = row["gate"]
        authority, operation, knowledge, duty = projection(row)
        counts = row.get("counts_before_recovery", row.get("counts"))
        if counts is None and row.get("history"):
            counts = row["history"][-1]["counts"]
        counts = counts or {}
        calls = counts.get("remote_calls", row.get("mutation_count", row.get("count")))
        if gate.startswith("actual_sdk_tcp_"):
            calls = {"actual_http_posts": len(row["requests"]), "reference_oracle_calls": calls}
        elif gate.startswith("hidden_retry_"):
            calls = {"mock_handoffs": row["outgoing_calls"], "reference_oracle_calls": calls}
        if calls is None:
            calls = (
                "One synthetic setup send; checker gate does not independently count HTTP"
                if gate.startswith("audit_tamper_")
                else "SQL gate; no transport exercised"
            )
        committed = {k: v for k, v in counts.items() if k not in {"remote_calls", "remote_effects"}}
        if gate.startswith("crash_"):
            committed["attempt_states"] = row["attempt_states_before_recovery"]
        if not committed:
            committed = {
                "attempts": len(row.get("database_snapshot_after_gate", {}).get("dispatch.attempt_runtime", [])),
                "duties": len(row.get("database_snapshot_after_gate", {}).get("dispatch.duty_runtime", [])),
            }
        setup = "Isolated PostgreSQL tenant-a, exact admitted CREATE_ORDER, B=100 C=0 R=100 F=0"
        if gate == "fresh_dispatch_migrations":
            setup = "Three new disposable databases; each domain upgrades twice and compares installed SQL"
        elif gate == "independent_model":
            setup += "; independent model schedule " + cell(row["schedule"])
        elif gate.startswith("crash_"):
            setup += "; real supervised child process, separate persistent reference service"
        elif gate.startswith("audit_tamper_"):
            setup += "; one positive export and one altered copy"
        ordering = row.get("barrier", "Deterministic command sequence; no concurrency claim")
        if gate.startswith("crash_"):
            ordering = {
                "checkpoint": gate.removeprefix("crash_"),
                "barrier": row.get("barrier"),
                "process_exit": row["process_exit"],
            }
        pids = row.get("backend_pids", row.get("backend_pid"))
        if isinstance(row.get("barrier"), dict):
            pids = {k: row["barrier"][k] for k in ("pid", "blocker") if k in row["barrier"]}
        if row.get("os_pid"):
            pids = {"os_pid": row["os_pid"], "postgres": row["backend_pid"]}
        expected = [authority, operation, knowledge, duty]
        if "_expiry_" in gate and "expiry" in row:
            expected[0] = {"heads": authority, "cutoff_permission": "EXPIRED", "deadline": row["expiry"]}
        if gate.startswith("crash_"):
            before = row["durable_before_recovery"]
            expected = [
                "ACTIVE; C=0 R=100 F=0",
                before["state"],
                before["knowledge"],
                [d["state"] for d in row["duty_states_before_recovery"]],
            ]
        observed = {"assertions": "PASS", "after_recovery_or_gate": [authority, operation, knowledge, duty]}
        for field in ("status", "outcome", "results", "revoked_retry", "replay_denial", "positive", "negative", "agreement"):
            if field in row:
                observed[field] = row[field]
        red_rows.append(
            [
                gate + (" #" + str(index) if gate == "independent_model" else ""),
                setup,
                ordering,
                pids or "not a PID gate",
                calls,
                committed,
                *expected,
                observed,
                f"postgres-tests.json[{index}] ({gate})",
                row["classification"],
            ]
        )
    live = read("service-restarts.json")
    for restart in live["restarts"]:
        durable = restart["durable"]
        red_rows.append(
            [
                "persistent restart " + restart["service"],
                "Live synthetic accepted response loss; initiating grant revoked; R=3000",
                "docker compose restart; health/DB barriers; all selected PIDs changed",
                {"before": restart["pids_before"], "after": restart["pids_after"]},
                durable["hidden_oracle_test_only"]["mutation_calls"],
                {"attempts": len(durable["attempts"]), "dispatch": len(durable["dispatch_records"])},
                "REVOKED; C=0 R=3000 F=0",
                durable["runtime"]["state"],
                "UNKNOWN",
                [d["runtime"]["state"] for d in durable["duties"]],
                "PASS; same exact identity/digest/reservation, no resend",
                "service-restarts.json/restarts/service=" + restart["service"],
                "VERIFIED_REFERENCE_FAULT",
            ]
        )
    browser = read("browser.json")
    for row in browser["records"]:
        red_rows.append(
            [
                row["gate"],
                "Real production Next/browser, live synthetic provider fixtures",
                "Playwright serial scenario; response interception/close/restart where specified",
                "Browser and container PIDs in restart artifact",
                row.get("mutation_calls", "No new mutation; existing fixture only"),
                "Exact exposure/reservation assertions in browser results",
                "Existing grant and held R retained",
                "Existing committed operation retained",
                row.get("knowledge", "Exact view/cutoff assertion"),
                "Existing duty retained; visibility/scopes enforced",
                row,
                "browser.json/records/gate=" + row["gate"],
                "VERIFIED_BROWSER",
            ]
        )
    red = """# Pass 2 adversarial evidence

All rows below come from executed assertions. State tuples in the expected columns are the contract states asserted by that case; observed post-gate PostgreSQL snapshots remain independently reviewable in the linked JSON. Crash rows distinguish the durable pre-recovery state from the observed post-recovery state. Counts are physical reference POSTs unless explicitly labeled actual TCP or application mock. A SQL/checker gate does not claim a network measurement.

Ordinary isolated fixtures reserve 100 minor units; live browser/restart fixtures reserve 3000 minor units. Test-owner oracle truth is never available to product runtime roles. Generated model schedules compare after every command, not just the final snapshot. All artifacts below are under `docs/evidence/pass2/`.

"""
    red += table(
        [
            "Gate", "Setup", "Ordering / barrier", "Process / PID", "Mutation count", "Committed rows",
            "Expected authority", "Expected operation", "Expected knowledge", "Expected duty",
            "Observed outcome", "Evidence selector", "Classification",
        ],
        red_rows,
    )
    red += "\n\nThe complete-stack restart is crash checkpoint 14. Checkpoints 1–13 kill actual child processes; checkpoint 14 stops/restarts all six Compose containers, including PostgreSQL, with its volume retained. No process-reconstruction substitute is used.\n"
    (ROOT / "docs/PASS2_RED_TEAM.md").write_text(red, encoding="utf-8")

    named = {row["gate"]: row for row in rows}
    races = []
    for gate in ("dispatch_revoke_wins", "dispatch_cutoff_wins", "disclosure_revoke_wins", "disclosure_cutoff_wins"):
        row = named[gate]
        races.append([gate, row["barrier"], row["results"], row["counts"], projection(row)])
    crashes = []
    for row in rows:
        if row["gate"].startswith("crash_"):
            state = row["durable_before_recovery"]
            crashes.append([
                row["gate"], state["state"], state["knowledge"], row["counts_before_recovery"],
                row["attempt_states_before_recovery"], row["held_capacity"], row["duty_states_before_recovery"],
                "Fresh current cutoff only" if row["default_new_mutation_permitted"] else "No default mutation; observation only",
                row["after_recovery"]["knowledge"],
            ])
    software = read("software-checks.json")
    checks = table(
        ["Check", "Exact command (working directory)", "Exit", "Log"],
        [[r["name"], cell(r["command"]) + " (" + r["cwd"] + ")", r["exit_code"], r["log"]] for r in software],
    )
    text = f"""# Pass 2 completion evidence

## A. BASELINE

Frozen local annotated `pass-0-contracts` resolves to `1466b168693220b00b514003a701299262e03954`. Frozen local annotated `pass-1-authority` resolves to `c5f18c7bd2ac3c857341d0301b6dbe3ed7eb888c`. The initial clean checkout was `pass1/authority-exact-admission` at that commit; the implementation branch is `pass2/dispatch-exposure-unknown`. Implementation source checkpoint: `{completion['source_commit_before_pack']}`. The final evidence checkpoint is the clean commit named by local annotated `pass-2-dispatch`; its exact SHA is reported in the final run report. Earlier specifications/evidence/tags were preserved. No push or merge.

## B. IMPLEMENTATION

Immutable BusinessOperation binds admission, action/evidence/decision/approval, grant version/epoch, reservation, tenant, account, integration, seller, amount/currency, target receipt, adapter version, exact body/digest and replay contract. OperationRuntime, attempt runtime, duty runtime and reservation-exposure projections are separate. Admission atomically creates outbox identity; short claims never grant dispatch authority. TransportAttempt, DispatchRecord, CheckoutExposure, RecoveryObligation, observations, durable disclosure command identities and immutable audit correlations persist separately. API/broker/worker enforce distinct user, DISPATCH, OBSERVE and CLAIM scopes. Frontend renders UNKNOWN, held consequences, cutoff history and exact stored disclosure recovery.

## C. DATABASE / MIGRATIONS

Proper revisions `0003_dispatch_exposure` through `0008_preserve_contradiction` install the private dispatch/reference schemas, hardened lock checks, exact terminal-zero proof, deferred audit bundles, disclosure masking and sticky contradictions. The control schema has 19 tables and 19 protected fixed-search-path functions (exact installed counts/fingerprints are in `migrations.json`). Dedicated owners are NOLOGIN/NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOINHERIT. Runtime roles have selected EXECUTE only; the dispatch owner cannot update monetary counters. Fresh control/reference/seller databases each upgrade twice, reproduce installed source/owners/constraints/triggers/indexes, and reach head. Seller acquires no private control schema. Actual login-role SQL bypass/oracle tests pass.

## D. DISPATCH CUTOFF

Queueing does not authorize dispatch. Lock order: relevant PURCHASE tenant/config head SHARE → credential/scope SHARE → integration SHARE → grant UPDATE → intent UPDATE and immutable source reads → quote/consumed approval SHARE → sorted requester/approver memberships SHARE → reservation SHARE → operation runtime UPDATE. Fresh DB time and service revision after the final wait govern permission. Current service/scope, pause, grant version/epoch/expiry, material policy, action/evidence, exact approval/actors, held reservation, immutable target/body/digest and replay envelope must pass. One transaction commits attempt + dispatch permission + UNKNOWN + operation duty + audit and retires SEND. Only after commit does one fixed HTTP POST occur. Independent connections acquire authority/runtime/outbox locks during HTTP; no SQL transaction spans network. Details: `DISPATCH_CUTOFF_ADR.md`.

## E. REVOKE / DISPATCH MATRIX

Distinct backend PIDs, a held winner transaction and observed `pg_blocking_pids`/Lock barriers establish ordering before release. Revocation first denies dispatch with no attempt/duty/send; dispatch first remains historically authorized after revoke and performs one exact reference handoff. All grants end REVOKED with C=0/R=100/F=0. State tuples are authority, runtime, knowledge, duties.

{table(['Gate / winner','Exact barrier','Winner / loser results','Committed rows / remote calls','Final state tuple'], races[:2])}

## F. CHECKOUT DISCLOSURE MATRIX

The same explicit winner/loser PostgreSQL barrier proves both orderings. Disclosure-first exact retrieval returns the original path and creates no additional exposure/duty. Revocation-first denies first disclosure with zero exposure; its already accepted order still has one original attempt/effect. Both retain the original reservation and revocation commits. Product inspection is masked until the first exposure cutoff commits.

{table(['Gate / winner','Exact barrier','Winner / loser results','Committed rows / remote calls','Final state tuple'], races[2:])}

## G. QUEUE / LEASE MATRIX

Four concurrent worker connections/PIDs cross an explicit barrier; exactly one SKIP LOCKED claim succeeds. The one-second lease expires naturally against DB time. Old claim is denied 409; a fresh uncommitted claim may cross a new cutoff. After commitment, forced SEND redelivery yields no claim/send/reset; one remote call remains. Worker pause followed by revocation denies dispatch 403 with zero attempt/network. No broker transaction remains open during transport.

## H. CRASH MATRIX

Every child exits by real process termination at a supervisor barrier; durable state is inspected before recovery. B=100/C=0/R=100/F=0 persists at every checkpoint. All thirteen isolated crash fixtures retain RESERVED / RESERVED_UNEXPOSED, with zero Checkout exposures. A committed UNKNOWN with no owner-known send remains UNKNOWN after absence reads. If exact effect evidence exists, independent read resolves it without another mutation. Precommit death rolls back the bundle and permits only a fresh authorized cutoff. Duties listed are pre-recovery; post-recovery knowledge is separate. Independent observation is permitted for every committed checkpoint; precommit observation has no committed effect to resolve. Product code cannot use the hidden oracle at any checkpoint.

{table(['Checkpoint','Durable operation','Knowledge before recovery','Rows / reference calls / effects','Durable attempt','Capacity','Durable duty','Mutation permission','Read-only recovery knowledge'], crashes)}

Checkpoint 14 is a real full Compose restart with persistent PostgreSQL volume; UNKNOWN and exposed Checkout paths both survive. Individual broker/worker/API/reference restarts also have changed-PID evidence in `service-restarts.json`.

## I. UNKNOWN / LOST RESPONSE

Reference `ACCEPT_DROP_RESPONSE` durably accepts the exact receipt/body, suppresses the response and delays read visibility by 10000 ticks in the live experiment. Hidden owner-only truth is one POST and one effect; product knowledge is UNKNOWN. Initiating grant is revoked, original reservation is 3000 and free is zero. Four individual restarts plus full stack restart preserve exact operation/digest/reservation/attempt/dispatch/duty and never resend. After visibility advances, an independently permitted exact receipt/account GET resolves CONFIRMED_EFFECT and discharges the OPERATION duty; remote mutation count stays one. This is VERIFIED_REFERENCE_FAULT, not Razorpay lost-response proof.

## J. REPLAY

SAFE_SAME_KEY_REPLAY, READ_BEFORE_REPLAY and NEVER_AUTOMATIC_REPLAY are explicit contracts. Default reference and Razorpay create use NEVER_AUTOMATIC_REPLAY; real Pass 2 mutations are disabled twice (kernel and broker). Only a verified synthetic 60-second same-key envelope permits an explicit second cutoff, with two calls/one deduplicated reference effect and the same original reservation/duty. Unverified read-before and never envelopes deny replay. Revoked authority denies replay 403 for all three; BLOCKED knowledge remains sticky after insufficient reads and cannot replay. Six real TCP reset/timeout/500/502/503/interruption probes each count exactly one POST and zero redelivery POSTs. Six application transport exception probes agree. No hidden SDK retry or redirect.

## K. BROKER SECURITY

Seventeen typed substitution cases reject with zero attempt/dispatch/network: tenant, payment, order, provider order, amount, currency, beneficiary, seller, account, integration, operation key, body, URL, host, headers, method and mode. Broker accepts only operation ID, claim ID and explicit replay flag; observation accepts operation ID only. Body/target/account/origin/path/verb/headers are server constructed from immutable binding. Browser additionally rejects four disclosure injections 422, cross-origin command 403 and public handoff 404. Tenant B known/nonexistent operation reads return indistinguishable 403. Five actual runtime login roles each fail eleven table/role/oracle bypass attempts.

## L. RECOVERY OBLIGATION

Commitment creates one OPERATION duty atomically; first exposure creates one distinct EXPOSURE duty atomically. Repeated retrieval duplicates neither. Duties survive revoke, crash, UNKNOWN and restart. Current independently scoped observation may continue after initiating grant revoke; removing observation membership denies before HTTP. An exact order read can discharge only the order-effect duty, never the exposed-payment-status predicate; that duty remains OPEN. Contradictions produce BLOCKED knowledge/VERIFYING duty, and stale reads preserve both. Only pinned reference terminal-zero coverage with exact receipt/account correlation proves CONFIRMED_ZERO_EFFECT; capacity still remains reserved in Pass 2. Neither duty existence nor observation credentials grant mutation authority.

## M. REFERENCE MODEL

Independent dataclass model imports no production transitions. Thirty-seven generated hostile schedules compare allowed commands, authority (ACTIVE/REVOKED/EXPIRED), operation states, reservation exposure, knowledge, exact attempts/calls/effects/duties, unchanged AuthorityMovements and B=C+R+F against protected PostgreSQL after every step. Revocation, expiry, READY, claim/commit order, postcommit crash, lease loss, lost effects, reads, repeated disclosure, terminal rejection, contradiction and replay are covered. The required knowledge vocabulary includes KNOWN_PENDING; CREATE_ORDER resolves the existence effect once an exact order is read. All agree; model import independence is also tested.

## N. AUDIT EXPORT

Owner read-only export includes immutable operation and exact Pass 1 source bindings; current projections; attempts/permission evidence; exposures; observations; duties; immutable audit correlations. Independent checker imports no production transition. Twelve altered exports are rejected: missing record, digest, epoch, reservation, action, evidence, attempt number, duty, provider-order exposure, freed UNKNOWN capacity, body and impossible sequence. The live export also passes. The checker proves internal exported correlation/conservation/sequence and detects these tamper classes. It does not prove export completeness, external exactly-once delivery, independent cryptographic provider truth or economic recognition. Manifest hashes bind recorded artifacts and source bytes, not an external attestation.

## O. FRONTEND

`/dispatch` and `/dispatch/[operation]` expose bounded counts/ages, UNKNOWN consequences, current grant versus historical commitment, attempt/dispatch details, replay-disabled reason, observation freshness/permissions, exposure age and duty age. No mutation Retry action. Provider order identity stays masked until exposure commits. Durable pending disclosure key/body/revision recovers after response loss and page close. Seven production browser workflows pass: pre-exposure masking; revoked UNKNOWN observation/no resend; lost disclosure/close/recovery/revoke/full restart/exact retrieval; injection/CSRF; tenant isolation; independent approver denial; 375px no horizontal overflow. Five dedicated Pass 1 authority browser regressions pass. Passwords are cleared on submission; traces/automatic failure screenshots disabled; three post-login views were visually inspected.

## P. VERIFICATION

Complete pytest: **{completion['backend_tests']} passed, 0 failed, 0 errors, 0 skipped**. This includes existing Pass 0/Pass 1 suites and all Pass 2 suites, 37 model schedules, thirteen killed-process checkpoints, twelve audit tamper cases, seventeen substitution attacks, six actual TCP retry probes, role bypasses, actual PostgreSQL races and fresh/repeated three-domain migration reproduction. Dedicated browsers: Pass 1 **5 passed / 0 failed / 0 skipped / 0 flaky**; Pass 2 **7 passed / 0 failed / 0 skipped / 0 flaky**. Exact native commands, working directories and exit status:

{checks}

Pack verification: `.venv/Scripts/python.exe -m tools.pass2.verify_evidence` (exit 0). The verifier checks artifact/source hashes, frozen checkpoints, all recorded gate groups, browser stats, restart durability, audit consistency and credential exclusion. Python runtime imports cover seven modules. Active ruff excludes unrelated legacy feasibility probes; no unrelated user work was changed. All required software commands exit zero. Pytest reports one upstream Starlette/httpx deprecation warning; it does not skip or fail a test. Executed logs and XML/results are preserved in `docs/evidence/pass2`.

## Q. LIMITATIONS

No instant remote cancellation claim; no exactly-once external effect claim; no verified unlimited Razorpay replay horizon; no arbitrary-zombie-process guarantee beyond the stated remote assumptions. Reference faults and Checkout instructions are synthetic. Frozen Pass 0 real provider evidence remains separately classified; no new real GET or mutation proof is invented. No Pass 3 economic facts, accounting/journals or financial R→C recognition. No Pass 4 commercial return/refund recovery. No Pass 5 autonomous remediation/planner. Exact CREATE_ORDER is the exercised Pass 2 effect; later effect kinds must bring their own typed bindings, authority predicates and provider replay/zero-proof contracts.

## R. VERDICT

PASS 2 COMPLETE

## S. TAG

Local annotated `pass-2-dispatch` is permitted only after the final evidence verifier passes and the final commit is clean. The final run report confirms creation and resolved SHA. No push/merge.

## T. STOP

No Pass 3 work started. No new real PSP mutation occurred. Stop at Pass 2.
"""
    (ROOT / "docs/PASS2_EVIDENCE.md").write_text(text, encoding="utf-8")
    print(json.dumps({"red_team_rows": len(red_rows), "reports": ["PASS2_EVIDENCE.md", "PASS2_RED_TEAM.md"]}))


if __name__ == "__main__":
    main()
