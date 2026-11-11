CREATE TABLE paypal_v1.bindings (
 operation_id text PRIMARY KEY REFERENCES dispatch.business_operations(id),
 tenant_id text NOT NULL REFERENCES paypal_v1.tenants(tenant_id),
 merchant_id text NOT NULL CHECK(merchant_id='JB7TR6VHB4GTQ'),
 amount_minor bigint NOT NULL CHECK(amount_minor>0), currency text NOT NULL CHECK(currency='USD'),
 create_request_id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
 capture_request_id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
 wire_body jsonb NOT NULL, wire_digest text NOT NULL,
 CHECK(create_request_id<>capture_request_id), CHECK(dispatch.hash(wire_body)=wire_digest)
);
CREATE TABLE paypal_v1.runtime (
 operation_id text PRIMARY KEY REFERENCES paypal_v1.bindings(operation_id),
 provider_order_id text UNIQUE CHECK(provider_order_id ~ '^[A-Z0-9]{10,32}$'),
 capture_id text UNIQUE CHECK(capture_id ~ '^[A-Z0-9]{10,32}$'),
 provider_status text, capture_confirmed boolean NOT NULL DEFAULT false,
 blocked boolean NOT NULL DEFAULT false, blocked_reason text,
 latest_receipt_id uuid REFERENCES economic.receipts(id), latest_read_at timestamptz,
 revision bigint NOT NULL DEFAULT 1 CHECK(revision>0),
 CHECK(NOT capture_confirmed OR (capture_id IS NOT NULL AND provider_status='COMPLETED'))
);
CREATE TABLE paypal_v1.capture_requests (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 operation_id text NOT NULL UNIQUE REFERENCES paypal_v1.bindings(operation_id),
 principal text NOT NULL REFERENCES authority.memberships(principal),
 principal_revision bigint NOT NULL CHECK(principal_revision>0),
 idempotency_key text NOT NULL, request_hash text NOT NULL,
 action_id text NOT NULL REFERENCES authority.action_revisions(id),
 approval_id text NOT NULL REFERENCES authority.approvals(id),
 reservation_id text NOT NULL UNIQUE REFERENCES authority.reservations(id),
 provider_order_id text NOT NULL, merchant_id text NOT NULL,
 amount_minor bigint NOT NULL CHECK(amount_minor>0), currency text NOT NULL CHECK(currency='USD'),
 requested_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE paypal_v1.phase_commits (
 operation_id text NOT NULL REFERENCES paypal_v1.bindings(operation_id),
 phase text NOT NULL CHECK(phase IN ('CREATE','CAPTURE')),
 request_id uuid NOT NULL UNIQUE, wire_digest text NOT NULL,
 capture_request_id uuid REFERENCES paypal_v1.capture_requests(id),
 provider_order_id text, service_credential_id text NOT NULL REFERENCES dispatch.service_credentials(id),
 permission jsonb NOT NULL, committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(operation_id,phase), CHECK((phase='CAPTURE')=(capture_request_id IS NOT NULL)),
 CHECK((phase='CAPTURE')=(provider_order_id IS NOT NULL))
);
CREATE TABLE paypal_v1.events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), operation_id text NOT NULL REFERENCES paypal_v1.bindings(operation_id),
 kind text NOT NULL, receipt_id uuid REFERENCES economic.receipts(id),
 occurred_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE paypal_v1.transport_diagnostics (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), operation_id text NOT NULL REFERENCES paypal_v1.bindings(operation_id),
 kind text NOT NULL CHECK(kind IN ('READ_UNAVAILABLE','MUTATION_OUTCOME_UNKNOWN','CANONICAL_BINDING_CONFLICT','COMMITTED_BINDING_CONFLICT')),
 evidence jsonb NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION paypal_v1.fixed_runtime() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
