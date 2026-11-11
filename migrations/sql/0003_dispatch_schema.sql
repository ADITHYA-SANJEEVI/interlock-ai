CREATE SCHEMA dispatch;
REVOKE ALL ON SCHEMA dispatch FROM PUBLIC;
ALTER TABLE authority.admitted_work ADD CONSTRAINT admitted_work_tenant_identity UNIQUE(tenant_id,id);
CREATE TABLE dispatch.service_credentials (
 id text PRIMARY KEY, token_hash text UNIQUE NOT NULL, revision bigint NOT NULL DEFAULT 1,
 active boolean NOT NULL DEFAULT true, expires_at timestamptz NOT NULL,
 CHECK(revision>0)
);
CREATE TABLE dispatch.service_scopes (
 credential_id text NOT NULL REFERENCES dispatch.service_credentials(id),
 tenant_id text NOT NULL REFERENCES public.tenants(id),
 verbs text[] NOT NULL CHECK(verbs<@ARRAY['DISPATCH','OBSERVE','CLAIM']::text[]),
 PRIMARY KEY(credential_id,tenant_id)
);
CREATE TABLE dispatch.observation_memberships (
 tenant_id text NOT NULL, principal text NOT NULL, active boolean NOT NULL DEFAULT true,
 PRIMARY KEY(tenant_id,principal),
 FOREIGN KEY(tenant_id,principal) REFERENCES authority.memberships(tenant_id,principal)
);
CREATE TABLE dispatch.replay_contracts (
 id text PRIMARY KEY, adapter text NOT NULL, version text NOT NULL,
 classification text NOT NULL CHECK(classification IN ('SAFE_SAME_KEY_REPLAY','READ_BEFORE_REPLAY','NEVER_AUTOMATIC_REPLAY')),
 verified boolean NOT NULL, retention_seconds int NOT NULL DEFAULT 0 CHECK(retention_seconds>=0),
 CHECK(NOT verified OR retention_seconds>0)
);
INSERT INTO dispatch.replay_contracts VALUES
 ('reference-create-v1','REFERENCE_SYNTHETIC','reference-dispatch-v1','NEVER_AUTOMATIC_REPLAY',false,0),
 ('reference-safe-v1','REFERENCE_SYNTHETIC','reference-dispatch-v1','SAFE_SAME_KEY_REPLAY',true,60),
 ('reference-read-v1','REFERENCE_SYNTHETIC','reference-dispatch-v1','READ_BEFORE_REPLAY',false,0),
 ('razorpay-create-v1','RAZORPAY_TEST','razorpay-test-pass0-v1','NEVER_AUTOMATIC_REPLAY',false,0);
