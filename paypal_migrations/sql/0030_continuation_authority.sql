CREATE FUNCTION paypal_v2.assert_enabled() RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v2,pg_temp AS $$
BEGIN
 PERFORM paypal_v1.assert_enabled();
 IF NOT EXISTS(SELECT 1 FROM paypal_v2.deployment WHERE enabled) THEN
  PERFORM authority.fail('CONTINUATION_DISABLED',503); END IF;
END $$;

-- Private common checks. Neither this nor historical_permission grants send authority.
CREATE FUNCTION paypal_v2.scope(opid text,credential_id text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SET search_path=pg_catalog,paypal_v2,pg_temp AS $$
DECLARE o dispatch.business_operations; r paypal_v1.runtime; b paypal_v1.bindings;
 q authority.seller_quotes; e authority.evidence_revisions; g authority.grant_versions;
 policy authority.policy_versions; p jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('paypal-capture-continuation:'||opid,0));
 SELECT * INTO o FROM dispatch.business_operations WHERE id=opid;
 IF o.id IS NULL OR o.adapter<>'PAYPAL_SANDBOX' OR o.provider_mode<>'PAYPAL_SANDBOX'
 OR o.currency<>'USD' OR o.amount<>100 OR o.account_id<>'JB7TR6VHB4GTQ' THEN
  PERFORM authority.fail('CONTINUATION_SCOPE_DENIED',403); END IF;
 p:=paypal_v2.historical_permission(o,false,credential_id);
 PERFORM dispatch.temporal(p);
 -- Match the v1 capture lock order: original requester/approver, issuer, runtime.
 PERFORM principal FROM authority.memberships WHERE tenant_id=o.tenant_id
  AND principal=(SELECT issuer FROM authority.grants WHERE id=o.grant_id) FOR SHARE;
 SELECT * INTO e FROM authority.evidence_revisions WHERE id=o.evidence_id;
 SELECT * INTO q FROM authority.seller_quotes WHERE id=e.quote_id;
 SELECT * INTO g FROM authority.grant_versions WHERE id=o.grant_version_id;
 SELECT * INTO policy FROM authority.policy_versions WHERE id=e.policy_version_id;
 IF q.body->>'merchant_id' IS DISTINCT FROM o.account_id OR q.body->>'provider' IS DISTINCT FROM 'PAYPAL_SANDBOX'
 OR q.currency IS DISTINCT FROM 'USD' OR q.amount<>o.amount OR q.seller<>o.seller
 OR jsonb_array_length(q.body->'lines')<>1 OR q.body->'lines'->0->>'sku' IS DISTINCT FROM 'workspace-seat'
 OR q.body->'lines'->0->'unit_price' IS DISTINCT FROM '100'::jsonb
 OR NOT (q.body->'lines'->0->>'beneficiary'=ANY(g.beneficiaries)) OR policy.max_amount<o.amount THEN
  PERFORM authority.fail('CONTINUATION_POLICY_DENIED',403); END IF;
 SELECT * INTO b FROM paypal_v1.bindings WHERE operation_id=opid;
 SELECT * INTO r FROM paypal_v1.runtime WHERE operation_id=opid FOR UPDATE;
 IF r.blocked OR r.capture_confirmed OR r.capture_id IS NOT NULL OR r.provider_status IS DISTINCT FROM 'APPROVED'
 OR r.provider_order_id IS NULL OR b.merchant_id<>o.account_id OR b.amount_minor<>o.amount OR b.currency<>'USD'
 OR b.wire_body IS DISTINCT FROM paypal_v1.wire(o)
 OR EXISTS(SELECT 1 FROM paypal_v1.cancellations WHERE operation_id=opid)
 OR EXISTS(SELECT 1 FROM paypal_v1.phase_commits WHERE operation_id=opid AND phase='CAPTURE')
 OR NOT EXISTS(SELECT 1 FROM paypal_v1.phase_commits WHERE operation_id=opid AND phase='CREATE' AND request_id=b.create_request_id)
 OR NOT EXISTS(SELECT 1 FROM dispatch.checkout_exposures WHERE operation_id=opid AND provider_order=r.provider_order_id) THEN
  PERFORM authority.fail('CONTINUATION_APPROVED_EXPOSED_ORDER_REQUIRED'); END IF;
 RETURN p||jsonb_build_object('operation',to_jsonb(o),'binding',to_jsonb(b),'runtime',to_jsonb(r),'quote',to_jsonb(q),
  'policy_evaluation',jsonb_build_object('allowed',true,'requires_independent_approval',true,'amount_minor',100,'currency','USD',
   'policy_version_id',policy.id,'evaluated_at',clock_timestamp(),'source','DETERMINISTIC_SQL_CONTINUATION_POLICY'));
