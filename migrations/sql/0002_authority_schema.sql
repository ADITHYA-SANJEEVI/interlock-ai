CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA authority;
REVOKE ALL ON SCHEMA authority FROM PUBLIC;

CREATE TABLE authority.issuer_keys (name text PRIMARY KEY, secret text NOT NULL);
CREATE TABLE authority.memberships (
    principal text PRIMARY KEY REFERENCES public.identities(username),
    tenant_id text NOT NULL REFERENCES public.tenants(id),
    roles text[] NOT NULL,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    active boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, principal),
    CHECK (roles <@ ARRAY['PURCHASE_ISSUER','PURCHASE_REQUESTER','PURCHASE_APPROVER',
                         'REFUND_ISSUER','REFUND_REQUESTER','REFUND_APPROVER']::text[])
);
CREATE TABLE authority.tenant_control (
    tenant_id text NOT NULL REFERENCES public.tenants(id),
    kind text NOT NULL CHECK (kind IN ('PURCHASE','REFUND')),
    epoch bigint NOT NULL DEFAULT 1 CHECK (epoch > 0),
    policy_version_id text,
    paused boolean NOT NULL DEFAULT false,
    PRIMARY KEY (tenant_id,kind)
);
CREATE TABLE authority.policies (
    id text PRIMARY KEY, tenant_id text NOT NULL REFERENCES public.tenants(id),
    kind text NOT NULL CHECK (kind IN ('PURCHASE','REFUND')),
    name text NOT NULL, issuer text NOT NULL,
    UNIQUE (tenant_id,id), UNIQUE (tenant_id,kind),
    FOREIGN KEY (tenant_id,issuer) REFERENCES authority.memberships(tenant_id,principal)
);
CREATE TABLE authority.policy_versions (
    id text PRIMARY KEY, tenant_id text NOT NULL, policy_id text NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    approval_above bigint NOT NULL CHECK (approval_above >= 0),
    max_amount bigint NOT NULL CHECK (max_amount > 0),
    evaluator_version text NOT NULL CHECK (evaluator_version = 'authority-v1'),
    published_by text NOT NULL, published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_id,id), UNIQUE (policy_id,version),
    FOREIGN KEY (tenant_id,policy_id) REFERENCES authority.policies(tenant_id,id),
    FOREIGN KEY (tenant_id,published_by) REFERENCES authority.memberships(tenant_id,principal)
);
ALTER TABLE authority.tenant_control ADD FOREIGN KEY (tenant_id,policy_version_id)
    REFERENCES authority.policy_versions(tenant_id,id);