BEGIN
 IF NEW.operation_id<>OLD.operation_id OR OLD.blocked AND NOT NEW.blocked
 OR OLD.provider_order_id IS NOT NULL AND NEW.provider_order_id IS DISTINCT FROM OLD.provider_order_id
 OR OLD.capture_id IS NOT NULL AND NEW.capture_id IS DISTINCT FROM OLD.capture_id
 OR OLD.capture_confirmed AND NOT NEW.capture_confirmed OR NEW.revision<=OLD.revision THEN
  PERFORM authority.fail('IMMUTABLE_PAYPAL_IDENTITY_OR_CONFIRMED_HISTORY'); END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER fixed_runtime BEFORE UPDATE ON paypal_v1.runtime
 FOR EACH ROW EXECUTE FUNCTION paypal_v1.fixed_runtime();
CREATE FUNCTION paypal_v1.bundle_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE b paypal_v1.bindings; o dispatch.business_operations; r paypal_v1.runtime; BEGIN
 SELECT * INTO b FROM paypal_v1.bindings WHERE operation_id=NEW.operation_id;
 SELECT * INTO o FROM dispatch.business_operations WHERE id=NEW.operation_id;
 SELECT * INTO r FROM paypal_v1.runtime WHERE operation_id=NEW.operation_id;
 IF b.operation_id IS NULL OR r.operation_id IS NULL OR b.merchant_id<>o.account_id OR b.currency<>o.currency
 OR b.amount_minor<>o.amount OR b.wire_body IS DISTINCT FROM paypal_v1.wire(o) THEN
  PERFORM authority.fail('INCOMPLETE_PAYPAL_BINDING_BUNDLE'); END IF;
 IF EXISTS(SELECT 1 FROM paypal_v1.phase_commits pc WHERE pc.operation_id=o.id AND
  (pc.request_id<>CASE pc.phase WHEN 'CREATE' THEN b.create_request_id ELSE b.capture_request_id END
  OR pc.wire_digest<>CASE pc.phase WHEN 'CREATE' THEN b.wire_digest ELSE dispatch.hash('{}'::jsonb) END
  OR pc.phase='CAPTURE' AND NOT EXISTS(SELECT 1 FROM paypal_v1.capture_requests cr
   WHERE cr.id=pc.capture_request_id AND cr.operation_id=o.id AND cr.action_id=o.action_id
   AND cr.approval_id=o.approval_id AND cr.reservation_id=o.reservation_id AND cr.currency=o.currency
   AND cr.amount_minor=o.amount AND cr.merchant_id=o.account_id AND cr.provider_order_id=pc.provider_order_id
   AND pc.provider_order_id=r.provider_order_id))) THEN
  PERFORM authority.fail('INCOMPLETE_PAYPAL_PHASE_AUTHORITY_BUNDLE'); END IF;
 IF EXISTS(SELECT 1 FROM paypal_v1.phase_commits WHERE operation_id=o.id) AND NOT EXISTS(
  SELECT 1 FROM dispatch.recovery_obligations d JOIN dispatch.duty_runtime dr ON dr.obligation_id=d.id
  WHERE d.operation_id=o.id AND d.reservation_id=o.reservation_id AND d.origin_kind='OPERATION') THEN
  PERFORM authority.fail('PAYPAL_COMMITTED_OBLIGATION_REQUIRED'); END IF;
 IF r.capture_confirmed AND (NOT EXISTS(SELECT 1 FROM paypal_v1.phase_commits pc WHERE pc.operation_id=o.id AND pc.phase='CAPTURE')
 OR NOT EXISTS(SELECT 1 FROM economic.receipt_states WHERE receipt_id=r.latest_receipt_id AND authentication='AUTHENTICATED')) THEN
  PERFORM authority.fail('AUTHENTICATED_PAYPAL_CAPTURE_BUNDLE_REQUIRED'); END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER paypal_phase_bundle AFTER INSERT ON paypal_v1.phase_commits
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION paypal_v1.bundle_guard();
CREATE CONSTRAINT TRIGGER paypal_runtime_bundle AFTER INSERT OR UPDATE ON paypal_v1.runtime
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION paypal_v1.bundle_guard();
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['bindings','capture_requests','phase_commits','events','transport_diagnostics'] LOOP
  EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON paypal_v1.%I FOR EACH ROW EXECUTE FUNCTION authority.immutable()',t);
 END LOOP;