END $$;

CREATE FUNCTION paypal_v2.verify_seller(raw text,sig text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v2,pg_temp AS $$
DECLARE secret text; v jsonb; BEGIN
 SELECT k.secret INTO secret FROM authority.issuer_keys k WHERE name='paypal-seller';
 IF secret IS NULL OR length(raw)>32000 OR sig IS DISTINCT FROM encode(public.hmac(raw,secret,'sha256'),'hex') THEN
  PERFORM authority.fail('CONTINUATION_SELLER_SIGNATURE_INVALID',403); END IF;
 v:=raw::jsonb;
 IF raw IS DISTINCT FROM dispatch.canonical(v) OR v->>'schema' IS DISTINCT FROM 'paypal-capture-continuation-attestation-v2' THEN
  PERFORM authority.fail('CONTINUATION_ATTESTATION_INVALID',422); END IF;
 RETURN v;
END $$;

CREATE FUNCTION paypal_v2.seed(token text,opid text,contid uuid,expected bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v2,pg_temp AS $$
DECLARE person authority.memberships; o dispatch.business_operations; s jsonb; q jsonb; result jsonb; prior paypal_v2.intents; BEGIN
 PERFORM paypal_v2.assert_enabled();
 person:=authority.actor(token); PERFORM authority.require_role(person,'PURCHASE_REQUESTER');
 o:=dispatch.user_operation(token,opid,'VIEW');
 IF o.requester<>person.principal OR contid IS NULL THEN PERFORM authority.fail('CONTINUATION_REQUESTER_REQUIRED',403); END IF;
 s:=paypal_v2.scope(opid); q:=s->'quote';
 SELECT * INTO prior FROM paypal_v2.intents WHERE id=contid;
 IF FOUND THEN
  IF prior.operation_id<>opid OR prior.requester<>person.principal OR prior.source_revision IS DISTINCT FROM expected THEN
   PERFORM authority.fail('IDEMPOTENCY_CONFLICT'); END IF;
  RETURN prior.request_raw::jsonb;
 END IF;
 IF (s->'runtime'->>'revision')::bigint IS DISTINCT FROM expected OR (s->'runtime'->>'latest_read_at')::timestamptz<clock_timestamp()-interval '60 seconds'
 OR EXISTS(SELECT 1 FROM paypal_v1.capture_requests WHERE operation_id=opid) THEN
  PERFORM authority.fail('CONTINUATION_FRESH_ORDER_REQUIRED'); END IF;
 result:=jsonb_build_object('schema','paypal-capture-continuation-request-v2','continuation_id',contid,'source_revision',expected,
  'operation_id',opid,'provider_order_id',s->'runtime'->>'provider_order_id',
  'capture_request_id',s->'binding'->>'capture_request_id','reservation_id',o.reservation_id,
  'quote_id',q->>'id','quote_digest',q->>'body_digest','quote',q->'body','amount_minor',100,'currency','USD',
  'merchant_id',o.account_id,'tenant_id',o.tenant_id,'seller',o.seller,
  'beneficiary',q->'body'->'lines'->0->>'beneficiary','product_id','workspace-seat','quantity',1,
  'expires_at',least((s->>'grant_expires_at')::timestamptz,clock_timestamp()+interval '29 minutes'));
 INSERT INTO paypal_v2.intents(id,operation_id,requester,source_revision,request_raw)
 VALUES(contid,opid,person.principal,expected,dispatch.canonical(result));
 RETURN result;
END $$;

CREATE FUNCTION paypal_v2.register(token text,opid text,contid uuid,raw text,sig text,expected bigint,idem text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v2,pg_temp AS $$
DECLARE person authority.memberships; o dispatch.business_operations; prior paypal_v2.commands;
 s jsonb; v jsonb; q jsonb; h text; result jsonb; BEGIN
 PERFORM paypal_v2.assert_enabled(); person:=authority.actor(token); PERFORM authority.require_role(person,'PURCHASE_REQUESTER');
 o:=dispatch.user_operation(token,opid,'VIEW');
 IF o.requester<>person.principal OR idem IS NULL OR idem !~ '^[a-zA-Z0-9_-]{8,100}$' THEN
  PERFORM authority.fail('CONTINUATION_REQUESTER_REQUIRED',403); END IF;
 h:=dispatch.hash(jsonb_build_object('operation_id',opid,'id',contid,'raw',raw,'signature',sig,'expected',expected));
 PERFORM pg_advisory_xact_lock(hashtextextended('continuation-register:'||person.principal||':'||idem,0));
 SELECT * INTO prior FROM paypal_v2.commands WHERE tenant_id=person.tenant_id AND principal=person.principal AND family='register' AND commands.idem=register.idem;
 IF FOUND THEN
  IF prior.body_hash<>h THEN PERFORM authority.fail('IDEMPOTENCY_CONFLICT'); END IF; RETURN prior.result;
 END IF;
 s:=paypal_v2.scope(opid); v:=paypal_v2.verify_seller(raw,sig); q:=s->'quote';
 IF NOT EXISTS(SELECT 1 FROM paypal_v2.intents i WHERE i.id=contid AND i.operation_id=opid AND i.requester=person.principal
   AND i.source_revision=expected AND i.request_raw::jsonb = (v||jsonb_build_object('schema','paypal-capture-continuation-request-v2')))
 OR (s->'runtime'->>'latest_read_at')::timestamptz<clock_timestamp()-interval '60 seconds'
 OR v->>'continuation_id' IS DISTINCT FROM contid::text OR v->>'operation_id' IS DISTINCT FROM opid
 OR v->>'provider_order_id' IS DISTINCT FROM s->'runtime'->>'provider_order_id'
 OR v->>'capture_request_id' IS DISTINCT FROM s->'binding'->>'capture_request_id'
 OR v->>'reservation_id' IS DISTINCT FROM o.reservation_id OR v->>'quote_id' IS DISTINCT FROM q->>'id'
 OR v->>'quote_digest' IS DISTINCT FROM q->>'body_digest' OR v->'quote' IS DISTINCT FROM q->'body'
 OR v->>'tenant_id' IS DISTINCT FROM o.tenant_id OR v->>'merchant_id' IS DISTINCT FROM o.account_id
 OR v->>'seller' IS DISTINCT FROM o.seller OR v->>'beneficiary' IS DISTINCT FROM q->'body'->'lines'->0->>'beneficiary'
 OR v->>'product_id' IS DISTINCT FROM 'workspace-seat' OR v->'quantity' IS DISTINCT FROM '1'::jsonb
 OR v->>'currency' IS DISTINCT FROM 'USD' OR v->'amount_minor' IS DISTINCT FROM '100'::jsonb
 OR (v->>'expires_at')::timestamptz IS NULL OR (v->>'expires_at')::timestamptz<=clock_timestamp()
 OR (v->>'expires_at')::timestamptz>(s->>'grant_expires_at')::timestamptz
 OR (v->>'expires_at')::timestamptz>clock_timestamp()+interval '30 minutes'
 OR EXISTS(SELECT 1 FROM paypal_v1.capture_requests WHERE operation_id=opid) THEN
  PERFORM authority.fail('CONTINUATION_EXACT_BINDING_REQUIRED'); END IF;
 INSERT INTO paypal_v2.continuations(id,operation_id,tenant_id,requester,requester_revision,attestation_raw,attestation_signature,
  digest,policy_evaluation,provider_order_id,capture_request_id,reservation_id,merchant_id,amount_minor,currency,expires_at)
 VALUES(contid,opid,o.tenant_id,person.principal,person.revision,raw,sig,dispatch.hash(v),s->'policy_evaluation',
  v->>'provider_order_id',(v->>'capture_request_id')::uuid,o.reservation_id,o.account_id,100,'USD',(v->>'expires_at')::timestamptz);
 result:=jsonb_build_object('continuation_id',contid,'operation_id',opid,'digest',dispatch.hash(v),'currency','USD','status','AWAITING_INDEPENDENT_APPROVAL');
 INSERT INTO paypal_v2.commands VALUES(person.tenant_id,person.principal,'register',idem,h,result,clock_timestamp());
 RETURN result;
END $$;

CREATE FUNCTION paypal_v2.permission(contid uuid,need_approval boolean,credential_id text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SET search_path=pg_catalog,paypal_v2,pg_temp AS $$
DECLARE c paypal_v2.continuations; a paypal_v2.approvals; m authority.memberships; s jsonb; v jsonb; BEGIN
 SELECT * INTO c FROM paypal_v2.continuations WHERE id=contid;
 IF c.id IS NULL THEN PERFORM authority.fail('CONTINUATION_NOT_FOUND',404); END IF;
 s:=paypal_v2.scope(c.operation_id,credential_id);
 SELECT * INTO c FROM paypal_v2.continuations WHERE id=contid FOR SHARE;
 IF EXISTS(SELECT 1 FROM paypal_v2.revocations WHERE continuation_id=contid) THEN PERFORM authority.fail('CONTINUATION_REVOKED',403); END IF;
 v:=paypal_v2.verify_seller(c.attestation_raw,c.attestation_signature);
 IF c.requester IS DISTINCT FROM s->'operation'->>'requester'
 OR c.requester_revision IS DISTINCT FROM (SELECT revision FROM authority.memberships WHERE principal=c.requester AND tenant_id=c.tenant_id)
 OR c.provider_order_id IS DISTINCT FROM s->'runtime'->>'provider_order_id'
 OR c.capture_request_id::text IS DISTINCT FROM s->'binding'->>'capture_request_id'
 OR c.reservation_id IS DISTINCT FROM s->'operation'->>'reservation_id' OR c.digest<>dispatch.hash(v)
 OR v->'quote' IS DISTINCT FROM s->'quote'->'body' OR c.expires_at<=clock_timestamp() THEN
  PERFORM authority.fail('CONTINUATION_STALE_OR_CONFLICT'); END IF;
 SELECT * INTO a FROM paypal_v2.approvals WHERE continuation_id=contid FOR SHARE;
 IF need_approval THEN
  IF a.id IS NULL OR a.digest<>c.digest OR a.expires_at<=clock_timestamp() THEN PERFORM authority.fail('CONTINUATION_APPROVAL_REQUIRED',403); END IF;
  SELECT * INTO m FROM authority.memberships WHERE principal=a.approver AND tenant_id=c.tenant_id FOR SHARE;
  IF NOT m.active OR m.revision<>a.approver_revision OR NOT 'PURCHASE_APPROVER'=ANY(m.roles)
  OR m.principal=c.requester OR m.principal=(SELECT issuer FROM authority.grants WHERE id=s->'operation'->>'grant_id') THEN
   PERFORM authority.fail('CONTINUATION_APPROVER_CHANGED',403); END IF;
 END IF;
 RETURN s||jsonb_build_object('continuation',to_jsonb(c),'continuation_approval',to_jsonb(a),
  'quote_expires_at',c.expires_at,'approval_expires_at',CASE WHEN need_approval THEN a.expires_at ELSE NULL END);
END $$;

CREATE FUNCTION paypal_v2.approve(token text,opid text,contid uuid,exact_digest text,expires timestamptz,expected bigint,idem text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v2,pg_temp AS $$
DECLARE person authority.memberships; o dispatch.business_operations; s jsonb; prior paypal_v2.commands; h text; result jsonb; aid uuid; BEGIN
 PERFORM paypal_v2.assert_enabled(); person:=authority.actor(token); PERFORM authority.require_role(person,'PURCHASE_APPROVER');
 o:=dispatch.user_operation(token,opid,'VIEW');
 IF person.principal=o.requester OR person.principal=(SELECT issuer FROM authority.grants WHERE id=o.grant_id)
 OR person.principal IS DISTINCT FROM (SELECT approver FROM authority.approvals WHERE id=o.approval_id)
 OR idem IS NULL OR idem !~ '^[a-zA-Z0-9_-]{8,100}$' THEN PERFORM authority.fail('SELF_APPROVAL_DENIED',403); END IF;
 h:=dispatch.hash(jsonb_build_object('operation_id',opid,'continuation_id',contid,'digest',exact_digest,'expires',expires,'expected',expected));
 PERFORM pg_advisory_xact_lock(hashtextextended('continuation-approve:'||person.principal||':'||idem,0));
 SELECT * INTO prior FROM paypal_v2.commands WHERE tenant_id=person.tenant_id AND principal=person.principal AND family='approve' AND commands.idem=approve.idem;
 IF FOUND THEN IF prior.body_hash<>h THEN PERFORM authority.fail('IDEMPOTENCY_CONFLICT'); END IF; RETURN prior.result; END IF;
 IF NOT EXISTS(SELECT 1 FROM paypal_v2.continuations WHERE id=contid AND operation_id=opid) THEN
  PERFORM authority.fail('CONTINUATION_SCOPE_DENIED',403); END IF;
 s:=paypal_v2.permission(contid,false);
 IF s->'continuation'->>'operation_id' IS DISTINCT FROM opid OR s->'continuation'->>'digest' IS DISTINCT FROM exact_digest
 OR expected IS DISTINCT FROM 1 OR expires IS NULL OR expires<=clock_timestamp()
 OR expires>(s->>'quote_expires_at')::timestamptz THEN PERFORM authority.fail('CONTINUATION_EXACT_APPROVAL_REQUIRED'); END IF;
 IF EXISTS(SELECT 1 FROM paypal_v2.approvals WHERE continuation_id=contid) THEN PERFORM authority.fail('CONTINUATION_ALREADY_APPROVED'); END IF;
 INSERT INTO paypal_v2.approvals(continuation_id,approver,approver_revision,digest,expires_at)
 VALUES(contid,person.principal,person.revision,exact_digest,expires) RETURNING id INTO aid;
 result:=jsonb_build_object('approval_id',aid,'continuation_id',contid,'operation_id',opid,'currency','USD','status','APPROVED');
 INSERT INTO paypal_v2.commands VALUES(person.tenant_id,person.principal,'approve',idem,h,result,clock_timestamp());
 RETURN result;
END $$;

CREATE FUNCTION paypal_v2.request_capture(token text,opid text,contid uuid,expected bigint,idem text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v2,pg_temp AS $$
DECLARE person authority.memberships; o dispatch.business_operations; s jsonb; prior paypal_v2.commands; h text; rid uuid; result jsonb; BEGIN
 PERFORM paypal_v2.assert_enabled(); person:=authority.actor(token); PERFORM authority.require_role(person,'PURCHASE_ISSUER');
 o:=dispatch.user_operation(token,opid,'DISCLOSE');
 IF person.principal IS DISTINCT FROM (SELECT issuer FROM authority.grants WHERE id=o.grant_id)
 OR idem IS NULL OR idem !~ '^[a-zA-Z0-9_-]{8,100}$' THEN PERFORM authority.fail('BUYER_OWNER_ROLE_REQUIRED',403); END IF;
 h:=dispatch.hash(jsonb_build_object('operation_id',opid,'continuation_id',contid,'expected',expected));
 PERFORM pg_advisory_xact_lock(hashtextextended('continuation-capture:'||person.principal||':'||idem,0));
 SELECT * INTO prior FROM paypal_v2.commands WHERE tenant_id=person.tenant_id AND principal=person.principal AND family='capture' AND commands.idem=request_capture.idem;
 IF FOUND THEN IF prior.body_hash<>h THEN PERFORM authority.fail('IDEMPOTENCY_CONFLICT'); END IF; RETURN prior.result; END IF;
 IF NOT EXISTS(SELECT 1 FROM paypal_v2.continuations WHERE id=contid AND operation_id=opid) THEN
  PERFORM authority.fail('CONTINUATION_SCOPE_DENIED',403); END IF;
 s:=paypal_v2.permission(contid,true);
 person:=authority.actor(token); PERFORM authority.require_role(person,'PURCHASE_ISSUER');
 PERFORM dispatch.temporal(s);
 IF s->'continuation'->>'operation_id' IS DISTINCT FROM opid OR (s->'runtime'->>'revision')::bigint IS DISTINCT FROM expected
 OR (s->'runtime'->>'latest_read_at')::timestamptz<clock_timestamp()-interval '60 seconds' THEN
  PERFORM authority.fail('FRESH_APPROVED_EXACT_ORDER_REQUIRED'); END IF;
 INSERT INTO paypal_v1.capture_requests(operation_id,principal,principal_revision,idempotency_key,request_hash,
  action_id,approval_id,reservation_id,provider_order_id,merchant_id,amount_minor,currency)
 VALUES(opid,person.principal,person.revision,idem,h,o.action_id,o.approval_id,o.reservation_id,
  s->'runtime'->>'provider_order_id',o.account_id,100,'USD') RETURNING id INTO rid;
 INSERT INTO paypal_v2.capture_links VALUES(rid,contid,(s->'continuation_approval'->>'id')::uuid,clock_timestamp());
 result:=jsonb_build_object('capture_authorization_id',rid,'continuation_id',contid,'operation_id',opid,'currency','USD');
 INSERT INTO paypal_v2.commands VALUES(person.tenant_id,person.principal,'capture',idem,h,result,clock_timestamp());
 RETURN result;
END $$;

CREATE FUNCTION paypal_v2.commit_capture(secret text,opid text,contid uuid,expected_digest text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v2,pg_temp AS $$
DECLARE o dispatch.business_operations; c dispatch.service_credentials; s jsonb; r paypal_v1.runtime;
 b paypal_v1.bindings; cr paypal_v1.capture_requests; link paypal_v2.capture_links; BEGIN
 PERFORM paypal_v2.assert_enabled(); SELECT * INTO o FROM dispatch.business_operations WHERE id=opid;
 c:=dispatch.service_actor(secret,o.tenant_id,'DISPATCH');
 IF NOT EXISTS(SELECT 1 FROM paypal_v2.continuations WHERE id=contid AND operation_id=opid) THEN
  PERFORM authority.fail('CONTINUATION_SCOPE_DENIED',403); END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('paypal-capture-continuation:'||opid,0));
 -- Duplicate commits are GET-only, including after a lost provider response.
 IF EXISTS(SELECT 1 FROM paypal_v1.phase_commits WHERE operation_id=opid AND phase='CAPTURE') THEN
  IF NOT EXISTS(SELECT 1 FROM paypal_v2.capture_links l JOIN paypal_v1.capture_requests q ON q.id=l.capture_authorization_id
    WHERE l.continuation_id=contid AND q.operation_id=opid) THEN PERFORM authority.fail('CONTINUATION_CAPTURE_CONFLICT'); END IF;
  RETURN jsonb_build_object('send',false,'operation_id',opid,'phase','CAPTURE','recovery','CANONICAL_READ_ONLY');
 END IF;
 s:=paypal_v2.permission(contid,true,c.id);
 IF s->'continuation'->>'operation_id' IS DISTINCT FROM opid THEN PERFORM authority.fail('CONTINUATION_EXACT_BINDING_REQUIRED'); END IF;
 SELECT * INTO r FROM paypal_v1.runtime WHERE operation_id=opid;
 SELECT * INTO b FROM paypal_v1.bindings WHERE operation_id=opid;
 SELECT * INTO cr FROM paypal_v1.capture_requests WHERE operation_id=opid;
 SELECT * INTO link FROM paypal_v2.capture_links WHERE continuation_id=contid;
 PERFORM principal FROM authority.memberships WHERE principal=cr.principal AND tenant_id=o.tenant_id FOR SHARE;
 IF cr.id IS NULL OR link.capture_authorization_id IS DISTINCT FROM cr.id
 OR link.approval_id IS DISTINCT FROM (s->'continuation_approval'->>'id')::uuid
 OR cr.action_id<>o.action_id OR cr.approval_id<>o.approval_id OR cr.reservation_id<>o.reservation_id
 OR cr.provider_order_id IS DISTINCT FROM r.provider_order_id OR cr.merchant_id<>o.account_id OR cr.amount_minor<>o.amount OR cr.currency<>'USD'
 OR NOT EXISTS(SELECT 1 FROM authority.memberships m JOIN authority.grants g ON g.issuer=m.principal
   WHERE g.id=o.grant_id AND m.principal=cr.principal AND m.tenant_id=o.tenant_id AND m.active AND m.revision=cr.principal_revision AND 'PURCHASE_ISSUER'=ANY(m.roles))
 OR r.latest_read_at<clock_timestamp()-interval '60 seconds' OR expected_digest IS DISTINCT FROM dispatch.hash('{}'::jsonb)
 OR NOT EXISTS(SELECT 1 FROM dispatch.integrations WHERE tenant_id=o.tenant_id AND account_id=o.account_id AND real_mutations_enabled) THEN
  PERFORM authority.fail('SEPARATE_EXACT_CAPTURE_AUTHORIZATION_REQUIRED'); END IF;
 PERFORM dispatch.temporal(s); c:=dispatch.service_actor(secret,o.tenant_id,'DISPATCH');
 INSERT INTO paypal_v1.phase_commits(operation_id,phase,request_id,wire_digest,capture_request_id,provider_order_id,service_credential_id,permission)
 VALUES(opid,'CAPTURE',b.capture_request_id,expected_digest,cr.id,r.provider_order_id,c.id,
  s-'operation'-'binding'-'runtime'-'quote'||jsonb_build_object('contract_version','capture-continuation-v2'));
 UPDATE dispatch.operation_runtime SET state='DISPATCH_COMMITTED',knowledge='UNKNOWN',revision=revision+1,updated_at=clock_timestamp() WHERE operation_id=opid;
 INSERT INTO paypal_v1.events(operation_id,kind) VALUES(opid,'CAPTURE_CONTINUATION_COMMITTED_OUTCOME_UNKNOWN');
 RETURN jsonb_build_object('send',true,'operation_id',opid,'phase','CAPTURE','request_id',b.capture_request_id,'wire_body','{}'::jsonb,'provider_order_id',r.provider_order_id);
END $$;

CREATE FUNCTION paypal_v2.inspect(token text,contid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v2,pg_temp AS $$
DECLARE c paypal_v2.continuations; BEGIN
 SELECT * INTO c FROM paypal_v2.continuations WHERE id=contid;
 IF c.id IS NULL THEN PERFORM authority.fail('CONTINUATION_NOT_FOUND',404); END IF;
 PERFORM dispatch.user_operation(token,c.operation_id,'VIEW');
 RETURN to_jsonb(c)||jsonb_build_object('approval',(SELECT to_jsonb(a) FROM paypal_v2.approvals a WHERE continuation_id=contid),
  'revoked',EXISTS(SELECT 1 FROM paypal_v2.revocations WHERE continuation_id=contid),'expired',c.expires_at<=clock_timestamp(),
  'capture_authorization_id',(SELECT capture_authorization_id FROM paypal_v2.capture_links WHERE continuation_id=contid),'financial_authority',false);
END $$;

CREATE FUNCTION paypal_v2.revoke(token text,opid text,contid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v2,pg_temp AS $$
DECLARE person authority.memberships; o dispatch.business_operations; BEGIN
 PERFORM paypal_v2.assert_enabled(); person:=authority.actor(token); PERFORM authority.require_role(person,'PURCHASE_ISSUER');
 o:=dispatch.user_operation(token,opid,'VIEW');
 PERFORM pg_advisory_xact_lock(hashtextextended('paypal-capture-continuation:'||opid,0));
 PERFORM principal FROM authority.memberships WHERE principal=person.principal FOR SHARE;
 person:=authority.actor(token); PERFORM authority.require_role(person,'PURCHASE_ISSUER');
 IF person.principal IS DISTINCT FROM (SELECT issuer FROM authority.grants WHERE id=o.grant_id)
 OR NOT EXISTS(SELECT 1 FROM paypal_v2.continuations WHERE id=contid AND operation_id=opid) THEN
  PERFORM authority.fail('CONTINUATION_SCOPE_DENIED',403); END IF;
 INSERT INTO paypal_v2.revocations(continuation_id,principal) VALUES(contid,person.principal) ON CONFLICT DO NOTHING;
 RETURN jsonb_build_object('continuation_id',contid,'operation_id',opid,'currency','USD','revoked',true,'reservation_released',false);
END $$;