CREATE TABLE authority.grants (
    id text PRIMARY KEY, tenant_id text NOT NULL, issuer text NOT NULL, subject text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('PURCHASE','REFUND')),
    currency text NOT NULL CHECK (currency='INR'), lifetime_bound bigint NOT NULL CHECK (lifetime_bound > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE (tenant_id,id),
    FOREIGN KEY (tenant_id,issuer) REFERENCES authority.memberships(tenant_id,principal),
    FOREIGN KEY (tenant_id,subject) REFERENCES authority.memberships(tenant_id,principal)
);
CREATE INDEX grant_scope ON authority.grants(tenant_id,subject,kind);
CREATE TABLE authority.grant_versions (
    id text PRIMARY KEY, tenant_id text NOT NULL, grant_id text NOT NULL,
    version bigint NOT NULL CHECK (version > 0), epoch bigint NOT NULL CHECK (epoch > 0),
    seller text NOT NULL, integration text NOT NULL, beneficiaries text[] NOT NULL,
    resource_id text, effective_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_id,id), UNIQUE (grant_id,version),
    FOREIGN KEY (tenant_id,grant_id) REFERENCES authority.grants(tenant_id,id),
    CHECK (expires_at > effective_at)
);
CREATE TABLE authority.grant_heads (
    grant_id text PRIMARY KEY, tenant_id text NOT NULL, version_id text NOT NULL,
    revision bigint NOT NULL DEFAULT 1, epoch bigint NOT NULL DEFAULT 1,
    status text NOT NULL CHECK (status IN ('DRAFT','ACTIVE','REVOKED','EXPIRED')),
    bound bigint NOT NULL CHECK (bound > 0),
    consumed bigint NOT NULL DEFAULT 0 CHECK (consumed >= 0),
    reserved bigint NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    free bigint NOT NULL CHECK (free >= 0),
    UNIQUE (tenant_id,grant_id),
    FOREIGN KEY (tenant_id,grant_id) REFERENCES authority.grants(tenant_id,id),
    FOREIGN KEY (tenant_id,version_id) REFERENCES authority.grant_versions(tenant_id,id),
    CHECK (bound=consumed+reserved+free)
);
CREATE TABLE authority.seller_quotes (
    id text PRIMARY KEY, tenant_id text NOT NULL REFERENCES public.tenants(id),
    seller text NOT NULL, revision bigint NOT NULL CHECK (revision > 0),
    amount bigint NOT NULL CHECK (amount > 0), currency text NOT NULL CHECK (currency='INR'),
    expires_at timestamptz NOT NULL, body jsonb NOT NULL, body_digest text NOT NULL,
    provenance text NOT NULL CHECK (provenance='AUTHENTIC_SIGNED_CONTROLLED_SELLER'),
    UNIQUE (tenant_id,id)
);
CREATE TABLE authority.quote_heads (
    quote_id text PRIMARY KEY, tenant_id text NOT NULL, revision bigint NOT NULL,
    FOREIGN KEY (tenant_id,quote_id) REFERENCES authority.seller_quotes(tenant_id,id)
);
CREATE TABLE authority.refund_fixture_sources (
    id text PRIMARY KEY, tenant_id text NOT NULL REFERENCES public.tenants(id),
    integration text NOT NULL, amount bigint NOT NULL CHECK (amount > 0),
    revision bigint NOT NULL DEFAULT 1, provenance text NOT NULL CHECK (provenance='LOCAL_REFERENCE_ONLY'),
    UNIQUE (tenant_id,id)
);
CREATE TABLE authority.purchase_intents (
    id text PRIMARY KEY, tenant_id text NOT NULL, requester text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('PURCHASE','REFUND')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE (tenant_id,id),
    FOREIGN KEY (tenant_id,requester) REFERENCES authority.memberships(tenant_id,principal)
);
CREATE TABLE authority.intent_revisions (
    id text PRIMARY KEY, tenant_id text NOT NULL, intent_id text NOT NULL,
    revision bigint NOT NULL, terms jsonb NOT NULL, terms_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_id,id), UNIQUE (intent_id,revision),
    FOREIGN KEY (tenant_id,intent_id) REFERENCES authority.purchase_intents(tenant_id,id)
);
CREATE TABLE authority.intent_heads (
    intent_id text PRIMARY KEY, tenant_id text NOT NULL, revision bigint NOT NULL,
    intent_revision_id text NOT NULL, action_id text,
    status text NOT NULL CHECK (status IN ('PREPARED','ADMITTED')),
    FOREIGN KEY (tenant_id,intent_id) REFERENCES authority.purchase_intents(tenant_id,id),
    FOREIGN KEY (tenant_id,intent_revision_id) REFERENCES authority.intent_revisions(tenant_id,id)
);
CREATE TABLE authority.action_revisions (
    id text PRIMARY KEY, tenant_id text NOT NULL, intent_id text NOT NULL, intent_revision_id text NOT NULL,
    requester text NOT NULL, requester_revision bigint NOT NULL, kind text NOT NULL,
    grant_id text NOT NULL, amount bigint NOT NULL CHECK (amount > 0), currency text NOT NULL CHECK (currency='INR'),
    terms jsonb NOT NULL, request_digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_id,id), UNIQUE (intent_revision_id),
    FOREIGN KEY (tenant_id,intent_id) REFERENCES authority.purchase_intents(tenant_id,id),
    FOREIGN KEY (tenant_id,intent_revision_id) REFERENCES authority.intent_revisions(tenant_id,id),
    FOREIGN KEY (tenant_id,requester) REFERENCES authority.memberships(tenant_id,principal),
    FOREIGN KEY (tenant_id,grant_id) REFERENCES authority.grants(tenant_id,id),
    CHECK (kind IN ('PURCHASE','REFUND'))
);
ALTER TABLE authority.intent_heads ADD FOREIGN KEY (tenant_id,action_id)
    REFERENCES authority.action_revisions(tenant_id,id);
