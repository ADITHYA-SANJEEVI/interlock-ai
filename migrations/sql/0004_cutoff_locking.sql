CREATE OR REPLACE FUNCTION dispatch.current_permission(o dispatch.business_operations,quote_deadline boolean,credential_id text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SET search_path=pg_catalog,dispatch,pg_temp AS $$
DECLARE cfg authority.tenant_control; gh authority.grant_heads; gv authority.grant_versions; ih authority.intent_heads;
 a authority.action_revisions; e authority.evidence_revisions; d authority.policy_decisions; ap authority.approvals;
 aph authority.approval_heads; r authority.reservations; rh authority.reservation_heads; member authority.memberships;
 q authority.seller_quotes; qh authority.quote_heads; now_at timestamptz; service dispatch.service_credentials; integration dispatch.integrations;
BEGIN
 SELECT * INTO cfg FROM authority.tenant_control WHERE tenant_id=o.tenant_id AND kind='PURCHASE' FOR SHARE;
 IF credential_id IS NOT NULL THEN
 SELECT * INTO service FROM dispatch.service_credentials WHERE id=credential_id FOR SHARE;
 PERFORM s.credential_id FROM dispatch.service_scopes s WHERE s.credential_id=current_permission.credential_id AND s.tenant_id=o.tenant_id FOR SHARE;
 END IF;
 SELECT * INTO integration FROM dispatch.integrations WHERE tenant_id=o.tenant_id FOR SHARE;
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
 IF integration.integration IS DISTINCT FROM o.integration OR integration.account_id IS DISTINCT FROM o.account_id
 OR integration.adapter IS DISTINCT FROM o.adapter OR integration.replay_contract_id IS DISTINCT FROM o.replay_contract_id THEN PERFORM dispatch.fail('INTEGRATION_CHANGED'); END IF;
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

CREATE OR REPLACE FUNCTION dispatch.commit_handoff(secret text,opid text,claimid text,expected_digest text,replay boolean DEFAULT false) RETURNS jsonb
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
 c:=dispatch.service_actor(secret,o.tenant_id,'DISPATCH');
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
