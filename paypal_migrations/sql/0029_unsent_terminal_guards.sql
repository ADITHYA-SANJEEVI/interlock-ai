-- Preserve qualified record fields and enforce terminal outbox closure.
CREATE OR REPLACE FUNCTION paypal_v1.admit_replacement(token text,parentid text,body jsonb,expected bigint,idem text) RETURNS jsonb
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
 SELECT * INTO a FROM authority.action_revisions WHERE id=admit_replacement.body->>'action_id' AND tenant_id=person.tenant_id;
 SELECT * INTO g FROM authority.grants WHERE id=a.grant_id;
 SELECT * INTO oldg FROM authority.grants WHERE id=old.grant_id;
 SELECT q.* INTO oldq FROM authority.seller_quotes q JOIN authority.evidence_revisions e ON e.quote_id=q.id WHERE e.id=old.evidence_id;
 SELECT q.* INTO newq FROM authority.seller_quotes q JOIN authority.evidence_revisions e ON e.quote_id=q.id WHERE e.id=admit_replacement.body->>'evidence_id' AND e.action_id=a.id;
 IF a.id IS NULL OR a.id=old.action_id OR admit_replacement.body->>'approval_id'=old.approval_id OR a.requester<>old.requester
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
  AND ap->>'evidence_id'=admit_replacement.body->>'evidence_id' AND ap->>'decision_id'=admit_replacement.body->>'decision_id' AND ap->>'effective_status'='AVAILABLE') THEN
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

CREATE OR REPLACE FUNCTION paypal_v1.cancellation_bundle() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
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
 OR r.provider_status IS NOT NULL OR r.latest_read_at IS NOT NULL
 OR rt.provider_order IS NOT NULL OR rt.latest_observation_id IS NOT NULL OR rt.blocked_reason<>'CANCELLED_NEVER_SENT'
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

CREATE CONSTRAINT TRIGGER unsent_cancellation_outbox AFTER INSERT OR UPDATE ON dispatch.outbox_items DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION paypal_v1.cancellation_bundle();