CREATE TABLE authority.evidence_revisions (
    id text PRIMARY KEY, tenant_id text NOT NULL, action_id text NOT NULL,
    grant_version_id text NOT NULL, grant_epoch bigint NOT NULL,
    config_epoch bigint NOT NULL, policy_version_id text NOT NULL,
    quote_id text, quote_revision bigint, fixture_id text, fixture_revision bigint,
    digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_id,id), UNIQUE(action_id),
    FOREIGN KEY (tenant_id,action_id) REFERENCES authority.action_revisions(tenant_id,id),
    FOREIGN KEY (tenant_id,grant_version_id) REFERENCES authority.grant_versions(tenant_id,id),
    FOREIGN KEY (tenant_id,policy_version_id) REFERENCES authority.policy_versions(tenant_id,id),
    FOREIGN KEY (tenant_id,quote_id) REFERENCES authority.seller_quotes(tenant_id,id),
    FOREIGN KEY (tenant_id,fixture_id) REFERENCES authority.refund_fixture_sources(tenant_id,id),
    CHECK ((quote_id IS NOT NULL AND fixture_id IS NULL AND quote_revision IS NOT NULL)
        OR (quote_id IS NULL AND fixture_id IS NOT NULL AND fixture_revision IS NOT NULL))
);
CREATE TABLE authority.evidence_members (
    tenant_id text NOT NULL, evidence_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('GRANT_VERSION','POLICY_VERSION','SELLER_QUOTE','REFUND_FIXTURE','ACTION','CONFIG')),
    source_id text NOT NULL, source_revision bigint NOT NULL,
    PRIMARY KEY (evidence_id,kind),
    FOREIGN KEY (tenant_id,evidence_id) REFERENCES authority.evidence_revisions(tenant_id,id)
);
CREATE TABLE authority.policy_decisions (
    id text PRIMARY KEY, tenant_id text NOT NULL, action_id text NOT NULL, evidence_id text NOT NULL,
    policy_version_id text NOT NULL, context_digest text NOT NULL,
    allowed boolean NOT NULL, approval_required boolean NOT NULL, reasons text[] NOT NULL,
    evaluator_version text NOT NULL CHECK (evaluator_version='authority-v1'),
    evaluated_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE (tenant_id,id), UNIQUE(action_id),
    FOREIGN KEY (tenant_id,action_id) REFERENCES authority.action_revisions(tenant_id,id),
    FOREIGN KEY (tenant_id,evidence_id) REFERENCES authority.evidence_revisions(tenant_id,id),
    FOREIGN KEY (tenant_id,policy_version_id) REFERENCES authority.policy_versions(tenant_id,id)
);
CREATE TABLE authority.approvals (
    id text PRIMARY KEY, tenant_id text NOT NULL, action_id text NOT NULL, evidence_id text NOT NULL,
    decision_id text NOT NULL, evidence_digest text NOT NULL,
    approver text NOT NULL, approver_revision bigint NOT NULL, expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE (tenant_id,id),
    FOREIGN KEY (tenant_id,action_id) REFERENCES authority.action_revisions(tenant_id,id),
    FOREIGN KEY (tenant_id,evidence_id) REFERENCES authority.evidence_revisions(tenant_id,id),
    FOREIGN KEY (tenant_id,decision_id) REFERENCES authority.policy_decisions(tenant_id,id),
    FOREIGN KEY (tenant_id,approver) REFERENCES authority.memberships(tenant_id,principal)
);
CREATE TABLE authority.approval_heads (
    approval_id text PRIMARY KEY, tenant_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('AVAILABLE','CONSUMED')), consumed_by text,
    FOREIGN KEY (tenant_id,approval_id) REFERENCES authority.approvals(tenant_id,id),
    CHECK ((status='AVAILABLE' AND consumed_by IS NULL) OR (status='CONSUMED' AND consumed_by IS NOT NULL))
);
CREATE TABLE authority.request_identities (
    id text PRIMARY KEY, tenant_id text NOT NULL, principal text NOT NULL,
    family text NOT NULL, idempotency_key text NOT NULL, request_hash text NOT NULL,
    result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_id,id), UNIQUE(tenant_id,principal,family,idempotency_key),
    FOREIGN KEY (tenant_id,principal) REFERENCES authority.memberships(tenant_id,principal)
);
CREATE TABLE authority.reservations (
    id text PRIMARY KEY, tenant_id text NOT NULL, grant_id text NOT NULL,
    grant_version_id text NOT NULL, epoch bigint NOT NULL,
    action_id text NOT NULL, intent_revision_id text NOT NULL, request_id text NOT NULL,
    amount bigint NOT NULL CHECK (amount > 0), currency text NOT NULL CHECK (currency='INR'),
    source_terms_digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_id,id), UNIQUE(action_id), UNIQUE(intent_revision_id),
    FOREIGN KEY (tenant_id,grant_id) REFERENCES authority.grants(tenant_id,id),
    FOREIGN KEY (tenant_id,grant_version_id) REFERENCES authority.grant_versions(tenant_id,id),
    FOREIGN KEY (tenant_id,action_id) REFERENCES authority.action_revisions(tenant_id,id),
    FOREIGN KEY (tenant_id,intent_revision_id) REFERENCES authority.intent_revisions(tenant_id,id),
    FOREIGN KEY (tenant_id,request_id) REFERENCES authority.request_identities(tenant_id,id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX reservation_grant ON authority.reservations(tenant_id,grant_id);
CREATE TABLE authority.reservation_heads (
    reservation_id text PRIMARY KEY, tenant_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('RESERVED','FIXTURE_CONSUMED','FIXTURE_RELEASED')),
    FOREIGN KEY (tenant_id,reservation_id) REFERENCES authority.reservations(tenant_id,id)
);
CREATE TABLE authority.authority_movements (
    id text PRIMARY KEY, tenant_id text NOT NULL, grant_id text NOT NULL,
    grant_version_id text NOT NULL, epoch bigint NOT NULL, reservation_id text NOT NULL,
    request_id text NOT NULL, kind text NOT NULL CHECK (kind IN ('RESERVE','FIXTURE_CONSUME','FIXTURE_RELEASE')),
    amount bigint NOT NULL CHECK (amount > 0), delta_consumed bigint NOT NULL,
    delta_reserved bigint NOT NULL, delta_free bigint NOT NULL, reason text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(reservation_id,kind),
    FOREIGN KEY (tenant_id,grant_id) REFERENCES authority.grants(tenant_id,id),
    FOREIGN KEY (tenant_id,grant_version_id) REFERENCES authority.grant_versions(tenant_id,id),
    FOREIGN KEY (tenant_id,reservation_id) REFERENCES authority.reservations(tenant_id,id),
    FOREIGN KEY (tenant_id,request_id) REFERENCES authority.request_identities(tenant_id,id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (delta_consumed+delta_reserved+delta_free=0),
    CHECK ((kind='RESERVE' AND delta_consumed=0 AND delta_reserved=amount AND delta_free=-amount)
        OR (kind='FIXTURE_CONSUME' AND delta_consumed=amount AND delta_reserved=-amount AND delta_free=0)
        OR (kind='FIXTURE_RELEASE' AND delta_consumed=0 AND delta_reserved=-amount AND delta_free=amount))
);
CREATE INDEX movements_grant ON authority.authority_movements(tenant_id,grant_id,occurred_at,id);
CREATE TABLE authority.admitted_work (
    id text PRIMARY KEY, tenant_id text NOT NULL, reservation_id text NOT NULL,
    action_id text NOT NULL, purpose text NOT NULL CHECK (purpose='LOCAL_ADMISSION_ONLY'),
    dispatch_allowed boolean NOT NULL DEFAULT false CHECK (dispatch_allowed=false),
    UNIQUE(action_id),
    FOREIGN KEY (tenant_id,reservation_id) REFERENCES authority.reservations(tenant_id,id),
    FOREIGN KEY (tenant_id,action_id) REFERENCES authority.action_revisions(tenant_id,id)
);
CREATE TABLE authority.audit_events (
    id text PRIMARY KEY, tenant_id text NOT NULL, principal text NOT NULL,
    request_id text NOT NULL, family text NOT NULL, result jsonb NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_id,principal) REFERENCES authority.memberships(tenant_id,principal),
    FOREIGN KEY (tenant_id,request_id) REFERENCES authority.request_identities(tenant_id,id) DEFERRABLE INITIALLY DEFERRED
);

