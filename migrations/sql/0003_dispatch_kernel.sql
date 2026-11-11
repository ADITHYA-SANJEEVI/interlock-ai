CREATE FUNCTION dispatch.canonical(v jsonb) RETURNS text LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,dispatch,pg_temp AS $$
DECLARE result text; BEGIN
 CASE jsonb_typeof(v)
 WHEN 'object' THEN SELECT '{'||coalesce(string_agg(to_jsonb(key)::text||':'||dispatch.canonical(value),',' ORDER BY key COLLATE "C"),'')||'}' INTO result FROM jsonb_each(v);
 WHEN 'array' THEN SELECT '['||coalesce(string_agg(dispatch.canonical(value),',' ORDER BY ord),'')||']' INTO result FROM jsonb_array_elements(v) WITH ORDINALITY a(value,ord);
 ELSE result:=v::text; END CASE; RETURN result;
END $$;
CREATE FUNCTION dispatch.hash(v jsonb) RETURNS text LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,dispatch,pg_temp AS $$
 SELECT encode(public.digest(dispatch.canonical(v),'sha256'),'hex') $$;
CREATE FUNCTION dispatch.fail(code text,status int DEFAULT 409) RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog AS $$
 BEGIN RAISE EXCEPTION '%',code USING ERRCODE='P0001',DETAIL=status::text; END $$;

CREATE FUNCTION dispatch.service_actor(secret text,tid text,verb text) RETURNS dispatch.service_credentials
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,dispatch,pg_temp AS $$
DECLARE c dispatch.service_credentials; BEGIN
 IF (verb IN ('DISPATCH','OBSERVE') AND session_user<>'interlock_broker') OR (verb='CLAIM' AND session_user<>'interlock_worker') THEN
  PERFORM dispatch.fail('SERVICE_ROLE_DENIED',403); END IF;
 SELECT * INTO c FROM dispatch.service_credentials WHERE token_hash=encode(public.digest(secret,'sha256'),'hex');
 IF c.id IS NULL OR NOT c.active OR c.expires_at<=clock_timestamp() OR NOT EXISTS(
 SELECT 1 FROM dispatch.service_scopes s WHERE s.credential_id=c.id AND s.tenant_id=tid AND verb=ANY(s.verbs)) THEN
  PERFORM dispatch.fail('SERVICE_CREDENTIAL_DENIED',403); END IF;
 RETURN c;
END $$;

CREATE FUNCTION dispatch.user_operation(token text,opid text,verb text DEFAULT 'VIEW') RETURNS dispatch.business_operations
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,dispatch,pg_temp AS $$
DECLARE person authority.memberships; o dispatch.business_operations; g authority.grants; BEGIN
 person:=authority.actor(token);
 SELECT * INTO o FROM dispatch.business_operations WHERE id=opid AND tenant_id=person.tenant_id;
 SELECT * INTO g FROM authority.grants WHERE id=o.grant_id AND tenant_id=person.tenant_id;
 IF o.id IS NULL OR NOT (o.requester=person.principal OR g.issuer=person.principal OR 'PURCHASE_APPROVER'=ANY(person.roles)) THEN
  PERFORM dispatch.fail('OPERATION_NOT_VISIBLE',403); END IF;
 IF verb='OBSERVE' AND NOT EXISTS(SELECT 1 FROM dispatch.observation_memberships
 WHERE tenant_id=person.tenant_id AND principal=person.principal AND active) THEN PERFORM dispatch.fail('OBSERVATION_PERMISSION_REQUIRED',403); END IF;
 IF verb='DISCLOSE' AND person.principal<>g.issuer THEN PERFORM dispatch.fail('BUYER_OWNER_REQUIRED',403); END IF;
 RETURN o;
END $$;

-- Trigger continuation of Pass1 admission: queue identity is not permission; no additional reservation.
CREATE FUNCTION dispatch.admit_operation() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,dispatch,pg_temp AS $$
DECLARE a authority.action_revisions; e authority.evidence_revisions; d authority.policy_decisions;
 r authority.reservations; gv authority.grant_versions; ap text; i dispatch.integrations; c dispatch.replay_contracts;
 opid text:='op_'||replace(gen_random_uuid()::text,'-',''); operation_key text; payload jsonb;
BEGIN
 SELECT * INTO a FROM authority.action_revisions WHERE id=NEW.action_id;
 IF a.kind<>'PURCHASE' THEN RETURN NEW; END IF;
 SELECT * INTO e FROM authority.evidence_revisions WHERE action_id=a.id;
 SELECT * INTO d FROM authority.policy_decisions WHERE action_id=a.id;
 SELECT * INTO r FROM authority.reservations WHERE id=NEW.reservation_id;
 SELECT * INTO gv FROM authority.grant_versions WHERE id=r.grant_version_id;
 SELECT approval_id INTO ap FROM authority.approval_heads WHERE consumed_by=r.id;
 INSERT INTO dispatch.integrations(tenant_id,integration,adapter,account_id,replay_contract_id)
 VALUES(a.tenant_id,gv.integration,'REFERENCE_SYNTHETIC','reference_'||a.tenant_id,'reference-create-v1') ON CONFLICT DO NOTHING;
 SELECT * INTO i FROM dispatch.integrations WHERE tenant_id=a.tenant_id;
 SELECT * INTO c FROM dispatch.replay_contracts WHERE id=i.replay_contract_id;
 IF i.integration<>gv.integration OR c.adapter<>i.adapter THEN PERFORM dispatch.fail('INTEGRATION_BINDING_CONFLICT'); END IF;
 operation_key:='r_'||opid;
 payload:=jsonb_build_object('amount',a.amount,'currency',a.currency,'receipt',operation_key,'partial_payment',false,
 'notes',jsonb_build_object('interlock_operation',operation_key,'interlock_integration',i.integration,'interlock_account',i.account_id));
 INSERT INTO dispatch.business_operations VALUES(opid,a.tenant_id,'CREATE_ORDER',a.intent_id,a.id,e.id,d.id,ap,a.grant_id,
 r.grant_version_id,r.epoch,e.config_epoch,r.id,NEW.id,a.requester,i.integration,i.adapter,i.adapter,i.account_id,c.version,
 'NEW_ORDER_BY_EXACT_RECEIPT',operation_key,a.amount,a.currency,gv.seller,a.request_digest,e.digest,payload,dispatch.hash(payload),c.id,clock_timestamp());
 INSERT INTO dispatch.operation_runtime(operation_id,tenant_id,state,knowledge) VALUES(opid,a.tenant_id,'QUEUED','NOT_ATTEMPTED');
 INSERT INTO dispatch.reservation_exposure VALUES(r.id,a.tenant_id,'RESERVED_UNEXPOSED');
 INSERT INTO dispatch.outbox_items(id,tenant_id,operation_id,purpose) VALUES('send_'||opid,a.tenant_id,opid,'SEND');
 INSERT INTO dispatch.outbox_items(id,tenant_id,operation_id,purpose,ready_at) VALUES('observe_'||opid,a.tenant_id,opid,'OBSERVE','infinity');
 INSERT INTO dispatch.audit_events VALUES('audit_'||gen_random_uuid()::text,a.tenant_id,opid,'ADMISSION_QUEUED_NO_SEND_PERMISSION',NEW.id,clock_timestamp());
 RETURN NEW;
