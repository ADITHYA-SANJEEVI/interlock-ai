-- Additive opt-in contract. No historical function, approval or operation is rewritten.
ALTER TABLE authority.reservation_heads DROP CONSTRAINT reservation_heads_status_check;
ALTER TABLE authority.reservation_heads ADD CHECK(status IN
 ('RESERVED','FIXTURE_CONSUMED','FIXTURE_RELEASED','REFUND_PROCESSED','CANCELLED_UNSENT'));
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='authority.authority_movements'::regclass
 AND contype='c' AND pg_get_constraintdef(oid) LIKE '%kind%' LOOP
  EXECUTE format('ALTER TABLE authority.authority_movements DROP CONSTRAINT %I',c.conname);
 END LOOP;
END $$;
ALTER TABLE authority.authority_movements ADD CHECK(kind IN
 ('RESERVE','FIXTURE_CONSUME','FIXTURE_RELEASE','REFUND_PROCESSED','UNSENT_RELEASE'));
ALTER TABLE authority.authority_movements ADD CHECK(
 (kind='RESERVE' AND delta_consumed=0 AND delta_reserved=amount AND delta_free=-amount)
 OR (kind IN ('FIXTURE_CONSUME','REFUND_PROCESSED') AND delta_consumed=amount AND delta_reserved=-amount AND delta_free=0)
 OR (kind IN ('FIXTURE_RELEASE','UNSENT_RELEASE') AND delta_consumed=0 AND delta_reserved=-amount AND delta_free=amount));

CREATE TABLE paypal_v1.cancellations (
 operation_id text PRIMARY KEY REFERENCES paypal_v1.bindings(operation_id),
 id uuid UNIQUE NOT NULL DEFAULT gen_random_uuid(), tenant_id text NOT NULL REFERENCES paypal_v1.tenants(tenant_id),
 principal text NOT NULL REFERENCES authority.memberships(principal), principal_revision bigint NOT NULL CHECK(principal_revision>0),
 idempotency_key text NOT NULL, request_hash text NOT NULL, request_id text NOT NULL REFERENCES authority.request_identities(id),
 result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,principal,idempotency_key)
);
CREATE TABLE paypal_v1.replacements (
 cancelled_operation_id text PRIMARY KEY REFERENCES paypal_v1.cancellations(operation_id),
 operation_id text UNIQUE NOT NULL REFERENCES paypal_v1.bindings(operation_id),
 tenant_id text NOT NULL REFERENCES paypal_v1.tenants(tenant_id),
 principal text NOT NULL REFERENCES authority.memberships(principal),
 idempotency_key text NOT NULL, request_hash text NOT NULL, result jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(cancelled_operation_id<>operation_id), UNIQUE(tenant_id,principal,idempotency_key)
);
ALTER TABLE paypal_v1.cancellations OWNER TO interlock_authority_owner;
ALTER TABLE paypal_v1.replacements OWNER TO interlock_authority_owner;
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON paypal_v1.cancellations
 FOR EACH ROW EXECUTE FUNCTION authority.immutable();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON paypal_v1.replacements
 FOR EACH ROW EXECUTE FUNCTION authority.immutable();
REVOKE ALL ON paypal_v1.cancellations,paypal_v1.replacements FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dispatch.user_operation(text,text,text) TO interlock_authority_owner;