CREATE TABLE dispatch.integrations (
 tenant_id text PRIMARY KEY REFERENCES public.tenants(id), integration text NOT NULL,
 adapter text NOT NULL CHECK(adapter IN ('REFERENCE_SYNTHETIC','RAZORPAY_TEST')),
 account_id text NOT NULL CHECK(account_id~'^[a-zA-Z0-9_-]+$'),
 replay_contract_id text NOT NULL REFERENCES dispatch.replay_contracts(id),
 real_mutations_enabled boolean NOT NULL DEFAULT false,
 CHECK(integration~'^[a-zA-Z0-9_-]+$')
);
CREATE TABLE dispatch.business_operations (
 id text PRIMARY KEY, tenant_id text NOT NULL, kind text NOT NULL CHECK(kind='CREATE_ORDER'),
 domain_root text NOT NULL, action_id text NOT NULL UNIQUE, evidence_id text NOT NULL,
 decision_id text NOT NULL, approval_id text, grant_id text NOT NULL,
 grant_version_id text NOT NULL, grant_epoch bigint NOT NULL, config_epoch bigint NOT NULL,
 reservation_id text NOT NULL UNIQUE, admitted_work_id text NOT NULL UNIQUE,
 requester text NOT NULL, integration text NOT NULL, adapter text NOT NULL,
 provider_mode text NOT NULL CHECK(provider_mode IN ('REFERENCE_SYNTHETIC','RAZORPAY_TEST')),
 account_id text NOT NULL, adapter_version text NOT NULL,
 target_kind text NOT NULL CHECK(target_kind='NEW_ORDER_BY_EXACT_RECEIPT'),
 operation_key text NOT NULL UNIQUE, amount bigint NOT NULL CHECK(amount>0),
 currency text NOT NULL CHECK(currency='INR'), seller text NOT NULL,
 action_digest text NOT NULL, evidence_digest text NOT NULL, body jsonb NOT NULL,
 request_digest text NOT NULL, replay_contract_id text NOT NULL REFERENCES dispatch.replay_contracts(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,domain_root) REFERENCES authority.purchase_intents(tenant_id,id),
 FOREIGN KEY(tenant_id,action_id) REFERENCES authority.action_revisions(tenant_id,id),
 FOREIGN KEY(tenant_id,evidence_id) REFERENCES authority.evidence_revisions(tenant_id,id),
 FOREIGN KEY(tenant_id,decision_id) REFERENCES authority.policy_decisions(tenant_id,id),
 FOREIGN KEY(tenant_id,approval_id) REFERENCES authority.approvals(tenant_id,id),
 FOREIGN KEY(tenant_id,grant_id,grant_version_id,grant_epoch) REFERENCES authority.grant_versions(tenant_id,grant_id,id,epoch),
 FOREIGN KEY(tenant_id,reservation_id) REFERENCES authority.reservations(tenant_id,id),
 FOREIGN KEY(tenant_id,requester) REFERENCES authority.memberships(tenant_id,principal),
 FOREIGN KEY(tenant_id,admitted_work_id) REFERENCES authority.admitted_work(tenant_id,id),
 CHECK(body->>'receipt'=operation_key AND (body->>'amount')::bigint=amount AND body->>'currency'=currency),
 CHECK(body->'notes'->>'interlock_operation'=operation_key AND body->'notes'->>'interlock_account'=account_id
       AND body->'notes'->>'interlock_integration'=integration),
 CHECK(adapter=provider_mode)
);
CREATE TABLE dispatch.operation_runtime (
 operation_id text PRIMARY KEY, tenant_id text NOT NULL,
 state text NOT NULL CHECK(state IN ('QUEUED','READY','DISPATCH_COMMITTED','OBSERVING','RESOLVED_EFFECT','RESOLVED_ZERO_EFFECT','CANCEL_UNSENT')),
 knowledge text NOT NULL CHECK(knowledge IN ('NOT_ATTEMPTED','UNKNOWN','KNOWN_PENDING','CONFIRMED_EFFECT','CONFIRMED_ZERO_EFFECT','BLOCKED')),
 attempt_count int NOT NULL DEFAULT 0 CHECK(attempt_count>=0), revision bigint NOT NULL DEFAULT 1,
 provider_order text, blocked_reason text, latest_observation_id text, updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(tenant_id,operation_id) REFERENCES dispatch.business_operations(tenant_id,id)
);
CREATE TABLE dispatch.outbox_items (
 id text PRIMARY KEY, tenant_id text NOT NULL, operation_id text NOT NULL,
 purpose text NOT NULL CHECK(purpose IN ('SEND','OBSERVE')), ready_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 claim_id text, credential_id text REFERENCES dispatch.service_credentials(id), lease_until timestamptz,
 completed boolean NOT NULL DEFAULT false, UNIQUE(operation_id,purpose),
 FOREIGN KEY(tenant_id,operation_id) REFERENCES dispatch.business_operations(tenant_id,id)
);
CREATE INDEX dispatch_ready ON dispatch.outbox_items(purpose,ready_at,lease_until) WHERE NOT completed;
CREATE TABLE dispatch.queue_claims (
 id text PRIMARY KEY, tenant_id text NOT NULL, operation_id text NOT NULL,
 credential_id text NOT NULL REFERENCES dispatch.service_credentials(id), purpose text NOT NULL,
 claimed_at timestamptz NOT NULL, lease_until timestamptz NOT NULL,
 FOREIGN KEY(tenant_id,operation_id) REFERENCES dispatch.business_operations(tenant_id,id)
);
CREATE TABLE dispatch.transport_attempts (
 id text PRIMARY KEY, tenant_id text NOT NULL, operation_id text NOT NULL,
 number int NOT NULL CHECK(number>0), committed_at timestamptz NOT NULL,
 request_digest text NOT NULL, operation_key text NOT NULL,
 UNIQUE(operation_id,number), UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,operation_id) REFERENCES dispatch.business_operations(tenant_id,id)
);
CREATE TABLE dispatch.attempt_runtime (
 attempt_id text PRIMARY KEY, tenant_id text NOT NULL,
 state text NOT NULL CHECK(state IN ('COMMITTED_TO_HANDOFF','RESPONSE_RECORDED','INTERRUPTED','TIMED_OUT')),
 recorded_at timestamptz, response_observation_id text,
 FOREIGN KEY(tenant_id,attempt_id) REFERENCES dispatch.transport_attempts(tenant_id,id)
);
CREATE TABLE dispatch.dispatch_records (
 id text PRIMARY KEY, tenant_id text NOT NULL, operation_id text NOT NULL,
 attempt_id text NOT NULL UNIQUE, attempt_number int NOT NULL, committed_at timestamptz NOT NULL,
 service_credential_id text NOT NULL REFERENCES dispatch.service_credentials(id), service_revision bigint NOT NULL,
 service_expires_at timestamptz NOT NULL, grant_id text NOT NULL, grant_version_id text NOT NULL, grant_epoch bigint NOT NULL,
 grant_expires_at timestamptz NOT NULL, config_epoch bigint NOT NULL, policy_version_id text NOT NULL,
 action_id text NOT NULL, evidence_id text NOT NULL, decision_id text NOT NULL, approval_id text,
 approval_expires_at timestamptz, reservation_id text NOT NULL,
 request_digest text NOT NULL, action_digest text NOT NULL, evidence_digest text NOT NULL,
 replay_contract_id text NOT NULL REFERENCES dispatch.replay_contracts(id),
 authorization_reason text NOT NULL CHECK(authorization_reason IN ('CURRENT_FIRST_HANDOFF','CURRENT_VERIFIED_SAME_KEY_REPLAY')),
 UNIQUE(operation_id,attempt_number), UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,operation_id) REFERENCES dispatch.business_operations(tenant_id,id),
 FOREIGN KEY(tenant_id,attempt_id) REFERENCES dispatch.transport_attempts(tenant_id,id),
 FOREIGN KEY(tenant_id,grant_id,grant_version_id,grant_epoch) REFERENCES authority.grant_versions(tenant_id,grant_id,id,epoch),
 FOREIGN KEY(tenant_id,action_id) REFERENCES authority.action_revisions(tenant_id,id),
 FOREIGN KEY(tenant_id,evidence_id) REFERENCES authority.evidence_revisions(tenant_id,id),
 FOREIGN KEY(tenant_id,decision_id) REFERENCES authority.policy_decisions(tenant_id,id),
 FOREIGN KEY(tenant_id,approval_id) REFERENCES authority.approvals(tenant_id,id),
 FOREIGN KEY(tenant_id,reservation_id) REFERENCES authority.reservations(tenant_id,id),
 FOREIGN KEY(tenant_id,policy_version_id) REFERENCES authority.policy_versions(tenant_id,id)
);
CREATE TABLE dispatch.reservation_exposure (
 reservation_id text PRIMARY KEY, tenant_id text NOT NULL,
 state text NOT NULL CHECK(state IN ('RESERVED_UNEXPOSED','EXPOSED_UNRESOLVED')),
 FOREIGN KEY(tenant_id,reservation_id) REFERENCES authority.reservations(tenant_id,id)
);
CREATE TABLE dispatch.checkout_exposures (
 id text PRIMARY KEY, tenant_id text NOT NULL, operation_id text NOT NULL UNIQUE,
 domain_root text NOT NULL, action_id text NOT NULL, evidence_id text NOT NULL,
 grant_id text NOT NULL, grant_version_id text NOT NULL, grant_epoch bigint NOT NULL,
 grant_expires_at timestamptz NOT NULL, config_epoch bigint NOT NULL, policy_version_id text NOT NULL,
 approval_id text, approval_expires_at timestamptz, reservation_id text NOT NULL,
 provider_order text NOT NULL, integration text NOT NULL, provider_mode text NOT NULL, account_id text NOT NULL,
 owner_principal text NOT NULL, amount bigint NOT NULL, currency text NOT NULL,
 first_disclosed_at timestamptz NOT NULL, instructions jsonb NOT NULL,
 UNIQUE(tenant_id,id), UNIQUE(integration,provider_mode,account_id,provider_order),
 FOREIGN KEY(tenant_id,domain_root) REFERENCES authority.purchase_intents(tenant_id,id),
 FOREIGN KEY(tenant_id,operation_id) REFERENCES dispatch.business_operations(tenant_id,id),
 FOREIGN KEY(tenant_id,action_id) REFERENCES authority.action_revisions(tenant_id,id),
 FOREIGN KEY(tenant_id,evidence_id) REFERENCES authority.evidence_revisions(tenant_id,id),
 FOREIGN KEY(tenant_id,grant_id,grant_version_id,grant_epoch) REFERENCES authority.grant_versions(tenant_id,grant_id,id,epoch),
 FOREIGN KEY(tenant_id,reservation_id) REFERENCES authority.reservations(tenant_id,id),
 FOREIGN KEY(tenant_id,owner_principal) REFERENCES authority.memberships(tenant_id,principal),
 FOREIGN KEY(tenant_id,approval_id) REFERENCES authority.approvals(tenant_id,id),
 FOREIGN KEY(tenant_id,policy_version_id) REFERENCES authority.policy_versions(tenant_id,id)
);
CREATE TABLE dispatch.recovery_obligations (
 id text PRIMARY KEY, tenant_id text NOT NULL, origin_kind text NOT NULL CHECK(origin_kind IN ('OPERATION','EXPOSURE')),
 operation_id text NOT NULL, exposure_id text, reservation_id text NOT NULL,
 duty_kind text NOT NULL CHECK(duty_kind='RESOLVE_EXTERNAL_EFFECT'),
 predicate text NOT NULL CHECK(predicate IN ('EXACT_ORDER_EFFECT','EXACT_EXPOSED_PAYMENT_STATUS')),
 read_role text NOT NULL CHECK(read_role='OBSERVE'), mutation_requires_current_authority boolean NOT NULL CHECK(mutation_requires_current_authority),
 replay_contract_id text NOT NULL REFERENCES dispatch.replay_contracts(id),
 evaluator_version text NOT NULL DEFAULT 'dispatch-duty-v1', created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(operation_id,origin_kind,duty_kind), UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,operation_id) REFERENCES dispatch.business_operations(tenant_id,id),
 FOREIGN KEY(tenant_id,exposure_id) REFERENCES dispatch.checkout_exposures(tenant_id,id),
 FOREIGN KEY(tenant_id,reservation_id) REFERENCES authority.reservations(tenant_id,id),
 CHECK((origin_kind='OPERATION' AND exposure_id IS NULL) OR (origin_kind='EXPOSURE' AND exposure_id IS NOT NULL))
);
CREATE TABLE dispatch.duty_runtime (
 obligation_id text PRIMARY KEY, tenant_id text NOT NULL,
 state text NOT NULL CHECK(state IN ('OPEN','OBSERVING','VERIFYING','DISCHARGED')),
 blocked_reason text, discharge_observation_id text, updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(tenant_id,obligation_id) REFERENCES dispatch.recovery_obligations(tenant_id,id),
 CHECK(state<>'DISCHARGED' OR discharge_observation_id IS NOT NULL)
);
CREATE TABLE dispatch.operation_observations (
 id text PRIMARY KEY, tenant_id text NOT NULL, operation_id text NOT NULL, attempt_id text,
 source text NOT NULL CHECK(source IN ('TRANSPORT_RESPONSE','CANONICAL_EXACT_READ','AMBIGUOUS_TRANSPORT')),
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp(), body jsonb NOT NULL,
 classification text NOT NULL CHECK(classification IN ('EXACT_EFFECT','EXACT_ZERO_EFFECT','INSUFFICIENT','AMBIGUOUS','CONFLICT')),
 UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,operation_id) REFERENCES dispatch.business_operations(tenant_id,id),
 FOREIGN KEY(tenant_id,attempt_id) REFERENCES dispatch.transport_attempts(tenant_id,id)
);
ALTER TABLE dispatch.operation_runtime ADD FOREIGN KEY(tenant_id,latest_observation_id) REFERENCES dispatch.operation_observations(tenant_id,id);
ALTER TABLE dispatch.duty_runtime ADD FOREIGN KEY(tenant_id,discharge_observation_id) REFERENCES dispatch.operation_observations(tenant_id,id);
ALTER TABLE dispatch.attempt_runtime ADD FOREIGN KEY(tenant_id,response_observation_id) REFERENCES dispatch.operation_observations(tenant_id,id);
CREATE TABLE dispatch.command_identities (
 id text PRIMARY KEY, tenant_id text NOT NULL, principal text NOT NULL,
 family text NOT NULL, idempotency_key text NOT NULL, request_hash text NOT NULL, result jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(tenant_id,principal,family,idempotency_key),
 FOREIGN KEY(tenant_id,principal) REFERENCES authority.memberships(tenant_id,principal)
);
CREATE TABLE dispatch.audit_events (
 id text PRIMARY KEY, tenant_id text NOT NULL, operation_id text NOT NULL,
 event text NOT NULL, reference_id text NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(tenant_id,operation_id) REFERENCES dispatch.business_operations(tenant_id,id)
);
CREATE FUNCTION dispatch.immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION 'immutable dispatch source' USING ERRCODE='42501'; END $$;
DO $$ DECLARE name text; BEGIN
 FOREACH name IN ARRAY ARRAY['business_operations','queue_claims','transport_attempts','dispatch_records','checkout_exposures',
 'recovery_obligations','operation_observations','command_identities','audit_events','replay_contracts'] LOOP
 EXECUTE format('CREATE TRIGGER immutable_source BEFORE UPDATE OR DELETE ON dispatch.%I FOR EACH ROW EXECUTE FUNCTION dispatch.immutable()',name);
 END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA dispatch FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA dispatch FROM PUBLIC;