END $$;
CREATE TRIGGER dispatch_admitted AFTER INSERT ON authority.admitted_work FOR EACH ROW EXECUTE FUNCTION dispatch.admit_operation();

CREATE FUNCTION dispatch.claim(secret text,lease_seconds int DEFAULT 30,only_operation text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,dispatch,pg_temp AS $$
DECLARE q dispatch.outbox_items; c dispatch.service_credentials; claimid text:='claim_'||gen_random_uuid()::text; until_at timestamptz;
BEGIN
 IF lease_seconds NOT BETWEEN 1 AND 60 THEN PERFORM dispatch.fail('INVALID_LEASE',422); END IF;
 SELECT * INTO c FROM dispatch.service_credentials WHERE token_hash=encode(public.digest(secret,'sha256'),'hex');
 IF c.id IS NULL OR session_user<>'interlock_worker' OR NOT c.active OR c.expires_at<=clock_timestamp() THEN PERFORM dispatch.fail('SERVICE_CREDENTIAL_DENIED',403); END IF;
 SELECT b.* INTO q FROM dispatch.outbox_items b JOIN dispatch.operation_runtime rt ON rt.operation_id=b.operation_id
 JOIN dispatch.service_scopes s ON s.tenant_id=b.tenant_id AND s.credential_id=c.id
 WHERE 'CLAIM'=ANY(s.verbs) AND NOT b.completed AND b.ready_at<=clock_timestamp()
 AND (b.lease_until IS NULL OR b.lease_until<=clock_timestamp()) AND (only_operation IS NULL OR b.operation_id=only_operation)
 AND (b.purpose='OBSERVE' OR rt.attempt_count=0 AND rt.state IN ('QUEUED','READY'))
 ORDER BY b.ready_at,b.id FOR UPDATE OF b SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN NULL; END IF;
 PERFORM dispatch.service_actor(secret,q.tenant_id,'CLAIM');
 until_at:=clock_timestamp()+make_interval(secs=>lease_seconds);
 UPDATE dispatch.outbox_items SET claim_id=claimid,credential_id=c.id,lease_until=until_at WHERE id=q.id;
 INSERT INTO dispatch.queue_claims VALUES(claimid,q.tenant_id,q.operation_id,c.id,q.purpose,clock_timestamp(),until_at);
 RETURN jsonb_build_object('operation_id',q.operation_id,'claim_id',claimid,'purpose',q.purpose,'lease_until',until_at);
END $$;

CREATE FUNCTION dispatch.binding(secret text,opid text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,dispatch,pg_temp AS $$
DECLARE o dispatch.business_operations; r dispatch.operation_runtime; BEGIN
 SELECT * INTO o FROM dispatch.business_operations WHERE id=opid;
 IF o.id IS NULL THEN PERFORM dispatch.fail('OPERATION_NOT_VISIBLE',403); END IF;
 PERFORM dispatch.service_actor(secret,o.tenant_id,'OBSERVE');
 SELECT * INTO r FROM dispatch.operation_runtime WHERE operation_id=opid;
 RETURN jsonb_build_object('operation',to_jsonb(o),'runtime',to_jsonb(r));
END $$;

CREATE FUNCTION dispatch.ready(secret text,opid text,claimid text) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,dispatch,pg_temp AS $$
DECLARE o dispatch.business_operations; BEGIN
 SELECT * INTO o FROM dispatch.business_operations WHERE id=opid;
 PERFORM dispatch.service_actor(secret,o.tenant_id,'DISPATCH');
 IF NOT EXISTS(SELECT 1 FROM dispatch.outbox_items WHERE operation_id=opid AND purpose='SEND' AND claim_id=claimid AND lease_until>clock_timestamp()) THEN
 PERFORM dispatch.fail('QUEUE_LEASE_EXPIRED'); END IF;
 UPDATE dispatch.operation_runtime SET state='READY',revision=revision+1,updated_at=clock_timestamp()
 WHERE operation_id=opid AND state='QUEUED' AND attempt_count=0;
END $$;

-- Ordered current-authority locks. This function holds no monetary UPDATE privileges.
CREATE FUNCTION dispatch.current_permission(o dispatch.business_operations,quote_deadline boolean,credential_id text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SET search_path=pg_catalog,dispatch,pg_temp AS $$
DECLARE cfg authority.tenant_control; gh authority.grant_heads; gv authority.grant_versions; ih authority.intent_heads;
 a authority.action_revisions; e authority.evidence_revisions; d authority.policy_decisions; ap authority.approvals;
 aph authority.approval_heads; r authority.reservations; rh authority.reservation_heads; member authority.memberships;
 q authority.seller_quotes; qh authority.quote_heads; now_at timestamptz; service dispatch.service_credentials;
BEGIN
 SELECT * INTO cfg FROM authority.tenant_control WHERE tenant_id=o.tenant_id AND kind='PURCHASE' FOR SHARE;
 IF credential_id IS NOT NULL THEN SELECT * INTO service FROM dispatch.service_credentials WHERE id=credential_id FOR SHARE; END IF;
 SELECT * INTO gh FROM authority.grant_heads WHERE grant_id=o.grant_id AND tenant_id=o.tenant_id FOR UPDATE;
 SELECT * INTO gv FROM authority.grant_versions WHERE id=o.grant_version_id AND tenant_id=o.tenant_id;
 SELECT * INTO a FROM authority.action_revisions WHERE id=o.action_id AND tenant_id=o.tenant_id;
 SELECT * INTO ih FROM authority.intent_heads WHERE intent_id=a.intent_id AND tenant_id=o.tenant_id FOR UPDATE;
 SELECT * INTO e FROM authority.evidence_revisions WHERE id=o.evidence_id AND tenant_id=o.tenant_id;
 SELECT * INTO d FROM authority.policy_decisions WHERE id=o.decision_id AND tenant_id=o.tenant_id;
 SELECT * INTO q FROM authority.seller_quotes WHERE id=e.quote_id AND tenant_id=o.tenant_id;
 SELECT * INTO qh FROM authority.quote_heads WHERE quote_id=e.quote_id AND tenant_id=o.tenant_id FOR SHARE;
 IF o.approval_id IS NOT NULL THEN
 SELECT * INTO ap FROM authority.approvals WHERE id=o.approval_id AND tenant_id=o.tenant_id;
 SELECT * INTO aph FROM authority.approval_heads WHERE approval_id=ap.id AND tenant_id=o.tenant_id FOR SHARE;
 END IF;
 PERFORM principal FROM authority.memberships WHERE tenant_id=o.tenant_id AND principal IN (a.requester,ap.approver) ORDER BY principal FOR SHARE;
 SELECT * INTO member FROM authority.memberships WHERE principal=a.requester AND tenant_id=o.tenant_id;
 SELECT * INTO r FROM authority.reservations WHERE id=o.reservation_id AND tenant_id=o.tenant_id;
 SELECT * INTO rh FROM authority.reservation_heads WHERE reservation_id=r.id AND tenant_id=o.tenant_id FOR SHARE;
 now_at:=clock_timestamp();
 IF gh.status<>'ACTIVE' THEN PERFORM dispatch.fail('AUTHORITY_'||gh.status,403); END IF;
 IF gv.expires_at<=now_at THEN PERFORM dispatch.fail('AUTHORITY_EXPIRED',403); END IF;
 IF cfg.paused THEN PERFORM dispatch.fail('TENANT_MUTATIONS_PAUSED',403); END IF;
 IF gh.version_id<>o.grant_version_id OR gh.epoch<>o.grant_epoch THEN PERFORM dispatch.fail('GRANT_EPOCH_CHANGED'); END IF;
 IF cfg.epoch<>o.config_epoch OR cfg.policy_version_id<>e.policy_version_id OR d.policy_version_id<>cfg.policy_version_id THEN PERFORM dispatch.fail('POLICY_CHANGED'); END IF;
 IF ih.action_id<>a.id OR ih.intent_revision_id<>a.intent_revision_id OR ih.status<>'ADMITTED' THEN PERFORM dispatch.fail('ACTION_STALE'); END IF;
 IF e.action_id<>a.id OR d.action_id<>a.id OR d.evidence_id<>e.id OR e.digest<>o.evidence_digest
 OR a.request_digest<>o.action_digest OR qh.revision<>e.quote_revision THEN PERFORM dispatch.fail('EVIDENCE_STALE'); END IF;
 IF quote_deadline AND q.expires_at<=now_at THEN PERFORM dispatch.fail('QUOTE_EXPIRED'); END IF;
 IF NOT member.active OR member.revision<>a.requester_revision OR NOT 'PURCHASE_REQUESTER'=ANY(member.roles) THEN PERFORM dispatch.fail('REQUESTER_CHANGED',403); END IF;
 IF NOT d.allowed THEN PERFORM dispatch.fail('POLICY_DENIED',403); END IF;
 IF d.approval_required THEN
 SELECT * INTO member FROM authority.memberships WHERE principal=ap.approver AND tenant_id=o.tenant_id;
 IF ap.id IS NULL OR ap.action_id<>a.id OR ap.evidence_id<>e.id OR ap.decision_id<>d.id OR ap.evidence_digest<>e.digest
 OR ap.approver=a.requester OR ap.expires_at<=now_at OR aph.status<>'CONSUMED' OR aph.consumed_by<>r.id
 OR NOT member.active OR member.revision<>ap.approver_revision OR NOT 'PURCHASE_APPROVER'=ANY(member.roles) THEN PERFORM dispatch.fail('APPROVAL_INVALID'); END IF;
 ELSIF o.approval_id IS NOT NULL THEN PERFORM dispatch.fail('APPROVAL_BINDING_INVALID'); END IF;
 IF r.action_id<>a.id OR r.intent_revision_id<>a.intent_revision_id OR r.grant_id<>o.grant_id OR r.grant_version_id<>o.grant_version_id
 OR r.epoch<>o.grant_epoch OR r.amount<>o.amount OR r.currency<>o.currency OR r.source_terms_digest<>a.request_digest OR rh.status<>'RESERVED' THEN PERFORM dispatch.fail('RESERVATION_INVALID'); END IF;
 IF dispatch.hash(o.body)<>o.request_digest OR o.amount<>a.amount OR o.currency<>a.currency OR o.integration<>gv.integration OR o.seller<>gv.seller THEN PERFORM dispatch.fail('EXACT_OPERATION_BINDING_CONFLICT'); END IF;
 RETURN jsonb_build_object('grant_expires_at',gv.expires_at,'approval_expires_at',ap.expires_at,
 'policy_version_id',cfg.policy_version_id,'quote_expires_at',CASE WHEN quote_deadline THEN q.expires_at ELSE NULL END,
 'service_expires_at',service.expires_at);
END $$;
CREATE FUNCTION dispatch.temporal(p jsonb) RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog,dispatch,pg_temp AS $$
BEGIN
 IF (p->>'grant_expires_at')::timestamptz<=clock_timestamp() THEN PERFORM dispatch.fail('AUTHORITY_EXPIRED',403); END IF;
 IF (p->>'approval_expires_at')::timestamptz<=clock_timestamp() THEN PERFORM dispatch.fail('APPROVAL_INVALID'); END IF;
 IF (p->>'quote_expires_at')::timestamptz<=clock_timestamp() THEN PERFORM dispatch.fail('QUOTE_EXPIRED'); END IF;
 IF (p->>'service_expires_at')::timestamptz<=clock_timestamp() THEN PERFORM dispatch.fail('SERVICE_CREDENTIAL_DENIED',403); END IF;
END $$;

CREATE FUNCTION dispatch.commit_handoff(secret text,opid text,claimid text,expected_digest text,replay boolean DEFAULT false) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,dispatch,pg_temp AS $$
DECLARE o dispatch.business_operations; rt dispatch.operation_runtime; c dispatch.service_credentials;
 contract dispatch.replay_contracts; permission jsonb; first_at timestamptz; now_at timestamptz;
 attemptid text:='attempt_'||gen_random_uuid()::text; recordid text:='dispatch_'||gen_random_uuid()::text;
 dutyid text:='duty_'||gen_random_uuid()::text; n int;
BEGIN
 SELECT * INTO o FROM dispatch.business_operations WHERE id=opid;
 IF o.id IS NULL THEN PERFORM dispatch.fail('OPERATION_NOT_VISIBLE',403); END IF;
 c:=dispatch.service_actor(secret,o.tenant_id,'DISPATCH');
 IF expected_digest IS DISTINCT FROM o.request_digest THEN PERFORM dispatch.fail('EXACT_OPERATION_BINDING_CONFLICT'); END IF;
 IF o.adapter='RAZORPAY_TEST' AND NOT EXISTS(SELECT 1 FROM dispatch.integrations WHERE tenant_id=o.tenant_id AND real_mutations_enabled) THEN PERFORM dispatch.fail('REAL_MUTATIONS_DISABLED',403); END IF;
 permission:=dispatch.current_permission(o,true,c.id);
 SELECT * INTO rt FROM dispatch.operation_runtime WHERE operation_id=opid FOR UPDATE;
 PERFORM dispatch.temporal(permission);
 PERFORM dispatch.service_actor(secret,o.tenant_id,'DISPATCH');
 SELECT * INTO contract FROM dispatch.replay_contracts WHERE id=o.replay_contract_id;
 IF rt.attempt_count=0 THEN
 IF replay OR rt.state NOT IN ('QUEUED','READY') THEN PERFORM dispatch.fail('INVALID_FIRST_ATTEMPT'); END IF;
 IF NOT EXISTS(SELECT 1 FROM dispatch.outbox_items WHERE operation_id=opid AND purpose='SEND' AND claim_id=claimid AND lease_until>clock_timestamp() AND NOT completed) THEN PERFORM dispatch.fail('QUEUE_LEASE_EXPIRED'); END IF;
 ELSE
 IF NOT replay THEN PERFORM dispatch.fail('ALREADY_COMMITTED_OBSERVE_ONLY'); END IF;
 IF rt.knowledge IN ('CONFIRMED_EFFECT','CONFIRMED_ZERO_EFFECT') THEN PERFORM dispatch.fail('ALREADY_RESOLVED'); END IF;
 SELECT min(committed_at) INTO first_at FROM dispatch.dispatch_records WHERE operation_id=opid;
 IF o.adapter<>'REFERENCE_SYNTHETIC' OR contract.classification='NEVER_AUTOMATIC_REPLAY' OR NOT contract.verified
 OR first_at+make_interval(secs=>contract.retention_seconds)<=clock_timestamp() THEN PERFORM dispatch.fail('REPLAY_ENVELOPE_UNVERIFIED_OR_EXPIRED'); END IF;
 IF contract.classification='READ_BEFORE_REPLAY' AND NOT EXISTS(SELECT 1 FROM dispatch.operation_observations WHERE operation_id=opid AND source='CANONICAL_EXACT_READ' AND observed_at>=rt.updated_at) THEN PERFORM dispatch.fail('REPLAY_READ_REQUIRED'); END IF;
 END IF;
 now_at:=clock_timestamp(); n:=rt.attempt_count+1;
 INSERT INTO dispatch.transport_attempts VALUES(attemptid,o.tenant_id,o.id,n,now_at,o.request_digest,o.operation_key);
 INSERT INTO dispatch.attempt_runtime VALUES(attemptid,o.tenant_id,'COMMITTED_TO_HANDOFF',NULL,NULL);
 INSERT INTO dispatch.dispatch_records VALUES(recordid,o.tenant_id,o.id,attemptid,n,now_at,c.id,c.revision,c.expires_at,
 o.grant_id,o.grant_version_id,o.grant_epoch,(permission->>'grant_expires_at')::timestamptz,o.config_epoch,permission->>'policy_version_id',
 o.action_id,o.evidence_id,o.decision_id,o.approval_id,(permission->>'approval_expires_at')::timestamptz,o.reservation_id,
 o.request_digest,o.action_digest,o.evidence_digest,o.replay_contract_id,CASE WHEN replay THEN 'CURRENT_VERIFIED_SAME_KEY_REPLAY' ELSE 'CURRENT_FIRST_HANDOFF' END);
 UPDATE dispatch.operation_runtime SET state='DISPATCH_COMMITTED',knowledge='UNKNOWN',attempt_count=n,revision=revision+1,blocked_reason=NULL,updated_at=now_at WHERE operation_id=opid;
 INSERT INTO dispatch.recovery_obligations VALUES(dutyid,o.tenant_id,'OPERATION',o.id,NULL,o.reservation_id,'RESOLVE_EXTERNAL_EFFECT',
 'EXACT_ORDER_EFFECT','OBSERVE',true,o.replay_contract_id,'dispatch-duty-v1',now_at) ON CONFLICT(operation_id,origin_kind,duty_kind) DO NOTHING;
 INSERT INTO dispatch.duty_runtime SELECT id,tenant_id,'OPEN',NULL,NULL,now_at FROM dispatch.recovery_obligations WHERE operation_id=opid AND origin_kind='OPERATION' ON CONFLICT DO NOTHING;
 UPDATE dispatch.outbox_items SET completed=true WHERE operation_id=opid AND purpose='SEND';
 UPDATE dispatch.outbox_items SET ready_at=now_at+interval '10 seconds',completed=false WHERE operation_id=opid AND purpose='OBSERVE';
 INSERT INTO dispatch.audit_events VALUES('audit_'||gen_random_uuid()::text,o.tenant_id,o.id,'HANDOFF_COMMITTED',recordid,now_at);
 RETURN jsonb_build_object('attempt_id',attemptid,'dispatch_id',recordid,'number',n,'operation_id',opid,'request_digest',o.request_digest);
END $$;

CREATE FUNCTION dispatch.record_observation(secret text,opid text,result jsonb,attemptid text DEFAULT NULL,transport_state text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,dispatch,pg_temp AS $$
DECLARE o dispatch.business_operations; rt dispatch.operation_runtime; obsid text:='obs_'||gen_random_uuid()::text;
 source_kind text; classification text; remote jsonb; state_value text; knowledge_value text;
BEGIN
 SELECT * INTO o FROM dispatch.business_operations WHERE id=opid;
 IF o.id IS NULL THEN PERFORM dispatch.fail('OPERATION_NOT_VISIBLE',403); END IF;
 PERFORM dispatch.service_actor(secret,o.tenant_id,'OBSERVE');
 SELECT * INTO rt FROM dispatch.operation_runtime WHERE operation_id=opid FOR UPDATE;
 PERFORM dispatch.service_actor(secret,o.tenant_id,'OBSERVE');
 IF rt.attempt_count=0 THEN PERFORM dispatch.fail('NOT_COMMITTED'); END IF;
 IF attemptid IS NOT NULL AND NOT EXISTS(SELECT 1 FROM dispatch.transport_attempts WHERE id=attemptid AND operation_id=opid) THEN PERFORM dispatch.fail('ATTEMPT_NOT_BOUND'); END IF;
 IF (attemptid IS NULL)<>(transport_state IS NULL) THEN PERFORM dispatch.fail('INVALID_TRANSPORT_STATE',422); END IF;
 IF transport_state IS NOT NULL AND transport_state NOT IN ('RESPONSE_RECORDED','INTERRUPTED','TIMED_OUT') THEN PERFORM dispatch.fail('INVALID_TRANSPORT_STATE',422); END IF;
 source_kind:=CASE WHEN transport_state IN ('INTERRUPTED','TIMED_OUT') THEN 'AMBIGUOUS_TRANSPORT' WHEN attemptid IS NOT NULL THEN 'TRANSPORT_RESPONSE' ELSE 'CANONICAL_EXACT_READ' END;
 classification:='INSUFFICIENT';
 IF source_kind='AMBIGUOUS_TRANSPORT' THEN classification:='AMBIGUOUS';
 ELSE
 remote:=CASE WHEN result ? 'items' AND jsonb_array_length(result->'items')=1 THEN result->'items'->0 WHEN result ? 'id' THEN result ELSE NULL END;
 IF remote IS NOT NULL THEN
 IF remote->>'receipt' IS DISTINCT FROM o.operation_key OR (remote->>'amount')::bigint IS DISTINCT FROM o.amount
 OR remote->>'currency' IS DISTINCT FROM o.currency OR remote->'notes'->>'interlock_operation' IS DISTINCT FROM o.operation_key
 OR remote->'notes'->>'interlock_account' IS DISTINCT FROM o.account_id OR remote->'notes'->>'interlock_integration' IS DISTINCT FROM o.integration
 OR remote->>'id' IS NULL OR remote->>'id' !~ '^order_[a-zA-Z0-9]+$' OR rt.provider_order IS NOT NULL AND rt.provider_order<>remote->>'id' THEN classification:='CONFLICT';
 ELSE classification:='EXACT_EFFECT'; END IF;
 ELSIF o.adapter='REFERENCE_SYNTHETIC' AND source_kind='CANONICAL_EXACT_READ' AND result->>'terminal_zero'='true'
 AND result->>'receipt'=o.operation_key AND result->>'request_digest'=o.request_digest AND result->>'account_id'=o.account_id THEN classification:='EXACT_ZERO_EFFECT';
 END IF;
 END IF;
 IF classification='EXACT_ZERO_EFFECT' AND rt.provider_order IS NOT NULL OR classification='EXACT_EFFECT' AND rt.knowledge='CONFIRMED_ZERO_EFFECT' THEN classification:='CONFLICT'; END IF;
 -- Persist only typed safe observation fields; authentication/payment credentials never enter this export.
 result:=jsonb_build_object('id',remote->>'id','receipt',remote->>'receipt','amount',remote->'amount','currency',remote->>'currency',
 'status',remote->>'status','notes',jsonb_build_object('interlock_operation',remote->'notes'->>'interlock_operation',
 'interlock_account',remote->'notes'->>'interlock_account','interlock_integration',remote->'notes'->>'interlock_integration'),
 'provenance',o.adapter,'terminal_zero',result->'terminal_zero',
 'request_digest',result->>'request_digest','coverage',result->>'coverage','transport_state',transport_state);
 INSERT INTO dispatch.operation_observations VALUES(obsid,o.tenant_id,o.id,attemptid,source_kind,clock_timestamp(),result,classification);
 IF attemptid IS NOT NULL THEN UPDATE dispatch.attempt_runtime SET state=transport_state,recorded_at=clock_timestamp(),response_observation_id=obsid WHERE attempt_id=attemptid; END IF;
 state_value:=CASE classification WHEN 'EXACT_EFFECT' THEN 'RESOLVED_EFFECT' WHEN 'EXACT_ZERO_EFFECT' THEN 'RESOLVED_ZERO_EFFECT' ELSE 'OBSERVING' END;
 knowledge_value:=CASE classification WHEN 'EXACT_EFFECT' THEN 'CONFIRMED_EFFECT' WHEN 'EXACT_ZERO_EFFECT' THEN 'CONFIRMED_ZERO_EFFECT' WHEN 'CONFLICT' THEN 'BLOCKED' ELSE 'UNKNOWN' END;
 -- Insufficient later reads do not erase a previously exact result. Contradiction blocks rather than releasing capacity.
 IF rt.knowledge IN ('CONFIRMED_EFFECT','CONFIRMED_ZERO_EFFECT') AND classification IN ('INSUFFICIENT','AMBIGUOUS') THEN state_value:=rt.state; knowledge_value:=rt.knowledge; END IF;
 UPDATE dispatch.operation_runtime SET state=state_value,knowledge=knowledge_value,revision=revision+1,latest_observation_id=obsid,
 provider_order=CASE WHEN classification='EXACT_EFFECT' THEN remote->>'id' ELSE provider_order END,
 blocked_reason=CASE WHEN classification='CONFLICT' THEN 'EXACT_READ_CONFLICT' ELSE NULL END,updated_at=clock_timestamp() WHERE operation_id=opid;
 IF classification IN ('EXACT_EFFECT','EXACT_ZERO_EFFECT') THEN
 UPDATE dispatch.duty_runtime SET state='DISCHARGED',discharge_observation_id=obsid,updated_at=clock_timestamp()
 WHERE obligation_id IN (SELECT id FROM dispatch.recovery_obligations WHERE operation_id=opid AND origin_kind='OPERATION');
 ELSE UPDATE dispatch.duty_runtime SET state='OBSERVING',blocked_reason=CASE WHEN classification='CONFLICT' THEN 'EXACT_READ_CONFLICT' ELSE NULL END,updated_at=clock_timestamp()
 WHERE obligation_id IN (SELECT id FROM dispatch.recovery_obligations WHERE operation_id=opid AND origin_kind='OPERATION') AND state<>'DISCHARGED'; END IF;
 UPDATE dispatch.outbox_items SET ready_at=clock_timestamp()+interval '10 seconds',lease_until=NULL,
 completed=classification IN ('EXACT_EFFECT','EXACT_ZERO_EFFECT') WHERE operation_id=opid AND purpose='OBSERVE';
 INSERT INTO dispatch.audit_events VALUES('audit_'||gen_random_uuid()::text,o.tenant_id,o.id,'OBSERVATION_'||classification,obsid,clock_timestamp());
 RETURN jsonb_build_object('operation_id',opid,'knowledge',knowledge_value,'state',state_value,'observation_id',obsid,'classification',classification);
END $$;

CREATE FUNCTION dispatch.disclose(token text,opid text,expected bigint,idem text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,dispatch,pg_temp AS $$
DECLARE o dispatch.business_operations; person authority.memberships; prior dispatch.command_identities;
 rt dispatch.operation_runtime; exposure dispatch.checkout_exposures; p jsonb; result jsonb; h text;
 exposureid text:='exposure_'||gen_random_uuid()::text; dutyid text:='duty_'||gen_random_uuid()::text; now_at timestamptz;
BEGIN
 o:=dispatch.user_operation(token,opid,'DISCLOSE'); person:=authority.actor(token);
 IF idem IS NULL OR idem !~ '^[a-zA-Z0-9_-]{8,100}$' OR expected IS NULL THEN PERFORM dispatch.fail('INVALID_COMMAND',422); END IF;
 h:=dispatch.hash(jsonb_build_object('operation_id',opid,'expected',expected));
 PERFORM pg_advisory_xact_lock(hashtextextended(person.tenant_id||'/'||person.principal||'/disclose/'||idem,0));
 PERFORM dispatch.user_operation(token,opid,'DISCLOSE');
 SELECT * INTO prior FROM dispatch.command_identities WHERE tenant_id=person.tenant_id AND principal=person.principal AND family='disclose' AND idempotency_key=idem;
 IF FOUND THEN IF prior.request_hash<>h THEN PERFORM dispatch.fail('IDEMPOTENCY_CONFLICT'); END IF; RETURN prior.result; END IF;
 SELECT * INTO exposure FROM dispatch.checkout_exposures WHERE operation_id=opid;
 IF exposure.id IS NOT NULL THEN result:=exposure.instructions; -- Existing path retrieval is not a new disclosure.
 ELSE
 p:=dispatch.current_permission(o,false);
 SELECT * INTO rt FROM dispatch.operation_runtime WHERE operation_id=opid FOR UPDATE;
 PERFORM dispatch.temporal(p); PERFORM dispatch.user_operation(token,opid,'DISCLOSE');
 SELECT * INTO exposure FROM dispatch.checkout_exposures WHERE operation_id=opid;
 IF exposure.id IS NOT NULL THEN result:=exposure.instructions;
 ELSE
 IF expected<>rt.revision THEN PERFORM dispatch.fail('REVISION_CONFLICT'); END IF;
 IF rt.knowledge<>'CONFIRMED_EFFECT' OR rt.provider_order IS NULL THEN PERFORM dispatch.fail('PROVIDER_ORDER_NOT_READY'); END IF;
 now_at:=clock_timestamp();
 result:=jsonb_build_object('exposure_id',exposureid,'operation_id',opid,'purchase_id',o.domain_root,'provider_order',rt.provider_order,
 'amount',o.amount,'currency',o.currency,'integration',o.integration,'mode',o.provider_mode,'account_id',o.account_id,
 'owner',person.principal,'first_disclosed_at',now_at,'provenance',o.adapter,'independently_usable_path',true);
 INSERT INTO dispatch.checkout_exposures VALUES(exposureid,o.tenant_id,o.id,o.domain_root,o.action_id,o.evidence_id,o.grant_id,o.grant_version_id,
 o.grant_epoch,(p->>'grant_expires_at')::timestamptz,o.config_epoch,p->>'policy_version_id',o.approval_id,(p->>'approval_expires_at')::timestamptz,
 o.reservation_id,rt.provider_order,o.integration,o.provider_mode,o.account_id,person.principal,o.amount,o.currency,now_at,result);
 UPDATE dispatch.reservation_exposure SET state='EXPOSED_UNRESOLVED' WHERE reservation_id=o.reservation_id;
 INSERT INTO dispatch.recovery_obligations VALUES(dutyid,o.tenant_id,'EXPOSURE',o.id,exposureid,o.reservation_id,'RESOLVE_EXTERNAL_EFFECT',
 'EXACT_EXPOSED_PAYMENT_STATUS','OBSERVE',true,o.replay_contract_id,'dispatch-duty-v1',now_at);
 INSERT INTO dispatch.duty_runtime VALUES(dutyid,o.tenant_id,'OPEN',NULL,NULL,now_at);
 INSERT INTO dispatch.audit_events VALUES('audit_'||gen_random_uuid()::text,o.tenant_id,o.id,'CHECKOUT_EXPOSURE_COMMITTED',exposureid,now_at);
 END IF;
 END IF;
 INSERT INTO dispatch.command_identities VALUES('command_'||gen_random_uuid()::text,person.tenant_id,person.principal,'disclose',idem,h,result,clock_timestamp());
 RETURN result;
END $$;

CREATE FUNCTION dispatch.note_block(secret text,opid text,reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,dispatch,pg_temp AS $$
DECLARE o dispatch.business_operations; BEGIN
 SELECT * INTO o FROM dispatch.business_operations WHERE id=opid;
 PERFORM dispatch.service_actor(secret,o.tenant_id,'OBSERVE');
 IF reason !~ '^[A-Z_]{1,100}$' THEN reason:='CUTOFF_DENIED'; END IF;
 UPDATE dispatch.operation_runtime SET blocked_reason=reason,revision=revision+1,updated_at=clock_timestamp()
 WHERE operation_id=opid;
END $$;

CREATE FUNCTION dispatch.recover(token text,family text,idem text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,dispatch,pg_temp AS $$
DECLARE person authority.memberships; result jsonb; BEGIN
 person:=authority.actor(token);
 SELECT c.result INTO result FROM dispatch.command_identities c WHERE tenant_id=person.tenant_id AND principal=person.principal AND c.family=recover.family AND idempotency_key=idem;
 IF result IS NULL THEN PERFORM dispatch.fail('COMMAND_NOT_VISIBLE',403); END IF;
 PERFORM dispatch.user_operation(token,result->>'operation_id'); RETURN result;
END $$;

CREATE FUNCTION dispatch.inspect(token text,opid text DEFAULT NULL) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,dispatch,pg_temp AS $$
DECLARE person authority.memberships; o dispatch.business_operations; result jsonb:='[]'::jsonb; details jsonb;
BEGIN
 person:=authority.actor(token);
 IF opid IS NOT NULL THEN PERFORM dispatch.user_operation(token,opid); END IF;
 FOR o IN SELECT b.* FROM dispatch.business_operations b JOIN authority.grants g ON g.id=b.grant_id WHERE b.tenant_id=person.tenant_id
 AND (opid IS NULL OR b.id=opid) AND (b.requester=person.principal OR g.issuer=person.principal OR 'PURCHASE_APPROVER'=ANY(person.roles)) ORDER BY b.created_at,b.id LOOP
 SELECT jsonb_build_object('operation',to_jsonb(o),'runtime',to_jsonb(rt),'grant_head',to_jsonb(gh),'reservation',to_jsonb(r),'exposure_state',to_jsonb(rs),
 'replay_contract',to_jsonb(c),'observation_permitted',EXISTS(SELECT 1 FROM dispatch.observation_memberships WHERE tenant_id=o.tenant_id AND principal=person.principal AND active),
 'disclosure_permitted',EXISTS(SELECT 1 FROM authority.grants WHERE id=o.grant_id AND issuer=person.principal),
 'age_seconds',extract(epoch FROM clock_timestamp()-o.created_at)::bigint,
 'attempts',coalesce((SELECT jsonb_agg(to_jsonb(a)||jsonb_build_object('runtime',to_jsonb(ar),'dispatch',to_jsonb(d)) ORDER BY a.number)
 FROM dispatch.transport_attempts a JOIN dispatch.attempt_runtime ar ON ar.attempt_id=a.id JOIN dispatch.dispatch_records d ON d.attempt_id=a.id WHERE a.operation_id=o.id),'[]'::jsonb),
 'exposures',coalesce((SELECT jsonb_agg(to_jsonb(e)-'instructions') FROM dispatch.checkout_exposures e WHERE e.operation_id=o.id),'[]'::jsonb),
 'duties',coalesce((SELECT jsonb_agg(to_jsonb(d)||jsonb_build_object('runtime',to_jsonb(h),'age_seconds',extract(epoch FROM clock_timestamp()-d.created_at)::bigint)) FROM dispatch.recovery_obligations d JOIN dispatch.duty_runtime h ON h.obligation_id=d.id WHERE d.operation_id=o.id),'[]'::jsonb),
 'observations',coalesce((SELECT jsonb_agg(to_jsonb(v) ORDER BY v.observed_at,v.id) FROM dispatch.operation_observations v WHERE v.operation_id=o.id),'[]'::jsonb)) INTO details
 FROM dispatch.operation_runtime rt JOIN authority.grant_heads gh ON gh.grant_id=o.grant_id JOIN authority.reservations r ON r.id=o.reservation_id
 JOIN dispatch.reservation_exposure rs ON rs.reservation_id=r.id JOIN dispatch.replay_contracts c ON c.id=o.replay_contract_id WHERE rt.operation_id=o.id;
 result:=result||jsonb_build_array(details);
 END LOOP;
 RETURN jsonb_build_object('principal',person.principal,'tenant_id',person.tenant_id,'operations',result);
END $$;

CREATE FUNCTION dispatch.bundle_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,dispatch,pg_temp AS $$
DECLARE opid text:=NEW.operation_id; rt dispatch.operation_runtime; o dispatch.business_operations; BEGIN
 SELECT * INTO rt FROM dispatch.operation_runtime WHERE operation_id=opid;
 SELECT * INTO o FROM dispatch.business_operations WHERE id=opid;
 IF rt.attempt_count<>(SELECT count(*) FROM dispatch.transport_attempts WHERE operation_id=opid)
 OR rt.attempt_count<>(SELECT count(*) FROM dispatch.dispatch_records WHERE operation_id=opid)
 OR rt.attempt_count>0 AND rt.state IN ('QUEUED','READY','CANCEL_UNSENT') THEN PERFORM dispatch.fail('INVALID_COMMITTED_OPERATION_BUNDLE'); END IF;
 IF rt.attempt_count>0 AND NOT EXISTS(SELECT 1 FROM dispatch.recovery_obligations WHERE operation_id=opid AND origin_kind='OPERATION') THEN PERFORM dispatch.fail('MISSING_COMMITTED_DUTY'); END IF;
 IF rt.knowledge='UNKNOWN' AND NOT EXISTS(SELECT 1 FROM authority.reservation_heads WHERE reservation_id=o.reservation_id AND status='RESERVED') THEN PERFORM dispatch.fail('UNKNOWN_RESERVATION_RELEASED'); END IF;
 IF EXISTS(SELECT 1 FROM dispatch.checkout_exposures e WHERE operation_id=opid AND
 (NOT EXISTS(SELECT 1 FROM dispatch.recovery_obligations WHERE exposure_id=e.id AND origin_kind='EXPOSURE') OR
 NOT EXISTS(SELECT 1 FROM dispatch.reservation_exposure WHERE reservation_id=e.reservation_id AND state='EXPOSED_UNRESOLVED'))) THEN PERFORM dispatch.fail('INCOMPLETE_EXPOSURE_BUNDLE'); END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER operation_bundle AFTER INSERT OR UPDATE ON dispatch.operation_runtime DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION dispatch.bundle_guard();
CREATE CONSTRAINT TRIGGER permission_bundle AFTER INSERT ON dispatch.dispatch_records DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION dispatch.bundle_guard();
CREATE CONSTRAINT TRIGGER exposure_bundle AFTER INSERT ON dispatch.checkout_exposures DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION dispatch.bundle_guard();

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA dispatch FROM PUBLIC;
DO $$ DECLARE item record; BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='interlock_dispatch_owner') THEN CREATE ROLE interlock_dispatch_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; END IF;
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='interlock_dispatch_owner' AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit)) THEN RAISE EXCEPTION 'Unsafe dispatch owner'; END IF;
 GRANT USAGE ON SCHEMA authority,public TO interlock_dispatch_owner;
 GRANT EXECUTE ON FUNCTION authority.actor(text) TO interlock_dispatch_owner;
 GRANT SELECT ON public.tenants TO interlock_dispatch_owner;
 GRANT SELECT ON authority.memberships,authority.tenant_control,authority.grants,authority.grant_versions,authority.grant_heads,
 authority.action_revisions,authority.intent_heads,authority.evidence_revisions,authority.policy_decisions,authority.approvals,authority.approval_heads,
 authority.reservations,authority.reservation_heads,authority.admitted_work,authority.seller_quotes,authority.quote_heads TO interlock_dispatch_owner;
 GRANT UPDATE(tenant_id) ON authority.tenant_control TO interlock_dispatch_owner;
 GRANT UPDATE(grant_id) ON authority.grant_heads TO interlock_dispatch_owner;
 GRANT UPDATE(intent_id) ON authority.intent_heads TO interlock_dispatch_owner;
 GRANT UPDATE(quote_id) ON authority.quote_heads TO interlock_dispatch_owner;
 GRANT UPDATE(approval_id) ON authority.approval_heads TO interlock_dispatch_owner;
 GRANT UPDATE(principal) ON authority.memberships TO interlock_dispatch_owner;
 GRANT UPDATE(reservation_id) ON authority.reservation_heads TO interlock_dispatch_owner;
 ALTER SCHEMA dispatch OWNER TO interlock_dispatch_owner;
 FOR item IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='dispatch' AND c.relkind='r' LOOP
 EXECUTE format('ALTER TABLE dispatch.%I OWNER TO interlock_dispatch_owner',item.relname); END LOOP;
 FOR item IN SELECT p.oid::regprocedure AS name FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='dispatch' LOOP
 EXECUTE format('ALTER FUNCTION %s OWNER TO interlock_dispatch_owner',item.name); END LOOP;
 GRANT USAGE ON SCHEMA dispatch TO interlock_api,interlock_worker,interlock_broker;
 GRANT EXECUTE ON FUNCTION dispatch.inspect(text,text),dispatch.disclose(text,text,bigint,text),dispatch.recover(text,text,text),dispatch.user_operation(text,text,text) TO interlock_api;
 GRANT EXECUTE ON FUNCTION dispatch.claim(text,int,text) TO interlock_worker;
 GRANT EXECUTE ON FUNCTION dispatch.binding(text,text),dispatch.ready(text,text,text),dispatch.commit_handoff(text,text,text,text,boolean),dispatch.record_observation(text,text,jsonb,text,text),dispatch.note_block(text,text,text) TO interlock_broker;
END $$;