END $$;

CREATE FUNCTION paypal_v1.integration_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
BEGIN
 -- The unchanged admission trigger attempts a reference default INSERT ...
 -- ON CONFLICT DO NOTHING. Allow that candidate only when an exact PayPal row
 -- already exists: uniqueness prevents it ever becoming a stored replacement.
 IF TG_OP='INSERT' AND NEW.adapter='REFERENCE_SYNTHETIC' AND EXISTS(
  SELECT 1 FROM dispatch.integrations WHERE tenant_id=NEW.tenant_id AND adapter='PAYPAL_SANDBOX') THEN
  RETURN NEW; END IF;
 IF NEW.adapter='PAYPAL_SANDBOX' OR EXISTS(SELECT 1 FROM paypal_v1.tenants WHERE tenant_id=NEW.tenant_id) THEN
  IF NEW.adapter<>'PAYPAL_SANDBOX' OR NEW.replay_contract_id<>'paypal-orders-v2-v1' OR NOT EXISTS(
    SELECT 1 FROM paypal_v1.tenants WHERE tenant_id=NEW.tenant_id AND merchant_id=NEW.account_id) THEN
   PERFORM authority.fail('PAYPAL_INTEGRATION_BINDING_REQUIRED'); END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER paypal_integration BEFORE INSERT OR UPDATE ON dispatch.integrations
 FOR EACH ROW EXECUTE FUNCTION paypal_v1.integration_guard();

CREATE FUNCTION paypal_v1.wire(o dispatch.business_operations) RETURNS jsonb
LANGUAGE sql STABLE SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
 SELECT jsonb_build_object('intent','CAPTURE','processing_instruction','NO_INSTRUCTION',
 'purchase_units',jsonb_build_array(jsonb_build_object('reference_id',o.operation_key,'custom_id',o.operation_key,
 'amount',jsonb_build_object('currency_code','USD','value',(o.amount/100)::text||'.'||lpad((o.amount%100)::text,2,'0')),
 'payee',jsonb_build_object('merchant_id',o.account_id),'description','Interlock protected workspace seat')),
 'payment_source',jsonb_build_object('paypal',jsonb_build_object('experience_context',jsonb_build_object(
 'shipping_preference','NO_SHIPPING','user_action','PAY_NOW','return_url','http://127.0.0.1:3000/paypal/return',
 'cancel_url','http://127.0.0.1:3000/paypal/cancel'))))
$$;
CREATE FUNCTION paypal_v1.bind_operation() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE payload jsonb; BEGIN
 IF NEW.adapter<>'PAYPAL_SANDBOX' THEN RETURN NEW; END IF;
 IF NEW.currency<>'USD' OR NEW.approval_id IS NULL OR NOT EXISTS(
  SELECT 1 FROM paypal_v1.tenants WHERE tenant_id=NEW.tenant_id AND merchant_id=NEW.account_id) THEN
  PERFORM authority.fail('PAYPAL_EXACT_APPROVED_USD_ACTION_REQUIRED'); END IF;
 payload:=paypal_v1.wire(NEW);
 INSERT INTO paypal_v1.bindings(operation_id,tenant_id,merchant_id,amount_minor,currency,wire_body,wire_digest)
 VALUES(NEW.id,NEW.tenant_id,NEW.account_id,NEW.amount,'USD',payload,dispatch.hash(payload));
 INSERT INTO paypal_v1.runtime(operation_id) VALUES(NEW.id);
 RETURN NEW;
END $$;
CREATE TRIGGER paypal_bind AFTER INSERT ON dispatch.business_operations
 FOR EACH ROW EXECUTE FUNCTION paypal_v1.bind_operation();