CREATE FUNCTION authority.immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION 'immutable authority source' USING ERRCODE='42501'; END $$;
DO $$ DECLARE tbl text; BEGIN
    FOREACH tbl IN ARRAY ARRAY['grants','grant_versions','policies','policy_versions','seller_quotes',
        'purchase_intents','intent_revisions','action_revisions','evidence_revisions','evidence_members',
        'policy_decisions','approvals','request_identities','reservations','authority_movements','admitted_work','audit_events'] LOOP
        EXECUTE format('CREATE TRIGGER immutable_source BEFORE UPDATE OR DELETE ON authority.%I FOR EACH ROW EXECUTE FUNCTION authority.immutable()',tbl);
    END LOOP;
END $$;
CREATE FUNCTION authority.fixed_grant_head() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.grant_id<>OLD.grant_id OR NEW.tenant_id<>OLD.tenant_id OR NEW.bound<>OLD.bound
       OR (OLD.status IN ('REVOKED','EXPIRED') AND NEW.status<>OLD.status)
       OR NEW.epoch<OLD.epoch OR NEW.revision<OLD.revision THEN
        RAISE EXCEPTION 'fixed lifetime authority/terminal status' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER fixed_head BEFORE UPDATE ON authority.grant_heads FOR EACH ROW EXECUTE FUNCTION authority.fixed_grant_head();
