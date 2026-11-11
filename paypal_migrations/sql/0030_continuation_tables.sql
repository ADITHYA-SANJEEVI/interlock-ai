-- All v1 functions, rows, expiries and provider identities remain unchanged.
CREATE SCHEMA paypal_v2;
REVOKE ALL ON SCHEMA paypal_v2 FROM PUBLIC;
CREATE TABLE paypal_v2.deployment (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), enabled boolean NOT NULL DEFAULT false
);
INSERT INTO paypal_v2.deployment DEFAULT VALUES;
CREATE TABLE paypal_v2.intents (
 id uuid PRIMARY KEY, operation_id text NOT NULL UNIQUE REFERENCES paypal_v1.bindings(operation_id),
 requester text NOT NULL REFERENCES authority.memberships(principal), source_revision bigint NOT NULL,
 request_raw text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE paypal_v2.continuations (
 id uuid PRIMARY KEY, operation_id text NOT NULL UNIQUE REFERENCES paypal_v1.bindings(operation_id),
 tenant_id text NOT NULL REFERENCES paypal_v1.tenants(tenant_id),
 requester text NOT NULL REFERENCES authority.memberships(principal), requester_revision bigint NOT NULL,
 attestation_raw text NOT NULL, attestation_signature text NOT NULL,
 digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
 policy_evaluation jsonb NOT NULL,
 provider_order_id text NOT NULL, capture_request_id uuid NOT NULL,
 reservation_id text NOT NULL UNIQUE REFERENCES authority.reservations(id),
 merchant_id text NOT NULL CHECK(merchant_id='JB7TR6VHB4GTQ'),
 amount_minor bigint NOT NULL CHECK(amount_minor=100), currency text NOT NULL CHECK(currency='USD'),
 expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE paypal_v2.approvals (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), continuation_id uuid NOT NULL UNIQUE REFERENCES paypal_v2.continuations(id),
 approver text NOT NULL REFERENCES authority.memberships(principal), approver_revision bigint NOT NULL,
 digest text NOT NULL, expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE paypal_v2.capture_links (
 capture_authorization_id uuid PRIMARY KEY REFERENCES paypal_v1.capture_requests(id),
 continuation_id uuid NOT NULL UNIQUE REFERENCES paypal_v2.continuations(id),
 approval_id uuid NOT NULL UNIQUE REFERENCES paypal_v2.approvals(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE paypal_v2.revocations (
 continuation_id uuid PRIMARY KEY REFERENCES paypal_v2.continuations(id),
 principal text NOT NULL REFERENCES authority.memberships(principal), created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE paypal_v2.commands (
 tenant_id text NOT NULL, principal text NOT NULL, family text NOT NULL, idem text NOT NULL,
 body_hash text NOT NULL, result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,principal,family,idem)
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['intents','continuations','approvals','capture_links','revocations','commands'] LOOP
  EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON paypal_v2.%I FOR EACH ROW EXECUTE FUNCTION authority.immutable()',t);
 END LOOP;
END $$;