-- Transport helper has no spending-counter write privilege. Only the authority
-- definer may invoke it, after acquiring the shared authority lock order.
CREATE FUNCTION paypal_v1.cancel_transport_unsent(opid text,expected bigint,dispatch_expected bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE o dispatch.business_operations; b paypal_v1.bindings; r paypal_v1.runtime; rt dispatch.operation_runtime;
BEGIN
 SELECT * INTO o FROM dispatch.business_operations WHERE id=opid;
 SELECT * INTO b FROM paypal_v1.bindings WHERE operation_id=opid;
 -- Queue claims lock outbox rows only; taking these before runtime prevents a
 -- concurrent claim from becoming invisible to the never-sent proof.
 PERFORM id FROM dispatch.outbox_items WHERE operation_id=opid ORDER BY id FOR UPDATE;
 SELECT * INTO r FROM paypal_v1.runtime WHERE operation_id=opid FOR UPDATE;
 SELECT * INTO rt FROM dispatch.operation_runtime WHERE operation_id=opid FOR UPDATE;
 IF b.operation_id IS NULL OR o.adapter<>'PAYPAL_SANDBOX' OR o.currency<>'USD'
 OR b.wire_body IS DISTINCT FROM paypal_v1.wire(o) OR b.wire_digest<>dispatch.hash(b.wire_body)
 OR b.merchant_id<>o.account_id OR b.amount_minor<>o.amount OR b.currency<>o.currency THEN
  PERFORM authority.fail('CANCELLATION_BINDING_MISMATCH',403); END IF;
 IF r.revision<>expected OR rt.revision<>dispatch_expected THEN PERFORM authority.fail('REVISION_CONFLICT'); END IF;
 IF r.blocked OR r.provider_order_id IS NOT NULL OR r.capture_id IS NOT NULL OR r.capture_confirmed
 OR r.latest_receipt_id IS NOT NULL OR r.latest_read_at IS NOT NULL OR r.provider_status IS NOT NULL
 OR rt.state NOT IN ('QUEUED','READY') OR rt.knowledge<>'NOT_ATTEMPTED' OR rt.attempt_count<>0 OR rt.provider_order IS NOT NULL
 OR EXISTS(SELECT 1 FROM paypal_v1.phase_commits WHERE operation_id=opid)
 OR EXISTS(SELECT 1 FROM dispatch.transport_attempts WHERE operation_id=opid)
 OR EXISTS(SELECT 1 FROM dispatch.dispatch_records WHERE operation_id=opid)
 OR EXISTS(SELECT 1 FROM dispatch.checkout_exposures WHERE operation_id=opid)
 OR EXISTS(SELECT 1 FROM paypal_v1.capture_requests WHERE operation_id=opid)
 OR EXISTS(SELECT 1 FROM dispatch.recovery_obligations WHERE operation_id=opid)
 OR EXISTS(SELECT 1 FROM economic.observations WHERE operation_id=opid)
 OR EXISTS(SELECT 1 FROM economic.facts WHERE operation_id=opid)
 OR EXISTS(SELECT 1 FROM paypal_v1.events WHERE operation_id=opid)
 OR EXISTS(SELECT 1 FROM paypal_v1.transport_diagnostics WHERE operation_id=opid)
 OR EXISTS(SELECT 1 FROM dispatch.reservation_exposure WHERE reservation_id=o.reservation_id AND state<>'RESERVED_UNEXPOSED') THEN
  PERFORM authority.fail('NEVER_SENT_PROOF_UNAVAILABLE'); END IF;
 IF EXISTS(SELECT 1 FROM dispatch.outbox_items WHERE operation_id=opid AND lease_until>clock_timestamp())
 OR EXISTS(SELECT 1 FROM dispatch.queue_claims WHERE operation_id=opid AND lease_until>clock_timestamp()) THEN
  PERFORM authority.fail('ACTIVE_CLAIM_PREVENTS_CANCELLATION'); END IF;
 UPDATE paypal_v1.runtime SET blocked=true,blocked_reason='CANCELLED_NEVER_SENT',revision=revision+1 WHERE operation_id=opid;
 UPDATE dispatch.operation_runtime SET state='CANCEL_UNSENT',knowledge='NOT_ATTEMPTED',revision=revision+1,
  blocked_reason='CANCELLED_NEVER_SENT',updated_at=clock_timestamp() WHERE operation_id=opid;
 UPDATE dispatch.outbox_items SET completed=true WHERE operation_id=opid;
END $$;
ALTER FUNCTION paypal_v1.cancel_transport_unsent(text,bigint,bigint) OWNER TO interlock_dispatch_owner;
REVOKE ALL ON FUNCTION paypal_v1.cancel_transport_unsent(text,bigint,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION paypal_v1.cancel_transport_unsent(text,bigint,bigint) TO interlock_authority_owner;
GRANT SELECT ON economic.observations,economic.facts TO interlock_dispatch_owner;

CREATE FUNCTION paypal_v1.cancel_never_sent(token text,opid text,expected bigint,dispatch_expected bigint,idem text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE person authority.memberships; o dispatch.business_operations; g authority.grants; a authority.action_revisions;
 h authority.grant_heads; r authority.reservations; rh authority.reservation_heads;
 prior paypal_v1.cancellations; result jsonb; request_hash text; rid text; cid uuid:=gen_random_uuid();
BEGIN
 person:=authority.actor(token); PERFORM authority.require_role(person,'PURCHASE_ISSUER');
 o:=dispatch.user_operation(token,opid,'VIEW');
 SELECT * INTO g FROM authority.grants WHERE id=o.grant_id AND tenant_id=person.tenant_id;
 IF g.issuer IS DISTINCT FROM person.principal OR o.adapter<>'PAYPAL_SANDBOX' OR o.currency<>'USD' THEN
  PERFORM authority.fail('CANCELLATION_ISSUER_REQUIRED',403); END IF;
 IF idem IS NULL OR idem !~ '^[a-zA-Z0-9_-]{8,100}$' OR expected IS NULL OR expected<1 OR dispatch_expected IS NULL OR dispatch_expected<1 THEN
  PERFORM authority.fail('INVALID_CANCELLATION_COMMAND',422); END IF;
 request_hash:=dispatch.hash(jsonb_build_object('operation_id',opid,'expected',expected,'dispatch_expected',dispatch_expected));
 PERFORM pg_advisory_xact_lock(hashtextextended(person.tenant_id||'/'||person.principal||'/cancel-never-sent/'||idem,0));
 SELECT * INTO prior FROM paypal_v1.cancellations WHERE tenant_id=person.tenant_id AND principal=person.principal AND idempotency_key=idem;
 IF FOUND THEN
  IF prior.request_hash<>request_hash OR prior.operation_id<>opid THEN PERFORM authority.fail('IDEMPOTENCY_CONFLICT'); END IF;
  RETURN prior.result;
 END IF;
 -- Match the order used by current_permission; expiry need not be revived to cancel.
 PERFORM tenant_id FROM authority.tenant_control WHERE tenant_id=o.tenant_id AND kind='PURCHASE' FOR SHARE;
 SELECT * INTO h FROM authority.grant_heads WHERE grant_id=o.grant_id FOR UPDATE;
 SELECT * INTO a FROM authority.action_revisions WHERE id=o.action_id;
 PERFORM intent_id FROM authority.intent_heads WHERE intent_id=a.intent_id FOR UPDATE;
 PERFORM quote_id FROM authority.quote_heads WHERE quote_id=(SELECT quote_id FROM authority.evidence_revisions WHERE id=o.evidence_id) FOR SHARE;
 PERFORM approval_id FROM authority.approval_heads WHERE approval_id=o.approval_id FOR SHARE;
 PERFORM principal FROM authority.memberships WHERE tenant_id=o.tenant_id
  AND principal IN (person.principal,a.requester,(SELECT approver FROM authority.approvals WHERE id=o.approval_id)) ORDER BY principal FOR SHARE;
 person:=authority.actor(token); PERFORM authority.require_role(person,'PURCHASE_ISSUER');
 SELECT * INTO r FROM authority.reservations WHERE id=o.reservation_id AND tenant_id=o.tenant_id;
 SELECT * INTO rh FROM authority.reservation_heads WHERE reservation_id=r.id FOR UPDATE;
 IF EXISTS(SELECT 1 FROM paypal_v1.cancellations WHERE operation_id=opid) THEN PERFORM authority.fail('CANCELLATION_ALREADY_RECORDED'); END IF;
 IF rh.status IS DISTINCT FROM 'RESERVED' OR r.action_id<>o.action_id OR r.amount<>o.amount OR r.currency<>'USD'
 OR r.grant_id<>o.grant_id OR h.reserved<r.amount OR NOT EXISTS(
  SELECT 1 FROM authority.approval_heads WHERE approval_id=o.approval_id AND status='CONSUMED' AND consumed_by=r.id) THEN
  PERFORM authority.fail('CANCELLATION_RESERVATION_MISMATCH'); END IF;
 PERFORM paypal_v1.cancel_transport_unsent(opid,expected,dispatch_expected);
 rid:='cmd_'||gen_random_uuid()::text;
 result:=jsonb_build_object('contract_version','paypal-cancellation-v1','cancellation_id',cid,'operation_id',opid,
  'reservation_id',r.id,'amount_minor',r.amount,'currency','USD','status','CANCELLED_NEVER_SENT',
  'reason','ISSUER_CANCELLED_BEFORE_HANDOFF','revision',expected+1,'dispatch_revision',dispatch_expected+1,
  'command_id',rid,'budget_at_cancellation',jsonb_build_array(h.bound,h.consumed,h.reserved-r.amount,h.free+r.amount),
  'provider_mutation_performed',false,'settlement_proven',false);
 INSERT INTO authority.request_identities VALUES(rid,person.tenant_id,person.principal,'cancel-never-sent',idem,request_hash,result,clock_timestamp());
 INSERT INTO authority.authority_movements(id,tenant_id,grant_id,grant_version_id,epoch,reservation_id,request_id,
  kind,amount,delta_consumed,delta_reserved,delta_free,reason)
 VALUES('movement_'||gen_random_uuid()::text,o.tenant_id,r.grant_id,r.grant_version_id,r.epoch,r.id,rid,
  'UNSENT_RELEASE',r.amount,0,-r.amount,r.amount,'LOCAL_NEVER_DISPATCHED_NO_PROVIDER_RECEIPT');
 UPDATE authority.grant_heads SET reserved=reserved-r.amount,free=free+r.amount WHERE grant_id=r.grant_id;
 UPDATE authority.reservation_heads SET status='CANCELLED_UNSENT' WHERE reservation_id=r.id;
 INSERT INTO paypal_v1.cancellations(operation_id,id,tenant_id,principal,principal_revision,idempotency_key,request_hash,request_id,result)
 VALUES(opid,cid,person.tenant_id,person.principal,person.revision,idem,request_hash,rid,result);
 INSERT INTO authority.audit_events VALUES('audit_'||gen_random_uuid()::text,o.tenant_id,person.principal,rid,'cancel-never-sent',result,clock_timestamp());
 SET CONSTRAINTS ALL IMMEDIATE;
 RETURN result;
END $$;
ALTER FUNCTION paypal_v1.cancel_never_sent(text,text,bigint,bigint,text) OWNER TO interlock_authority_owner;
REVOKE ALL ON FUNCTION paypal_v1.cancel_never_sent(text,text,bigint,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION paypal_v1.cancel_never_sent(text,text,bigint,bigint,text) TO interlock_api;

-- A terminal proof cannot coexist with an exposed/reopened/re-reserved operation.
CREATE FUNCTION paypal_v1.cancellation_bundle() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE opid text; c paypal_v1.cancellations; o dispatch.business_operations; r paypal_v1.runtime;
 rt dispatch.operation_runtime; rh authority.reservation_heads;
BEGIN
 IF TG_TABLE_SCHEMA='authority' THEN
  SELECT id INTO opid FROM dispatch.business_operations WHERE reservation_id=NEW.reservation_id;
 ELSE opid:=NEW.operation_id; END IF;
 SELECT * INTO c FROM paypal_v1.cancellations WHERE operation_id=opid;
 SELECT * INTO o FROM dispatch.business_operations WHERE id=opid;
 SELECT * INTO r FROM paypal_v1.runtime WHERE operation_id=opid;
 SELECT * INTO rt FROM dispatch.operation_runtime WHERE operation_id=opid;
 SELECT * INTO rh FROM authority.reservation_heads WHERE reservation_id=o.reservation_id;
 IF c.operation_id IS NULL THEN
  IF rh.status='CANCELLED_UNSENT' OR r.blocked_reason='CANCELLED_NEVER_SENT' OR rt.state='CANCEL_UNSENT' AND o.adapter='PAYPAL_SANDBOX' THEN
   PERFORM authority.fail('INCOMPLETE_UNSENT_CANCELLATION_BUNDLE'); END IF;
  RETURN NULL;
 END IF;
 IF c.tenant_id<>o.tenant_id OR rh.status<>'CANCELLED_UNSENT' OR NOT r.blocked OR r.blocked_reason<>'CANCELLED_NEVER_SENT'
 OR r.provider_order_id IS NOT NULL OR r.capture_id IS NOT NULL OR r.latest_receipt_id IS NOT NULL OR r.capture_confirmed
 OR rt.state<>'CANCEL_UNSENT' OR rt.knowledge<>'NOT_ATTEMPTED' OR rt.attempt_count<>0
 OR EXISTS(SELECT 1 FROM paypal_v1.phase_commits WHERE operation_id=opid)
 OR EXISTS(SELECT 1 FROM paypal_v1.capture_requests WHERE operation_id=opid)
 OR EXISTS(SELECT 1 FROM dispatch.checkout_exposures WHERE operation_id=opid)
 OR EXISTS(SELECT 1 FROM dispatch.outbox_items WHERE operation_id=opid AND NOT completed)
 OR EXISTS(SELECT 1 FROM economic.facts WHERE operation_id=opid)
 OR NOT EXISTS(SELECT 1 FROM authority.authority_movements WHERE reservation_id=o.reservation_id AND kind='UNSENT_RELEASE'
  AND amount=o.amount AND delta_consumed=0 AND delta_reserved=-o.amount AND delta_free=o.amount AND request_id=c.request_id) THEN
  PERFORM authority.fail('INCOMPLETE_UNSENT_CANCELLATION_BUNDLE'); END IF;
 RETURN NULL;
END $$;
ALTER FUNCTION paypal_v1.cancellation_bundle() OWNER TO interlock_dispatch_owner;
REVOKE ALL ON FUNCTION paypal_v1.cancellation_bundle() FROM PUBLIC;
GRANT SELECT ON paypal_v1.cancellations,paypal_v1.replacements,authority.authority_movements TO interlock_dispatch_owner;
CREATE CONSTRAINT TRIGGER unsent_cancellation_proof AFTER INSERT ON paypal_v1.cancellations DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION paypal_v1.cancellation_bundle();
CREATE CONSTRAINT TRIGGER unsent_cancellation_runtime AFTER INSERT OR UPDATE ON paypal_v1.runtime DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION paypal_v1.cancellation_bundle();
CREATE CONSTRAINT TRIGGER unsent_cancellation_dispatch AFTER INSERT OR UPDATE ON dispatch.operation_runtime DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION paypal_v1.cancellation_bundle();
CREATE CONSTRAINT TRIGGER unsent_cancellation_reservation AFTER INSERT OR UPDATE ON authority.reservation_heads DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION paypal_v1.cancellation_bundle();

CREATE FUNCTION paypal_v1.cancellation(token text,opid text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE o dispatch.business_operations; result jsonb; BEGIN
 o:=dispatch.user_operation(token,opid,'VIEW');
 SELECT c.result||jsonb_build_object('replacement_operation_id',r.operation_id) INTO result
 FROM paypal_v1.cancellations c LEFT JOIN paypal_v1.replacements r ON r.cancelled_operation_id=c.operation_id
 WHERE c.operation_id=opid AND c.tenant_id=o.tenant_id;
 RETURN result;
END $$;
ALTER FUNCTION paypal_v1.cancellation(text,text) OWNER TO interlock_authority_owner;
REVOKE ALL ON FUNCTION paypal_v1.cancellation(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION paypal_v1.cancellation(text,text) TO interlock_api;

CREATE FUNCTION paypal_v1.admit_replacement(token text,parentid text,body jsonb,expected bigint,idem text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE person authority.memberships; old dispatch.business_operations; a authority.action_revisions;
 g authority.grants; oldg authority.grants; oldq authority.seller_quotes; newq authority.seller_quotes;
 c paypal_v1.cancellations; prior paypal_v1.replacements; h text; inner_key text; result jsonb; snapshot jsonb;
BEGIN
 person:=authority.actor(token); PERFORM authority.require_role(person,'PURCHASE_REQUESTER');
 old:=dispatch.user_operation(token,parentid,'VIEW');
 IF old.requester<>person.principal OR old.adapter<>'PAYPAL_SANDBOX' OR old.currency<>'USD' THEN
  PERFORM authority.fail('REPLACEMENT_REQUESTER_REQUIRED',403); END IF;
 IF idem IS NULL OR idem !~ '^[a-zA-Z0-9_-]{8,100}$' OR expected IS NULL OR expected<1 THEN
  PERFORM authority.fail('INVALID_REPLACEMENT_COMMAND',422); END IF;
 h:=dispatch.hash(jsonb_build_object('cancelled_operation_id',parentid,'body',body,'expected',expected));
 PERFORM pg_advisory_xact_lock(hashtextextended(person.tenant_id||'/'||person.principal||'/replacement/'||idem,0));
 SELECT * INTO prior FROM paypal_v1.replacements WHERE tenant_id=person.tenant_id AND principal=person.principal AND idempotency_key=idem;
 IF FOUND THEN
  IF prior.request_hash<>h THEN PERFORM authority.fail('IDEMPOTENCY_CONFLICT'); END IF;
  RETURN prior.result;
 END IF;
 -- Acquire the inner normal command identity lock before its domain locks.
 inner_key:='replacement_'||dispatch.hash(jsonb_build_object('tenant',person.tenant_id,'principal',person.principal,'parent',parentid,'idem',idem));
 PERFORM pg_advisory_xact_lock(hashtextextended(person.tenant_id||'/'||person.principal||'/admit/'||inner_key,0));
 SELECT * INTO c FROM paypal_v1.cancellations WHERE operation_id=parentid AND tenant_id=person.tenant_id FOR UPDATE;
 IF c.operation_id IS NULL THEN PERFORM authority.fail('CANCELLED_PARENT_REQUIRED'); END IF;
 IF EXISTS(SELECT 1 FROM paypal_v1.replacements WHERE cancelled_operation_id=parentid) THEN
  PERFORM authority.fail('REPLACEMENT_ALREADY_ADMITTED'); END IF;
 SELECT * INTO a FROM authority.action_revisions WHERE id=body->>'action_id' AND tenant_id=person.tenant_id;
 SELECT * INTO g FROM authority.grants WHERE id=a.grant_id;
 SELECT * INTO oldg FROM authority.grants WHERE id=old.grant_id;
 SELECT q.* INTO oldq FROM authority.seller_quotes q JOIN authority.evidence_revisions e ON e.quote_id=q.id WHERE e.id=old.evidence_id;
 SELECT q.* INTO newq FROM authority.seller_quotes q JOIN authority.evidence_revisions e ON e.quote_id=q.id WHERE e.id=body->>'evidence_id' AND e.action_id=a.id;
 IF a.id IS NULL OR a.id=old.action_id OR body->>'approval_id'=old.approval_id OR a.requester<>old.requester
 OR a.currency<>'USD' OR a.amount<>old.amount OR g.issuer<>oldg.issuer OR g.subject<>old.requester
 OR newq.id IS NULL OR newq.id=oldq.id OR newq.seller<>old.seller OR newq.currency<>'USD'
 OR newq.body->>'merchant_id'<>old.account_id OR newq.body->>'provider'<>'PAYPAL_SANDBOX'
 OR jsonb_array_length(newq.body->'lines')<>1 OR jsonb_array_length(oldq.body->'lines')<>1
 OR newq.body->'lines'->0->>'sku' IS DISTINCT FROM oldq.body->'lines'->0->>'sku'
 OR newq.body->'lines'->0->>'beneficiary' IS DISTINCT FROM oldq.body->'lines'->0->>'beneficiary'
 OR newq.body->'lines'->0->>'unit_price' IS DISTINCT FROM oldq.body->'lines'->0->>'unit_price' THEN
  PERFORM authority.fail('REPLACEMENT_EXACT_TERMS_REQUIRED',403); END IF;
 IF EXISTS(SELECT 1 FROM dispatch.business_operations WHERE action_id=a.id) THEN PERFORM authority.fail('REPLACEMENT_ACTION_ALREADY_ADMITTED'); END IF;
 result:=paypal_v1.command(token,'admit',body,expected,inner_key);
 -- The original kernel validates and consumes the selected fresh approval.
 -- Additional current exact approvals are ambiguous, so roll back the admission.
 snapshot:=paypal_v1.snapshot(token);
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(snapshot->'approvals') ap WHERE ap->>'action_id'=a.id
  AND ap->>'evidence_id'=body->>'evidence_id' AND ap->>'decision_id'=body->>'decision_id' AND ap->>'effective_status'='AVAILABLE') THEN
  PERFORM authority.fail('EXACT_APPROVAL_AMBIGUOUS'); END IF;
 IF NOT EXISTS(SELECT 1 FROM dispatch.business_operations n WHERE n.id=result->>'operation_id'
  AND n.account_id=old.account_id AND n.integration=old.integration AND n.amount=old.amount AND n.currency='USD') THEN
  PERFORM authority.fail('REPLACEMENT_EXACT_TERMS_REQUIRED',403); END IF;
 result:=result||jsonb_build_object('cancelled_operation_id',parentid,'replacement_operation_id',result->>'operation_id');
 INSERT INTO paypal_v1.replacements(cancelled_operation_id,operation_id,tenant_id,principal,idempotency_key,request_hash,result)
 VALUES(parentid,result->>'operation_id',person.tenant_id,person.principal,idem,h,result);
 SET CONSTRAINTS ALL IMMEDIATE;
 RETURN result;
END $$;
ALTER FUNCTION paypal_v1.admit_replacement(text,text,jsonb,bigint,text) OWNER TO interlock_authority_owner;
REVOKE ALL ON FUNCTION paypal_v1.admit_replacement(text,text,jsonb,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION paypal_v1.admit_replacement(text,text,jsonb,bigint,text) TO interlock_api;