REVOKE ALL ON ALL TABLES IN SCHEMA authority FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA authority FROM PUBLIC;

-- Deferred structural conservation: even a kernel defect cannot commit counter-only admission.
CREATE FUNCTION authority.check_bundle() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,authority,pg_temp AS $$
DECLARE g text:=NEW.grant_id; h authority.grant_heads; b bigint; dc bigint; dr bigint; df bigint;
BEGIN
    SELECT * INTO h FROM authority.grant_heads WHERE grant_id=g;
    SELECT lifetime_bound INTO b FROM authority.grants WHERE id=g;
    SELECT coalesce(sum(delta_consumed),0),coalesce(sum(delta_reserved),0),coalesce(sum(delta_free),0)
        INTO dc,dr,df FROM authority.authority_movements WHERE grant_id=g;
    IF h.grant_id IS NULL OR h.bound<>b OR h.consumed<>dc OR h.reserved<>dr OR h.free<>b+df THEN
        RAISE EXCEPTION 'authority movement/head reconstruction mismatch' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM authority.reservations r WHERE r.grant_id=g AND
        (NOT EXISTS(SELECT 1 FROM authority.authority_movements m WHERE m.reservation_id=r.id AND m.kind='RESERVE'
            AND m.amount=r.amount AND m.grant_id=r.grant_id AND m.grant_version_id=r.grant_version_id AND m.epoch=r.epoch AND m.request_id=r.request_id)
        OR NOT EXISTS(SELECT 1 FROM authority.admitted_work w WHERE w.reservation_id=r.id AND w.action_id=r.action_id)
        OR NOT EXISTS(SELECT 1 FROM authority.action_revisions a WHERE a.id=r.action_id AND a.amount=r.amount
            AND a.currency=r.currency AND a.request_digest=r.source_terms_digest AND a.intent_revision_id=r.intent_revision_id))) THEN
        RAISE EXCEPTION 'incomplete exact admission bundle' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER conservation_heads AFTER INSERT OR UPDATE ON authority.grant_heads
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION authority.check_bundle();
CREATE CONSTRAINT TRIGGER conservation_movements AFTER INSERT ON authority.authority_movements
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION authority.check_bundle();
CREATE CONSTRAINT TRIGGER conservation_reservations AFTER INSERT ON authority.reservations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION authority.check_bundle();
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA authority FROM PUBLIC;

-- Cross-source relationships retain both tenant and the exact owning grant/target.
ALTER TABLE authority.grant_versions ADD CONSTRAINT grant_version_owner UNIQUE(tenant_id,grant_id,id,epoch);
ALTER TABLE authority.grant_versions ADD CONSTRAINT grant_version_pointer UNIQUE(tenant_id,grant_id,id);
ALTER TABLE authority.grant_heads ADD CONSTRAINT head_version_owner FOREIGN KEY(tenant_id,grant_id,version_id)
    REFERENCES authority.grant_versions(tenant_id,grant_id,id);
ALTER TABLE authority.reservations ADD CONSTRAINT reservation_version_owner FOREIGN KEY(tenant_id,grant_id,grant_version_id,epoch)
    REFERENCES authority.grant_versions(tenant_id,grant_id,id,epoch);
ALTER TABLE authority.authority_movements ADD CONSTRAINT movement_version_owner FOREIGN KEY(tenant_id,grant_id,grant_version_id,epoch)
    REFERENCES authority.grant_versions(tenant_id,grant_id,id,epoch);
ALTER TABLE authority.approval_heads ADD CONSTRAINT approval_consumption_target FOREIGN KEY(tenant_id,consumed_by)
    REFERENCES authority.reservations(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
