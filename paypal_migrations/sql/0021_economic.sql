-- New canonical interpreter; legacy economic.observe is deliberately unchanged.
ALTER TABLE economic.channels DROP CONSTRAINT channels_provider_check;
ALTER TABLE economic.channels ADD CHECK(provider IN ('REFERENCE_SYNTHETIC','RAZORPAY_TEST','CONTROLLED_SELLER','PAYPAL_SANDBOX'));
ALTER TABLE economic.observations DROP CONSTRAINT observations_currency_check;
ALTER TABLE economic.observations ADD CHECK(currency IN ('INR','USD'));
ALTER TABLE economic.facts DROP CONSTRAINT facts_currency_check;
ALTER TABLE economic.facts ADD CHECK(currency IN ('INR','USD'));
ALTER TABLE economic.postings DROP CONSTRAINT postings_currency_check;
ALTER TABLE economic.postings ADD CHECK(currency IN ('INR','USD'));
ALTER TABLE economic.lines DROP CONSTRAINT lines_currency_check;
ALTER TABLE economic.lines ADD CHECK(currency IN ('INR','USD'));
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['economic.observations','economic.facts','economic.postings'] LOOP
  EXECUTE format('CREATE TRIGGER paypal_denomination BEFORE INSERT ON %s FOR EACH ROW EXECUTE FUNCTION paypal_v1.denomination_guard()',t);
 END LOOP;
END $$;

CREATE FUNCTION paypal_v1.pending() RETURNS jsonb LANGUAGE sql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
 SELECT jsonb_build_object(
 'receipts',coalesce((SELECT jsonb_agg(x.id) FROM (SELECT r.id FROM economic.receipts r
  JOIN economic.receipt_states s ON s.receipt_id=r.id JOIN economic.channels c ON c.id=r.channel_id
  JOIN paypal_v1.tenants t ON t.tenant_id=c.tenant_id WHERE NOT s.interpreted
  AND c.provider IN ('PAYPAL_SANDBOX','CONTROLLED_SELLER') ORDER BY r.received_at,r.id LIMIT 50) x),'[]'::jsonb),
 'facts',coalesce((SELECT jsonb_agg(x.id) FROM (SELECT f.id FROM economic.facts f
  JOIN paypal_v1.bindings b ON b.operation_id=f.operation_id WHERE f.kind<>'SELLER_FULFILLED'
  AND NOT EXISTS(SELECT 1 FROM economic.postings p WHERE p.fact_id=f.id)
  ORDER BY CASE f.kind WHEN 'CAPTURE' THEN 1 WHEN 'ORDER_LIABILITY' THEN 2 ELSE 3 END,f.id LIMIT 50) x),'[]'::jsonb),
 'fulfillments',coalesce((SELECT jsonb_agg(x.operation_id) FROM (SELECT b.operation_id FROM paypal_v1.bindings b
  JOIN paypal_v1.runtime r ON r.operation_id=b.operation_id WHERE r.capture_confirmed AND NOT r.blocked
  AND EXISTS(SELECT 1 FROM economic.facts f JOIN economic.postings p ON p.fact_id=f.id
   WHERE f.operation_id=b.operation_id AND f.kind='ORDER_LIABILITY')
  AND NOT EXISTS(SELECT 1 FROM economic.facts f WHERE f.operation_id=b.operation_id AND f.kind='SELLER_FULFILLED')
  ORDER BY b.operation_id LIMIT 50) x),'[]'::jsonb))
$$;