-- Old reference/Razorpay commit and observation paths cannot mutate PayPal state.
CREATE FUNCTION paypal_v1.legacy_transport_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM dispatch.business_operations WHERE id=NEW.operation_id AND adapter='PAYPAL_SANDBOX') THEN
  PERFORM authority.fail('PAYPAL_VERSIONED_TRANSPORT_REQUIRED'); END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER paypal_no_legacy_commit BEFORE INSERT ON dispatch.dispatch_records
 FOR EACH ROW EXECUTE FUNCTION paypal_v1.legacy_transport_guard();
CREATE TRIGGER paypal_no_legacy_observation BEFORE INSERT ON dispatch.operation_observations
 FOR EACH ROW EXECUTE FUNCTION paypal_v1.legacy_transport_guard();

CREATE FUNCTION paypal_v1.binding(secret text,opid text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE b paypal_v1.bindings; r paypal_v1.runtime; BEGIN
 SELECT * INTO b FROM paypal_v1.bindings WHERE operation_id=opid;
 IF b.operation_id IS NULL THEN PERFORM authority.fail('PAYPAL_OPERATION_NOT_VISIBLE',403); END IF;
 PERFORM dispatch.service_actor(secret,b.tenant_id,'OBSERVE');
 SELECT * INTO r FROM paypal_v1.runtime WHERE operation_id=opid;
 RETURN jsonb_build_object('binding',to_jsonb(b),'runtime',to_jsonb(r));
END $$;

CREATE FUNCTION paypal_v1.capture_request(token text,opid text,expected bigint,idem text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE o dispatch.business_operations; person authority.memberships; r paypal_v1.runtime;
 prior paypal_v1.capture_requests; p jsonb; h text; rid uuid; BEGIN
 o:=dispatch.user_operation(token,opid,'DISCLOSE'); person:=authority.actor(token);
 IF idem IS NULL OR idem !~ '^[a-zA-Z0-9_-]{8,100}$' OR expected IS NULL THEN
  PERFORM authority.fail('INVALID_CAPTURE_COMMAND',422); END IF;
 h:=dispatch.hash(jsonb_build_object('operation_id',opid,'expected',expected,'principal',person.principal));
 -- Same domain lock order as disclosure and handoff; runtime is always last.
 p:=dispatch.current_permission(o,false);
 PERFORM principal FROM authority.memberships WHERE principal=person.principal AND tenant_id=o.tenant_id FOR SHARE;
 SELECT * INTO r FROM paypal_v1.runtime WHERE operation_id=opid FOR UPDATE;
 SELECT * INTO prior FROM paypal_v1.capture_requests WHERE operation_id=opid;
 IF prior.id IS NOT NULL THEN
  IF prior.principal<>person.principal OR prior.request_hash<>h OR prior.idempotency_key<>idem THEN
   PERFORM authority.fail('CAPTURE_COMMAND_CONFLICT'); END IF;
  RETURN jsonb_build_object('capture_request_id',prior.id,'operation_id',opid,'recovered',true);
 END IF;
 PERFORM dispatch.temporal(p); PERFORM dispatch.user_operation(token,opid,'DISCLOSE');
 person:=authority.actor(token);
 IF NOT 'PURCHASE_ISSUER'=ANY(person.roles) THEN PERFORM authority.fail('BUYER_OWNER_ROLE_REQUIRED',403); END IF;
 IF r.operation_id IS NULL OR r.blocked OR r.revision<>expected OR r.provider_status IS DISTINCT FROM 'APPROVED'
 OR r.capture_confirmed OR NOT EXISTS(SELECT 1 FROM dispatch.checkout_exposures WHERE operation_id=opid)
 OR r.latest_read_at IS NULL OR r.latest_read_at<clock_timestamp()-interval '60 seconds' THEN
  PERFORM authority.fail('FRESH_APPROVED_EXACT_ORDER_REQUIRED'); END IF;
 INSERT INTO paypal_v1.capture_requests(operation_id,principal,principal_revision,idempotency_key,request_hash,action_id,approval_id,
 reservation_id,provider_order_id,merchant_id,amount_minor,currency)
 VALUES(opid,person.principal,person.revision,idem,h,o.action_id,o.approval_id,o.reservation_id,r.provider_order_id,o.account_id,o.amount,'USD')
 RETURNING id INTO rid;
 INSERT INTO paypal_v1.events(operation_id,kind) VALUES(opid,'INDEPENDENT_CAPTURE_REQUESTED');
 RETURN jsonb_build_object('capture_request_id',rid,'operation_id',opid,'recovered',false);
END $$;

CREATE FUNCTION paypal_v1.commit_phase(secret text,opid text,phase text,expected_digest text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
#variable_conflict use_variable
DECLARE o dispatch.business_operations; b paypal_v1.bindings; r paypal_v1.runtime;
 c dispatch.service_credentials; p jsonb; cr paypal_v1.capture_requests; previous paypal_v1.phase_commits;
 req uuid; wire_hash text; dutyid text; BEGIN
 SELECT * INTO o FROM dispatch.business_operations WHERE id=opid;
 SELECT * INTO b FROM paypal_v1.bindings WHERE operation_id=opid;
 IF b.operation_id IS NULL OR phase NOT IN ('CREATE','CAPTURE') THEN
  PERFORM authority.fail('PAYPAL_EXACT_PHASE_REQUIRED',403); END IF;
 c:=dispatch.service_actor(secret,o.tenant_id,'DISPATCH');
 -- Every NEW send uses the unchanged protected predicate, not cached admission.
 p:=dispatch.current_permission(o,true,c.id);
 SELECT * INTO cr FROM paypal_v1.capture_requests WHERE operation_id=opid;
 IF phase='CAPTURE' AND cr.id IS NOT NULL THEN
  PERFORM principal FROM authority.memberships WHERE principal=cr.principal AND tenant_id=o.tenant_id FOR SHARE;
 END IF;
 SELECT * INTO r FROM paypal_v1.runtime WHERE operation_id=opid FOR UPDATE;
 PERFORM dispatch.temporal(p); c:=dispatch.service_actor(secret,o.tenant_id,'DISPATCH');
 SELECT * INTO previous FROM paypal_v1.phase_commits pc WHERE pc.operation_id=opid AND pc.phase=commit_phase.phase;
 IF previous.operation_id IS NOT NULL THEN
  RETURN jsonb_build_object('send',false,'operation_id',opid,'phase',phase,'recovery','CANONICAL_READ_ONLY'); END IF;
 IF r.blocked OR NOT EXISTS(SELECT 1 FROM paypal_v1.deployment WHERE enabled)
 OR NOT EXISTS(SELECT 1 FROM dispatch.integrations WHERE tenant_id=o.tenant_id AND real_mutations_enabled)
 OR o.approval_id IS NULL OR o.adapter<>'PAYPAL_SANDBOX' OR o.currency<>'USD'
 OR b.currency<>o.currency OR b.amount_minor<>o.amount OR b.merchant_id<>o.account_id
 OR b.wire_body IS DISTINCT FROM paypal_v1.wire(o) THEN
  PERFORM authority.fail('PAYPAL_DISABLED_OR_BINDING_CONFLICT',403); END IF;
 IF phase='CREATE' THEN
  IF r.provider_order_id IS NOT NULL THEN PERFORM authority.fail('ORDER_ALREADY_BOUND'); END IF;
  req:=b.create_request_id; wire_hash:=b.wire_digest;
 ELSE
  SELECT * INTO cr FROM paypal_v1.capture_requests WHERE operation_id=opid;
  IF cr.id IS NULL OR cr.action_id<>o.action_id OR cr.approval_id<>o.approval_id
  OR cr.reservation_id<>o.reservation_id OR cr.merchant_id<>o.account_id OR cr.amount_minor<>o.amount
  OR cr.currency<>'USD' OR cr.provider_order_id IS DISTINCT FROM r.provider_order_id
  OR r.provider_status IS DISTINCT FROM 'APPROVED' OR r.capture_confirmed
  OR r.latest_read_at IS NULL OR r.latest_read_at<clock_timestamp()-interval '60 seconds'
  OR NOT EXISTS(SELECT 1 FROM paypal_v1.phase_commits pc WHERE pc.operation_id=opid AND pc.phase='CREATE') THEN
   PERFORM authority.fail('SEPARATE_EXACT_CAPTURE_AUTHORIZATION_REQUIRED'); END IF;
  -- Buyer-owner membership remains current as well as original requester/approver.
  IF NOT EXISTS(SELECT 1 FROM authority.memberships m JOIN authority.grants g ON g.issuer=m.principal
   WHERE m.principal=cr.principal AND m.tenant_id=o.tenant_id AND m.active AND m.revision=cr.principal_revision
   AND 'PURCHASE_ISSUER'=ANY(m.roles) AND g.id=o.grant_id) THEN
   PERFORM authority.fail('BUYER_OWNER_CHANGED',403); END IF;
  req:=b.capture_request_id; wire_hash:=dispatch.hash('{}'::jsonb);
 END IF;
 IF expected_digest IS DISTINCT FROM wire_hash THEN PERFORM authority.fail('EXACT_WIRE_DIGEST_REQUIRED'); END IF;
 PERFORM dispatch.temporal(p);
 INSERT INTO paypal_v1.phase_commits(operation_id,phase,request_id,wire_digest,capture_request_id,
 provider_order_id,service_credential_id,permission)
 VALUES(opid,phase,req,wire_hash,cr.id,CASE WHEN phase='CAPTURE' THEN r.provider_order_id END,c.id,p);
 dutyid:='paypal_duty_'||replace(gen_random_uuid()::text,'-','');
 INSERT INTO dispatch.recovery_obligations(id,tenant_id,origin_kind,operation_id,reservation_id,duty_kind,
 predicate,read_role,mutation_requires_current_authority,replay_contract_id,evaluator_version)
 VALUES(dutyid,o.tenant_id,'OPERATION',opid,o.reservation_id,'RESOLVE_EXTERNAL_EFFECT','EXACT_ORDER_EFFECT',
 'OBSERVE',true,o.replay_contract_id,'paypal-duty-v1') ON CONFLICT DO NOTHING;
 INSERT INTO dispatch.duty_runtime(obligation_id,tenant_id,state)
 SELECT id,tenant_id,'OPEN' FROM dispatch.recovery_obligations WHERE operation_id=opid AND origin_kind='OPERATION'
 ON CONFLICT DO NOTHING;
 UPDATE dispatch.operation_runtime SET state='DISPATCH_COMMITTED',knowledge='UNKNOWN',revision=revision+1,
 updated_at=clock_timestamp() WHERE operation_id=opid;
 UPDATE dispatch.outbox_items SET completed=true WHERE operation_id=opid AND purpose='SEND';
 INSERT INTO paypal_v1.events(operation_id,kind) VALUES(opid,phase||'_COMMITTED_OUTCOME_UNKNOWN');
 RETURN jsonb_build_object('send',true,'operation_id',opid,'phase',phase,'request_id',req,
 'wire_body',CASE WHEN phase='CREATE' THEN b.wire_body ELSE '{}'::jsonb END,'provider_order_id',r.provider_order_id);
END $$;

CREATE FUNCTION paypal_v1.note(secret text,opid text,code text,evidence jsonb DEFAULT '{}'::jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE b paypal_v1.bindings; conflict boolean; BEGIN
 SELECT * INTO b FROM paypal_v1.bindings WHERE operation_id=opid;
 IF b.operation_id IS NULL THEN PERFORM authority.fail('PAYPAL_OPERATION_NOT_VISIBLE',403); END IF;
 PERFORM dispatch.service_actor(secret,b.tenant_id,'OBSERVE');
 IF code NOT IN ('READ_UNAVAILABLE','MUTATION_OUTCOME_UNKNOWN','CANONICAL_BINDING_CONFLICT','COMMITTED_BINDING_CONFLICT')
 OR code IS NULL THEN PERFORM authority.fail('INVALID_PAYPAL_DIAGNOSTIC'); END IF;
 IF jsonb_typeof(evidence) IS DISTINCT FROM 'object' OR EXISTS(SELECT 1 FROM jsonb_object_keys(evidence) k
  WHERE k NOT IN ('http_status','name','issues','debug_id','response_sha256'))
 OR evidence ? 'http_status' AND ((evidence->>'http_status') !~ '^[1-5][0-9]{2}$')
 OR evidence ? 'name' AND (evidence->>'name') !~ '^[A-Za-z][A-Za-z0-9_]{0,99}$'
 OR evidence ? 'debug_id' AND (evidence->>'debug_id') !~ '^[A-Za-z0-9_-]{1,100}$'
 OR evidence ? 'response_sha256' AND (evidence->>'response_sha256') !~ '^[a-f0-9]{64}$' THEN
  PERFORM authority.fail('UNSAFE_PAYPAL_DIAGNOSTIC'); END IF;
 IF evidence ? 'issues' THEN
  IF jsonb_typeof(evidence->'issues') IS DISTINCT FROM 'array' OR jsonb_array_length(evidence->'issues')>10 THEN
   PERFORM authority.fail('UNSAFE_PAYPAL_DIAGNOSTIC'); END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(evidence->'issues') x WHERE x !~ '^[A-Z][A-Z0-9_]{0,99}$') THEN
   PERFORM authority.fail('UNSAFE_PAYPAL_DIAGNOSTIC'); END IF;
 END IF;
 INSERT INTO paypal_v1.transport_diagnostics(operation_id,kind,evidence) VALUES(opid,code,evidence);
 conflict:=code IN ('CANONICAL_BINDING_CONFLICT','COMMITTED_BINDING_CONFLICT');
 UPDATE paypal_v1.runtime SET blocked=blocked OR conflict,blocked_reason=CASE WHEN conflict THEN code ELSE blocked_reason END,
 latest_read_at=NULL,revision=revision+1 WHERE operation_id=opid;
 IF conflict THEN
  UPDATE dispatch.operation_runtime SET knowledge='BLOCKED',blocked_reason=code,revision=revision+1 WHERE operation_id=opid;
  UPDATE economic.object_heads SET blocked=true WHERE provider='PAYPAL_SANDBOX' AND account_id=b.merchant_id
   AND object_id=(SELECT capture_id FROM paypal_v1.runtime WHERE operation_id=opid);
 END IF;
 INSERT INTO paypal_v1.events(operation_id,kind) VALUES(opid,code);
 RETURN jsonb_build_object('operation_id',opid,'blocked',conflict,'obligation_retained',true,'mutation_replay_allowed',false);
END $$;

CREATE FUNCTION paypal_v1.order_hint(secret text,opid text,orderid text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE b paypal_v1.bindings; r paypal_v1.runtime; BEGIN
 SELECT * INTO b FROM paypal_v1.bindings WHERE operation_id=opid;
 PERFORM dispatch.service_actor(secret,b.tenant_id,'OBSERVE');
 SELECT * INTO r FROM paypal_v1.runtime WHERE operation_id=opid FOR UPDATE;
 IF orderid IS NULL OR orderid !~ '^[A-Z0-9]{10,32}$' OR NOT EXISTS(
  SELECT 1 FROM paypal_v1.phase_commits WHERE operation_id=opid AND phase='CREATE') THEN
  PERFORM authority.fail('COMMITTED_CREATE_AND_NATIVE_ORDER_REQUIRED'); END IF;
 IF r.provider_order_id IS NOT NULL AND r.provider_order_id<>orderid THEN
  PERFORM authority.fail('NATIVE_ORDER_IDENTITY_CONFLICT'); END IF;
 UPDATE paypal_v1.runtime SET provider_order_id=orderid,revision=revision+1 WHERE operation_id=opid;
 INSERT INTO paypal_v1.events(operation_id,kind) VALUES(opid,'ORDER_ID_HINT_NOT_SETTLEMENT');
END $$;