CREATE FUNCTION paypal_v1.record_read(secret text,opid text,raw text,sig text,delivery text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE o dispatch.business_operations; b paypal_v1.bindings; rt paypal_v1.runtime; c economic.channels;
 v jsonb; ord jsonb; u jsonb; cap jsonb; caps jsonb; rid uuid; oid uuid; amt text;
 reason text; confirmed boolean:=false; native_capture text; sh text; BEGIN
 SELECT * INTO o FROM dispatch.business_operations WHERE id=opid;
 SELECT * INTO b FROM paypal_v1.bindings WHERE operation_id=opid;
 IF b.operation_id IS NULL THEN PERFORM authority.fail('PAYPAL_OPERATION_NOT_VISIBLE',403); END IF;
 PERFORM dispatch.service_actor(secret,o.tenant_id,'OBSERVE');
 SELECT * INTO c FROM economic.channels WHERE tenant_id=o.tenant_id AND provider='PAYPAL_SANDBOX'
 AND source='CANONICAL_READ' AND account_id=b.merchant_id AND integration=o.integration AND active;
 IF c.id IS NULL OR sig IS DISTINCT FROM encode(public.hmac(raw,c.secret,'sha256'),'hex') THEN
  PERFORM authority.fail('CANONICAL_BROKER_SIGNATURE_REQUIRED',403); END IF;
 rid:=economic.receive(c.id,convert_to(raw,'UTF8'),jsonb_build_object('signature',sig),delivery);
 IF economic.authenticate(rid)<>'AUTHENTICATED' THEN PERFORM authority.fail('CANONICAL_AUTHENTICATION_REQUIRED'); END IF;
 v:=raw::jsonb;
 SELECT * INTO rt FROM paypal_v1.runtime WHERE operation_id=opid FOR UPDATE;
 ord:=v->'order'; u:=ord->'purchase_units'->0; caps:=u->'captures'; cap:=caps->0;
 amt:=(b.amount_minor/100)::text||'.'||lpad((b.amount_minor%100)::text,2,'0');
 IF raw IS DISTINCT FROM dispatch.canonical(v) OR v->>'schema' IS DISTINCT FROM 'paypal-canonical-v1'
 OR v->>'operation_id' IS DISTINCT FROM opid OR v->>'wire_digest' IS DISTINCT FROM b.wire_digest
 OR v->>'merchant_id' IS DISTINCT FROM b.merchant_id OR v->>'currency' IS DISTINCT FROM 'USD'
 OR v->'amount_minor' IS DISTINCT FROM to_jsonb(b.amount_minor)
 OR rt.provider_order_id IS NULL OR ord->>'id' IS DISTINCT FROM rt.provider_order_id
 OR jsonb_typeof(ord->'purchase_units') IS DISTINCT FROM 'array' OR jsonb_array_length(ord->'purchase_units')<>1
 OR u->>'reference_id' IS DISTINCT FROM o.operation_key OR u->>'custom_id' IS DISTINCT FROM o.operation_key
 OR u->>'merchant_id' IS DISTINCT FROM b.merchant_id OR u->'amount'->>'currency_code' IS DISTINCT FROM 'USD'
 OR u->'amount'->>'value' IS DISTINCT FROM amt
 OR jsonb_typeof(caps) IS DISTINCT FROM 'array' OR jsonb_array_length(caps)>1
 OR ord->>'status' NOT IN ('CREATED','SAVED','PAYER_ACTION_REQUIRED','APPROVED','COMPLETED','VOIDED')
 OR ord->>'status' IS NULL THEN reason:='EXACT_CANONICAL_BINDING_CONFLICT';
 ELSIF jsonb_array_length(caps)=1 THEN
  native_capture:=cap->>'id';
  IF native_capture IS NULL OR native_capture !~ '^[A-Z0-9]{10,32}$'
  OR cap->'amount'->>'currency_code' IS DISTINCT FROM 'USD' OR cap->'amount'->>'value' IS DISTINCT FROM amt
  OR cap->'final_capture' IS DISTINCT FROM 'true'::jsonb
  OR cap->>'status' IS NULL OR cap->>'status' NOT IN ('COMPLETED','PENDING','DECLINED','FAILED','REFUNDED','PARTIALLY_REFUNDED') THEN
   reason:='EXACT_CAPTURE_BINDING_CONFLICT';
  ELSE
   confirmed:=ord->>'status'='COMPLETED' AND cap->>'status'='COMPLETED';
   IF rt.capture_id IS NOT NULL AND rt.capture_id<>native_capture THEN reason:='CAPTURE_IDENTITY_CHANGED'; END IF;
   IF EXISTS(SELECT 1 FROM paypal_v1.runtime WHERE capture_id=native_capture AND operation_id<>opid) THEN
    reason:='CAPTURE_ALREADY_BOUND_TO_ANOTHER_OPERATION'; END IF;
  END IF;
 END IF;
 IF rt.capture_confirmed AND NOT confirmed THEN reason:='CONTRADICTORY_CANONICAL_CAPTURE'; END IF;
 IF confirmed AND NOT EXISTS(SELECT 1 FROM paypal_v1.phase_commits pc WHERE pc.operation_id=opid AND pc.phase='CAPTURE') THEN
  reason:='UNAUTHORIZED_EXTERNAL_CAPTURE'; END IF;
 IF rt.blocked THEN reason:=coalesce(rt.blocked_reason,'RETAINED_EVIDENCE_CONFLICT'); END IF;
 IF reason IS NOT NULL THEN
  UPDATE paypal_v1.runtime SET blocked=true,blocked_reason=reason,latest_receipt_id=rid,
   latest_read_at=clock_timestamp(),revision=revision+1 WHERE operation_id=opid;
  UPDATE dispatch.operation_runtime SET knowledge='BLOCKED',blocked_reason=reason,revision=revision+1 WHERE operation_id=opid;
  UPDATE economic.object_heads SET blocked=true WHERE provider='PAYPAL_SANDBOX' AND account_id=b.merchant_id
   AND object_id=rt.capture_id;
  INSERT INTO economic.conflicts(receipt_id,reason) VALUES(rid,reason);
  UPDATE economic.receipt_states SET interpreted=true,classification=reason WHERE receipt_id=rid;
  INSERT INTO paypal_v1.events(operation_id,kind,receipt_id) VALUES(opid,'CANONICAL_CONFLICT_RETAIN_OBLIGATION',rid);
  RETURN jsonb_build_object('receipt_id',rid,'operation_id',opid,'blocked',true,'capture_confirmed',false);
 END IF;
 UPDATE paypal_v1.runtime SET provider_status=ord->>'status',capture_confirmed=confirmed,
 capture_id=coalesce(native_capture,capture_id),latest_receipt_id=rid,latest_read_at=clock_timestamp(),revision=revision+1
 WHERE operation_id=opid;
 UPDATE dispatch.operation_runtime SET state='RESOLVED_EFFECT',knowledge='CONFIRMED_EFFECT',
 provider_order=rt.provider_order_id,revision=revision+1,updated_at=clock_timestamp() WHERE operation_id=opid;
 IF confirmed THEN
  -- A signed canonical read supplies an observation, never caller-supplied facts/lines.
  sh:=dispatch.hash(jsonb_build_object('operation_id',opid,'capture_id',native_capture,'amount_minor',b.amount_minor,
   'currency','USD','merchant_id',b.merchant_id,'kind','CAPTURED','mapping','paypal-canonical-v1'));
  INSERT INTO economic.observations(receipt_id,channel_id,provider,account_id,integration,tenant_id,operation_id,
   purchase_id,object_id,order_id,kind,amount,currency,strength,payload,semantic_hash,authentication_basis)
  VALUES(rid,c.id,'PAYPAL_SANDBOX',b.merchant_id,o.integration,o.tenant_id,opid,o.domain_root,native_capture,
   rt.provider_order_id,'CAPTURED',b.amount_minor,'USD',3,ord,sh,'AUTHENTICATED_BROKER_CANONICAL_READ_V1')
  ON CONFLICT(channel_id,semantic_hash) DO NOTHING;
  SELECT id INTO oid FROM economic.observations WHERE channel_id=c.id AND semantic_hash=sh;
  INSERT INTO economic.object_heads(provider,account_id,object_id,observation_id,strength)
  VALUES('PAYPAL_SANDBOX',b.merchant_id,native_capture,oid,3) ON CONFLICT DO NOTHING;
 END IF;
 UPDATE economic.receipt_states SET observation_id=oid,interpreted=(oid IS NULL),
 classification=CASE WHEN confirmed THEN 'PAYPAL_EXACT_CAPTURE' ELSE 'PAYPAL_ORDER_ONLY_NOT_SETTLEMENT' END
 WHERE receipt_id=rid;
 INSERT INTO paypal_v1.events(operation_id,kind,receipt_id) VALUES(opid,
  CASE WHEN confirmed THEN 'CANONICAL_CAPTURE_VERIFIED' ELSE 'CANONICAL_ORDER_OBSERVED' END,rid);
 RETURN jsonb_build_object('receipt_id',rid,'operation_id',opid,'blocked',false,'capture_confirmed',confirmed,
 'provider_order_status',ord->>'status','capture_id',native_capture,'currency','USD','amount_minor',b.amount_minor);
END $$;

-- Seller attestations use the same authenticated receipt/fact/posting pipeline,
-- with a USD/native-ID mapper instead of relabeling PayPal data as Razorpay.
CREATE FUNCTION paypal_v1.observe_seller(rid uuid) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE r economic.receipts; s economic.receipt_states; c economic.channels; v jsonb;
 o dispatch.business_operations; cap economic.facts; oid uuid; sh text; BEGIN
 SELECT * INTO s FROM economic.receipt_states WHERE receipt_id=rid FOR UPDATE;
 IF s.observation_id IS NOT NULL THEN RETURN s.observation_id; END IF;
 SELECT * INTO r FROM economic.receipts WHERE id=rid;
 SELECT * INTO c FROM economic.channels WHERE id=r.channel_id;
 IF s.authentication IS DISTINCT FROM 'AUTHENTICATED' OR c.provider<>'CONTROLLED_SELLER'
 OR c.source<>'SELLER_ATTESTATION' OR NOT c.active THEN RETURN NULL; END IF;
 v:=convert_from(public.pgp_sym_decrypt_bytea(r.body,c.secret),'UTF8')::jsonb;
 SELECT * INTO o FROM dispatch.business_operations WHERE id=v->>'operation_id' AND tenant_id=c.tenant_id;
 SELECT * INTO cap FROM economic.facts WHERE operation_id=o.id AND kind='CAPTURE';
 IF o.adapter IS DISTINCT FROM 'PAYPAL_SANDBOX' OR v->>'currency' IS DISTINCT FROM 'USD'
 OR v->>'state' IS DISTINCT FROM 'FULFILLED' OR v->>'account_id' IS DISTINCT FROM o.account_id
 OR v->>'integration' IS DISTINCT FROM o.integration OR v->>'tenant_id' IS DISTINCT FROM o.tenant_id
 OR v->>'purchase_id' IS DISTINCT FROM o.domain_root OR v->>'amount' IS DISTINCT FROM o.amount::text
 OR cap.id IS NULL OR v->>'capture_fact_id' IS DISTINCT FROM cap.id::text
 OR v->>'payment_id' IS DISTINCT FROM cap.effect_id OR v->>'provider_order' IS DISTINCT FROM
 (SELECT provider_order_id FROM paypal_v1.runtime WHERE operation_id=o.id AND capture_confirmed AND NOT blocked) THEN
  UPDATE economic.receipt_states SET interpreted=true,classification='USD_SELLER_BINDING_CONFLICT' WHERE receipt_id=rid;
  INSERT INTO economic.conflicts(receipt_id,reason) VALUES(rid,'USD_SELLER_BINDING_CONFLICT'); RETURN NULL;
 END IF;
 sh:=dispatch.hash(v);
 INSERT INTO economic.observations(receipt_id,channel_id,provider,account_id,integration,tenant_id,operation_id,
 purchase_id,object_id,order_id,kind,amount,currency,strength,payload,semantic_hash,authentication_basis)
 VALUES(rid,c.id,'CONTROLLED_SELLER',o.account_id,o.integration,o.tenant_id,o.id,o.domain_root,v->>'order_id',
 v->>'provider_order','FULFILLED',o.amount,'USD',3,v,sh,'AUTHENTICATED_SELLER_ATTESTATION_V1')
 ON CONFLICT(channel_id,semantic_hash) DO NOTHING;
 SELECT id INTO oid FROM economic.observations WHERE channel_id=c.id AND semantic_hash=sh;
 INSERT INTO economic.object_heads(provider,account_id,object_id,observation_id,strength)
 VALUES('CONTROLLED_SELLER',o.account_id,v->>'order_id',oid,3) ON CONFLICT DO NOTHING;
 UPDATE economic.receipt_states SET observation_id=oid,classification='USD_SELLER_ATTESTATION' WHERE receipt_id=rid;
 RETURN oid;
END $$;

CREATE FUNCTION paypal_v1.process_receipt(rid uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE oid uuid; facts jsonb; fid jsonb; BEGIN
 IF session_user<>'interlock_economic' THEN PERFORM authority.fail('ECONOMIC_ROLE_REQUIRED',403); END IF;
 IF economic.authenticate(rid)<>'AUTHENTICATED' THEN RETURN '[]'::jsonb; END IF;
 SELECT observation_id INTO oid FROM economic.receipt_states WHERE receipt_id=rid;
 IF oid IS NULL THEN oid:=paypal_v1.observe_seller(rid); END IF;
 IF oid IS NULL OR NOT EXISTS(SELECT 1 FROM economic.observations ob JOIN dispatch.business_operations o ON o.id=ob.operation_id
  WHERE ob.id=oid AND o.adapter='PAYPAL_SANDBOX' AND ob.currency='USD') THEN RETURN '[]'::jsonb; END IF;
 IF EXISTS(SELECT 1 FROM paypal_v1.runtime r JOIN economic.observations ob ON ob.operation_id=r.operation_id
  WHERE ob.id=oid AND r.blocked) THEN RETURN '[]'::jsonb; END IF;
 facts:=economic.derive(oid);
 FOR fid IN SELECT value FROM jsonb_array_elements(facts) LOOP PERFORM economic.post((fid#>>'{}')::uuid); END LOOP;
 RETURN facts;
END $$;
