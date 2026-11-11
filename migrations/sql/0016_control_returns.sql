CREATE SCHEMA returns;
REVOKE ALL ON SCHEMA returns FROM PUBLIC;
CREATE TABLE returns.locks(
 id uuid PRIMARY KEY, tenant_id text NOT NULL, purchase_operation text NOT NULL UNIQUE REFERENCES dispatch.business_operations(id),
 capture_fact_id uuid NOT NULL UNIQUE REFERENCES economic.facts(id), raw text NOT NULL, signature text NOT NULL,
 confirmed_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(tenant_id,id)
);
CREATE TABLE returns.principal(
 capture_fact_id uuid PRIMARY KEY REFERENCES economic.facts(id), captured bigint NOT NULL CHECK(captured>0),
 processed bigint NOT NULL DEFAULT 0 CHECK(processed>=0), unresolved bigint NOT NULL DEFAULT 0 CHECK(unresolved>=0),
 CHECK(processed+unresolved<=captured)
);
CREATE TABLE returns.grant_bindings(
 grant_id text PRIMARY KEY REFERENCES authority.grants(id), tenant_id text NOT NULL, account_id text NOT NULL,
 integration text NOT NULL, provider text NOT NULL, capture_fact_id uuid NOT NULL REFERENCES economic.facts(id),
 operation_kind text NOT NULL CHECK(operation_kind='REFUND_CAPTURED_PRINCIPAL')
);
ALTER TABLE authority.evidence_revisions ADD COLUMN return_lock_id uuid REFERENCES returns.locks(id);
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='authority.evidence_revisions'::regclass AND contype='c'
 AND pg_get_constraintdef(oid) LIKE '%quote_id%' LOOP
 EXECUTE format('ALTER TABLE authority.evidence_revisions DROP CONSTRAINT %I',c.conname); END LOOP;
END $$;

ALTER TABLE authority.evidence_revisions ADD CHECK(
 (quote_id IS NOT NULL AND fixture_id IS NULL AND quote_revision IS NOT NULL AND return_lock_id IS NULL)
 OR (quote_id IS NULL AND fixture_id IS NOT NULL AND fixture_revision IS NOT NULL AND return_lock_id IS NULL)
 OR (quote_id IS NULL AND fixture_id IS NULL AND return_lock_id IS NOT NULL));
ALTER TABLE authority.evidence_members DROP CONSTRAINT evidence_members_kind_check;
ALTER TABLE authority.evidence_members ADD CHECK(kind IN ('GRANT_VERSION','POLICY_VERSION','SELLER_QUOTE','REFUND_FIXTURE','ACTION','CONFIG','SELLER_RETURN_LOCK','CAPTURE_FACT'));
ALTER TABLE authority.reservation_heads DROP CONSTRAINT reservation_heads_status_check;
ALTER TABLE authority.reservation_heads ADD CHECK(status IN ('RESERVED','FIXTURE_CONSUMED','FIXTURE_RELEASED','REFUND_PROCESSED'));
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='authority.authority_movements'::regclass AND contype='c'
 AND pg_get_constraintdef(oid) LIKE '%kind%' LOOP
 EXECUTE format('ALTER TABLE authority.authority_movements DROP CONSTRAINT %I',c.conname); END LOOP;
END $$;
ALTER TABLE authority.authority_movements ADD CHECK(kind IN ('RESERVE','FIXTURE_CONSUME','FIXTURE_RELEASE','REFUND_PROCESSED'));
ALTER TABLE authority.authority_movements ADD CHECK(
 (kind='RESERVE' AND delta_consumed=0 AND delta_reserved=amount AND delta_free=-amount)
 OR (kind IN ('FIXTURE_CONSUME','REFUND_PROCESSED') AND delta_consumed=amount AND delta_reserved=-amount AND delta_free=0)
 OR (kind='FIXTURE_RELEASE' AND delta_consumed=0 AND delta_reserved=-amount AND delta_free=amount));
CREATE TABLE returns.obligations(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id text NOT NULL, purchase_operation text NOT NULL,
 purchase_id text NOT NULL, payment_id text NOT NULL, provider_order text NOT NULL,
 capture_fact_id uuid NOT NULL REFERENCES economic.facts(id), lock_id uuid NOT NULL REFERENCES returns.locks(id),
 amount bigint NOT NULL CHECK(amount>0), currency text NOT NULL CHECK(currency='INR'),
 account_id text NOT NULL, integration text NOT NULL, provider text NOT NULL,
 reservation_id text NOT NULL UNIQUE REFERENCES authority.reservations(id),
 action_id text NOT NULL UNIQUE REFERENCES authority.action_revisions(id), evidence_id text NOT NULL REFERENCES authority.evidence_revisions(id),
 decision_id text NOT NULL REFERENCES authority.policy_decisions(id), approval_id text REFERENCES authority.approvals(id),
 accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(tenant_id,id)
);
CREATE TABLE returns.principal_reservations(
 obligation_id uuid PRIMARY KEY REFERENCES returns.obligations(id) DEFERRABLE INITIALLY DEFERRED,
 capture_fact_id uuid NOT NULL REFERENCES returns.principal(capture_fact_id), amount bigint NOT NULL CHECK(amount>0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE returns.operations(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), obligation_id uuid NOT NULL UNIQUE REFERENCES returns.obligations(id),
 operation_key text NOT NULL UNIQUE, body jsonb NOT NULL, request_digest text NOT NULL,
 replay_class text NOT NULL DEFAULT 'NEVER_AUTOMATIC_REPLAY' CHECK(replay_class IN ('SAFE_SAME_KEY_REPLAY','READ_BEFORE_REPLAY','NEVER_AUTOMATIC_REPLAY')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE returns.runtime(
 operation_id uuid PRIMARY KEY REFERENCES returns.operations(id), state text NOT NULL DEFAULT 'QUEUED'
 CHECK(state IN ('QUEUED','UNKNOWN','PROCESSED','BLOCKED')), refund_id text, blocked boolean NOT NULL DEFAULT false,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE returns.outbox(
 operation_id uuid PRIMARY KEY REFERENCES returns.operations(id), claim_id uuid, credential_id text,
 lease_until timestamptz, completed boolean NOT NULL DEFAULT false
);
CREATE TABLE returns.attempts(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), operation_id uuid NOT NULL UNIQUE REFERENCES returns.operations(id),
 request_digest text NOT NULL, committed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE returns.dispatch_records(
 attempt_id uuid PRIMARY KEY REFERENCES returns.attempts(id), operation_id uuid NOT NULL UNIQUE REFERENCES returns.operations(id),
 service_id text NOT NULL, service_revision bigint NOT NULL, grant_version text NOT NULL, grant_epoch bigint NOT NULL,
 config_epoch bigint NOT NULL, approval_id text, cutoff_at timestamptz NOT NULL, bindings jsonb NOT NULL
);
CREATE TABLE returns.recovery_duties(
 operation_id uuid PRIMARY KEY REFERENCES returns.operations(id), obligation_id uuid NOT NULL REFERENCES returns.obligations(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE returns.observations(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), receipt_id uuid NOT NULL UNIQUE REFERENCES economic.receipts(id),
 provider text NOT NULL, account_id text NOT NULL, integration text NOT NULL, tenant_id text,
 payment_id text NOT NULL, refund_id text NOT NULL, amount bigint NOT NULL CHECK(amount>0), currency text NOT NULL CHECK(currency='INR'),
 status text NOT NULL CHECK(status IN ('PENDING','PROCESSED','FAILED')), operation_id uuid REFERENCES returns.operations(id),
 obligation_id uuid REFERENCES returns.obligations(id), body jsonb NOT NULL, observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE returns.conflicts(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), receipt_id uuid NOT NULL REFERENCES economic.receipts(id),
 operation_id uuid REFERENCES returns.operations(id), reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE returns.facts(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), kind text NOT NULL CHECK(kind IN ('REFUND_ACCEPTED','REFUND_PROCESSED')),
 obligation_id uuid NOT NULL REFERENCES returns.obligations(id), operation_id uuid NOT NULL REFERENCES returns.operations(id),
 observation_id uuid REFERENCES returns.observations(id), capture_fact_id uuid NOT NULL REFERENCES economic.facts(id),
 amount bigint NOT NULL CHECK(amount>0), currency text NOT NULL CHECK(currency='INR'), refund_id text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(obligation_id,kind),
 CHECK((kind='REFUND_ACCEPTED' AND observation_id IS NULL AND refund_id IS NULL)
 OR (kind='REFUND_PROCESSED' AND observation_id IS NOT NULL AND refund_id IS NOT NULL))
);
CREATE UNIQUE INDEX unique_refund_effect ON returns.facts(refund_id,capture_fact_id) WHERE kind='REFUND_PROCESSED';
CREATE TABLE returns.postings(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), fact_id uuid NOT NULL UNIQUE REFERENCES returns.facts(id),
 template text NOT NULL CHECK(template IN ('RETURN_TO_PAYABLE','PAYABLE_TO_PROVIDER')), created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE returns.lines(
 posting_id uuid NOT NULL REFERENCES returns.postings(id), ordinal int NOT NULL CHECK(ordinal IN (1,2)),
 account text NOT NULL REFERENCES economic.accounts(name), side text NOT NULL CHECK(side IN ('DEBIT','CREDIT')),
 amount bigint NOT NULL CHECK(amount>0), currency text NOT NULL CHECK(currency='INR'), PRIMARY KEY(posting_id,ordinal)
);
CREATE TABLE returns.closures(
 lock_id uuid PRIMARY KEY REFERENCES returns.locks(id), raw text NOT NULL, signature text NOT NULL,
 confirmed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
DO $$ DECLARE t text; BEGIN FOREACH t IN ARRAY ARRAY['locks','grant_bindings','obligations','principal_reservations','operations','attempts','dispatch_records','recovery_duties','observations','conflicts','facts','postings','lines','closures'] LOOP
 EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON returns.%I FOR EACH ROW EXECUTE FUNCTION authority.immutable()',t);
END LOOP; END $$;

CREATE FUNCTION returns.capture(token text,opid text) RETURNS economic.facts LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,returns,pg_temp AS $$
DECLARE m authority.memberships; f economic.facts;
BEGIN
 m:=authority.actor(token);
 IF NOT (ARRAY['REFUND_REQUESTER','REFUND_ISSUER','REFUND_APPROVER']::text[] && m.roles) THEN PERFORM authority.fail('REFUND_ROLE_REQUIRED',403); END IF;
 SELECT * INTO f FROM economic.facts WHERE operation_id=opid AND tenant_id=m.tenant_id AND kind='CAPTURE';
 IF f.id IS NULL OR NOT EXISTS(SELECT 1 FROM economic.facts rv JOIN economic.facts li ON li.id=rv.parent_fact_id WHERE rv.kind='REVENUE' AND li.parent_fact_id=f.id)
 OR EXISTS(SELECT 1 FROM economic.object_heads WHERE provider=f.provider AND account_id=f.account_id AND object_id=f.effect_id AND blocked)
 THEN PERFORM authority.fail('MATCHED_FULFILLED_CAPTURE_REQUIRED',403); END IF;
 RETURN f;
END $$;

CREATE FUNCTION returns.prepare(token text,opid text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,returns,pg_temp AS $$
DECLARE f economic.facts; sf economic.facts; obs economic.observations; raw text; key text;
BEGIN
 f:=returns.capture(token,opid);
 SELECT * INTO sf FROM economic.facts WHERE operation_id=opid AND kind='SELLER_FULFILLED' AND parent_fact_id=f.id;
 SELECT * INTO obs FROM economic.observations WHERE id=sf.observation_id;
 -- Reuse the independently authenticated exact seller finalization; no final action exists yet.
 raw:=dispatch.canonical(obs.payload||jsonb_build_object('kind','RETURN_PREPARE','seller_finalization_hash',
 encode(public.digest(dispatch.canonical(obs.payload),'sha256'),'hex')));
 SELECT secret INTO key FROM economic.channels WHERE id='seller-capability-key';
 RETURN jsonb_build_object('raw',raw,'signature',encode(public.hmac(raw,key,'sha256'),'hex'));
END $$;

CREATE FUNCTION returns.confirm_lock(token text,opid text,raw text,sig text) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,returns,pg_temp AS $$
DECLARE f economic.facts; v jsonb; key text; expected jsonb; l returns.locks; lid uuid;
BEGIN
 f:=returns.capture(token,opid); expected:=(returns.prepare(token,opid)->>'raw')::jsonb;
 SELECT secret INTO key FROM economic.channels WHERE id='seller-'||f.tenant_id;
 IF key IS NULL OR sig IS DISTINCT FROM encode(public.hmac(raw,key,'sha256'),'hex') THEN PERFORM authority.fail('SELLER_LOCK_AUTHENTICATION_REQUIRED',403); END IF;
 v:=raw::jsonb;
 IF v->>'kind' IS DISTINCT FROM 'RETURN_LOCKED' OR (v-'kind'-'lock_id') IS DISTINCT FROM (expected-'kind')
 OR v->>'order_id' IS DISTINCT FROM expected->>'order_id' THEN PERFORM authority.fail('EXACT_RETURN_LOCK_REQUIRED',403); END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('return-lock:'||opid,0));
 SELECT * INTO l FROM returns.locks WHERE purchase_operation=opid;
 IF l.id IS NOT NULL THEN IF l.raw<>raw THEN PERFORM authority.fail('RETURN_LOCK_SUBSTITUTION'); END IF; RETURN l.id; END IF;
 lid:=(v->>'lock_id')::uuid;
 INSERT INTO returns.locks VALUES(lid,f.tenant_id,opid,f.id,raw,sig,clock_timestamp());
 INSERT INTO returns.principal(capture_fact_id,captured) VALUES(f.id,f.amount);
 RETURN lid;
END $$;

CREATE FUNCTION returns.guard(aid text,approval text DEFAULT NULL,consumed boolean DEFAULT false) RETURNS void LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog,returns,pg_temp AS $$
DECLARE a authority.action_revisions; e authority.evidence_revisions; d authority.policy_decisions;
 h authority.grant_heads; v authority.grant_versions; c authority.tenant_control; m authority.memberships;
 ap authority.approvals; ah authority.approval_heads; l returns.locks; b returns.grant_bindings; f economic.facts;
BEGIN
 SELECT * INTO a FROM authority.action_revisions WHERE id=aid AND kind='REFUND';
 SELECT * INTO c FROM authority.tenant_control WHERE tenant_id=a.tenant_id AND kind='REFUND' FOR SHARE;
 SELECT * INTO h FROM authority.grant_heads WHERE grant_id=a.grant_id FOR UPDATE;
 SELECT * INTO v FROM authority.grant_versions WHERE id=h.version_id;
 PERFORM 1 FROM authority.intent_heads WHERE intent_id=a.intent_id FOR UPDATE;
 SELECT * INTO e FROM authority.evidence_revisions WHERE action_id=a.id;
 SELECT * INTO d FROM authority.policy_decisions WHERE action_id=a.id AND evidence_id=e.id;
 SELECT * INTO l FROM returns.locks WHERE id=e.return_lock_id;
 SELECT * INTO b FROM returns.grant_bindings WHERE grant_id=a.grant_id;
 SELECT * INTO f FROM economic.facts WHERE id=l.capture_fact_id;
 IF a.id IS NULL OR l.id IS NULL OR b.capture_fact_id IS DISTINCT FROM f.id OR b.tenant_id IS DISTINCT FROM a.tenant_id
 OR b.account_id IS DISTINCT FROM f.account_id OR b.integration IS DISTINCT FROM f.integration OR b.provider IS DISTINCT FROM f.provider
 OR l.raw::jsonb IS DISTINCT FROM a.terms->'seller_lock' OR (a.terms->>'amount')::bigint IS DISTINCT FROM a.amount
 OR a.terms->>'capture_fact_id' IS DISTINCT FROM f.id::text OR a.terms->>'payment_id' IS DISTINCT FROM f.effect_id
 OR NOT ARRAY(SELECT value->>'beneficiary' FROM jsonb_array_elements(l.raw::jsonb->'seats'))<@v.beneficiaries
 OR v.integration IS DISTINCT FROM f.integration OR v.resource_id IS DISTINCT FROM f.id::text
 OR NOT EXISTS(SELECT 1 FROM authority.grants WHERE id=a.grant_id AND kind='REFUND' AND subject=a.requester)
 THEN PERFORM authority.fail('REFUND_BINDING_MISMATCH',403); END IF;
 IF c.paused OR c.policy_version_id IS DISTINCT FROM e.policy_version_id OR c.epoch IS DISTINCT FROM e.config_epoch
 OR d.policy_version_id IS DISTINCT FROM c.policy_version_id OR NOT d.allowed THEN PERFORM authority.fail('REFUND_POLICY_CHANGED',403); END IF;
 IF h.status IS DISTINCT FROM 'ACTIVE' OR h.version_id IS DISTINCT FROM e.grant_version_id OR h.epoch IS DISTINCT FROM e.grant_epoch THEN
 PERFORM authority.fail('REFUND_AUTHORITY_CHANGED',403); END IF;
 IF approval IS NOT NULL THEN
 SELECT * INTO ap FROM authority.approvals WHERE id=approval FOR SHARE;
 SELECT * INTO ah FROM authority.approval_heads WHERE approval_id=approval FOR UPDATE;
 END IF;
 PERFORM 1 FROM authority.memberships WHERE principal IN (a.requester,ap.approver) ORDER BY principal FOR SHARE;
 SELECT * INTO m FROM authority.memberships WHERE principal=a.requester;
 IF NOT m.active OR m.revision<>a.requester_revision OR NOT 'REFUND_REQUESTER'=ANY(m.roles) THEN PERFORM authority.fail('REFUND_REQUESTER_CHANGED',403); END IF;
 IF d.approval_required AND consumed AND approval IS NULL THEN PERFORM authority.fail('REFUND_APPROVAL_REQUIRED',403); END IF;
 IF approval IS NOT NULL THEN
 SELECT * INTO m FROM authority.memberships WHERE principal=ap.approver;
 IF ap.id IS NULL OR ap.action_id IS DISTINCT FROM a.id OR ap.evidence_id IS DISTINCT FROM e.id OR ap.decision_id IS DISTINCT FROM d.id
 OR ap.evidence_digest IS DISTINCT FROM e.digest OR ap.approver=a.requester OR NOT m.active OR m.revision<>ap.approver_revision
 OR NOT 'REFUND_APPROVER'=ANY(m.roles) OR ap.expires_at<=clock_timestamp()
 OR ah.status IS DISTINCT FROM (CASE WHEN consumed THEN 'CONSUMED' ELSE 'AVAILABLE' END)
 THEN PERFORM authority.fail('REFUND_APPROVAL_INVALID',403); END IF;
 END IF;
 IF v.expires_at<=clock_timestamp() THEN PERFORM authority.fail('REFUND_AUTHORITY_EXPIRED',403); END IF;
 IF EXISTS(SELECT 1 FROM economic.object_heads WHERE provider=f.provider AND account_id=f.account_id AND object_id=f.effect_id AND blocked)
 THEN PERFORM authority.fail('CAPTURE_CONTRADICTION',403); END IF;
 IF EXISTS(SELECT 1 FROM returns.obligations ob JOIN returns.operations op ON op.obligation_id=ob.id JOIN returns.runtime rt ON rt.operation_id=op.id
 WHERE ob.capture_fact_id=f.id AND rt.blocked) THEN PERFORM authority.fail('REFUND_PRINCIPAL_CONTRADICTION',403); END IF;
 IF EXISTS(SELECT 1 FROM returns.observations ob WHERE ob.operation_id IS NULL AND ob.provider=f.provider AND ob.account_id=f.account_id
 AND ob.integration=f.integration AND ob.payment_id=f.effect_id AND NOT EXISTS(
 SELECT 1 FROM returns.facts rf JOIN returns.observations mo ON mo.id=rf.observation_id
 WHERE rf.capture_fact_id=f.id AND rf.kind='REFUND_PROCESSED' AND mo.refund_id=ob.refund_id)) THEN
 PERFORM authority.fail('UNATTRIBUTED_REFUND_ON_CAPTURE',403); END IF;
END $$;

CREATE FUNCTION returns.command(token text,family text,body jsonb,expected bigint,idem text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,returns,pg_temp AS $$
DECLARE m authority.memberships; cfg authority.tenant_control; g authority.grants; h authority.grant_heads;
 gv authority.grant_versions; l returns.locks; f economic.facts; pv authority.policy_versions;
 a authority.action_revisions; e authority.evidence_revisions; d authority.policy_decisions; ih authority.intent_heads;
 pp returns.principal; prior authority.request_identities; allowed text[]; rid text; digest text; result jsonb;
 gid text; vid text; iid text; irid text; aid text; eid text; did text; apid text; rsid text;
 oid uuid; opid uuid; amt bigint; terms jsonb; members jsonb; expires timestamptz; payload jsonb;
BEGIN
 m:=authority.actor(token);
 allowed:=CASE family WHEN 'create-grant' THEN ARRAY['purchase_operation','subject','limit','expires_at','beneficiaries']
 WHEN 'propose' THEN ARRAY['lock_id','grant_id','amount'] WHEN 'approve' THEN ARRAY['action_id','evidence_id','decision_id','expires_at']
 WHEN 'admit' THEN ARRAY['action_id','evidence_id','decision_id','approval_id'] ELSE NULL END;
 IF allowed IS NULL OR jsonb_typeof(body)<>'object' OR EXISTS(SELECT 1 FROM jsonb_object_keys(body) k WHERE NOT k=ANY(allowed))
 OR idem!~'^[A-Za-z0-9_-]{8,100}$' THEN PERFORM authority.fail('INVALID_RETURN_COMMAND',422); END IF;
 digest:=dispatch.hash(jsonb_build_object('tenant',m.tenant_id,'principal',m.principal,'family',family,'body',body,'expected',expected));
 PERFORM pg_advisory_xact_lock(hashtextextended(m.tenant_id||':'||m.principal||':return-'||family||':'||idem,0));
 SELECT * INTO prior FROM authority.request_identities WHERE tenant_id=m.tenant_id AND principal=m.principal
 AND request_identities.family='return-'||command.family AND idempotency_key=idem;
 IF prior.id IS NOT NULL THEN IF prior.request_hash<>digest THEN PERFORM authority.fail('IDEMPOTENCY_CONFLICT'); END IF; RETURN prior.result; END IF;
 rid:='return_request_'||gen_random_uuid()::text;
 SELECT * INTO cfg FROM authority.tenant_control WHERE tenant_id=m.tenant_id AND kind='REFUND' FOR SHARE;
 IF cfg.tenant_id IS NULL OR cfg.paused OR cfg.policy_version_id IS NULL THEN PERFORM authority.fail('REFUND_POLICY_NOT_READY',403); END IF;
 IF family='create-grant' THEN
 PERFORM authority.require_role(m,'REFUND_ISSUER');
 IF expected<>0 THEN PERFORM authority.fail('REVISION_CONFLICT'); END IF;
 f:=returns.capture(token,body->>'purchase_operation');
 amt:=(body->>'limit')::bigint; expires:=(body->>'expires_at')::timestamptz;
 IF amt IS NULL OR amt<=0 OR amt>f.amount OR expires IS NULL OR expires<=clock_timestamp()
 OR NOT EXISTS(SELECT 1 FROM authority.memberships WHERE tenant_id=m.tenant_id AND principal=body->>'subject' AND active AND 'REFUND_REQUESTER'=ANY(roles))
 OR jsonb_typeof(body->'beneficiaries') IS DISTINCT FROM 'array' OR jsonb_array_length(body->'beneficiaries') NOT BETWEEN 1 AND 5
 THEN PERFORM authority.fail('INVALID_EXACT_REFUND_GRANT',422); END IF;
 gid:='refund_grant_'||gen_random_uuid()::text; vid:='refund_version_'||gen_random_uuid()::text;
 INSERT INTO authority.grants(id,tenant_id,issuer,subject,kind,currency,lifetime_bound) VALUES(gid,m.tenant_id,m.principal,body->>'subject','REFUND','INR',amt);
 INSERT INTO authority.grant_versions(id,tenant_id,grant_id,version,epoch,seller,integration,beneficiaries,resource_id,expires_at)
 VALUES(vid,m.tenant_id,gid,1,1,'workspace-seller-v1',f.integration,ARRAY(SELECT jsonb_array_elements_text(body->'beneficiaries')),f.id::text,expires);
 INSERT INTO authority.grant_heads(grant_id,tenant_id,version_id,status,bound,free) VALUES(gid,m.tenant_id,vid,'ACTIVE',amt,amt);
 INSERT INTO returns.grant_bindings VALUES(gid,m.tenant_id,f.account_id,f.integration,f.provider,f.id,'REFUND_CAPTURED_PRINCIPAL');
 result:=jsonb_build_object('grant_id',gid,'revision',1,'status','ACTIVE');
 ELSIF family='propose' THEN
 PERFORM authority.require_role(m,'REFUND_REQUESTER');
 SELECT * INTO l FROM returns.locks WHERE id=(body->>'lock_id')::uuid AND tenant_id=m.tenant_id;
 SELECT * INTO g FROM authority.grants WHERE id=body->>'grant_id' AND tenant_id=m.tenant_id AND subject=m.principal AND kind='REFUND';
 SELECT * INTO h FROM authority.grant_heads WHERE grant_id=g.id FOR UPDATE;
 SELECT * INTO gv FROM authority.grant_versions WHERE id=h.version_id;
 IF l.id IS NULL OR g.id IS NULL OR expected<>0 THEN PERFORM authority.fail('LOCKED_EXACT_REFUND_REQUIRED',403); END IF;
 SELECT * INTO f FROM economic.facts WHERE id=l.capture_fact_id;
 amt:=(body->>'amount')::bigint;
 IF amt IS NULL OR amt<=0 OR amt>f.amount OR h.status<>'ACTIVE' OR gv.expires_at<=clock_timestamp() THEN PERFORM authority.fail('INVALID_REFUND_REQUEST',422); END IF;
 iid:='return_intent_'||gen_random_uuid()::text; irid:='return_ir_'||gen_random_uuid()::text;
 aid:='return_action_'||gen_random_uuid()::text; eid:='return_evidence_'||gen_random_uuid()::text; did:='return_decision_'||gen_random_uuid()::text;
 terms:=jsonb_build_object('kind','REFUND_CAPTURED_PRINCIPAL','seller_lock',l.raw::jsonb,'capture_fact_id',f.id,
 'payment_id',f.effect_id,'provider_order',(SELECT order_id FROM economic.observations WHERE id=f.observation_id),'purchase_id',f.purchase_id,'amount',amt,'currency','INR',
 'account_id',f.account_id,'integration',f.integration,'provider',f.provider);
 INSERT INTO authority.purchase_intents(id,tenant_id,requester,kind) VALUES(iid,m.tenant_id,m.principal,'REFUND');
 INSERT INTO authority.intent_revisions VALUES(irid,m.tenant_id,iid,1,terms,dispatch.hash(terms),clock_timestamp());
 INSERT INTO authority.action_revisions(id,tenant_id,intent_id,intent_revision_id,requester,requester_revision,kind,grant_id,amount,currency,terms,request_digest)
 VALUES(aid,m.tenant_id,iid,irid,m.principal,m.revision,'REFUND',g.id,amt,'INR',terms,dispatch.hash(terms));
 INSERT INTO authority.intent_heads VALUES(iid,m.tenant_id,1,irid,aid,'PREPARED');
 members:=jsonb_build_object('action',aid,'lock',l.id,'lock_hash',encode(public.digest(l.raw,'sha256'),'hex'),
 'capture',f.id,'grant_version',gv.id,'grant_epoch',h.epoch,'config_epoch',cfg.epoch,'policy',cfg.policy_version_id);
 INSERT INTO authority.evidence_revisions(id,tenant_id,action_id,grant_version_id,grant_epoch,config_epoch,policy_version_id,digest,return_lock_id)
 VALUES(eid,m.tenant_id,aid,gv.id,h.epoch,cfg.epoch,cfg.policy_version_id,dispatch.hash(members),l.id);
 INSERT INTO authority.evidence_members VALUES(m.tenant_id,eid,'SELLER_RETURN_LOCK',l.id::text,1),(m.tenant_id,eid,'CAPTURE_FACT',f.id::text,1),
 (m.tenant_id,eid,'ACTION',aid,1),(m.tenant_id,eid,'GRANT_VERSION',gv.id,gv.version),
 (m.tenant_id,eid,'POLICY_VERSION',cfg.policy_version_id,cfg.epoch),(m.tenant_id,eid,'CONFIG',m.tenant_id||'/REFUND',cfg.epoch);
 SELECT * INTO pv FROM authority.policy_versions WHERE id=cfg.policy_version_id;
 INSERT INTO authority.policy_decisions VALUES(did,m.tenant_id,aid,eid,pv.id,dispatch.hash(members),amt<=pv.max_amount,amt>pv.approval_above,
 ARRAY['EXACT_LOCKED_RETURN'],'authority-v1',clock_timestamp());
 PERFORM returns.guard(aid);
 result:=jsonb_build_object('action_id',aid,'evidence_id',eid,'decision_id',did,'revision',1,'approval_required',amt>pv.approval_above);
 ELSE
 SELECT * INTO a FROM authority.action_revisions WHERE id=body->>'action_id' AND tenant_id=m.tenant_id;
 SELECT * INTO e FROM authority.evidence_revisions WHERE action_id=a.id AND id=body->>'evidence_id' AND return_lock_id IS NOT NULL;
 SELECT * INTO d FROM authority.policy_decisions WHERE action_id=a.id AND evidence_id=e.id AND id=body->>'decision_id';
 PERFORM returns.guard(a.id);
 SELECT * INTO ih FROM authority.intent_heads WHERE intent_id=a.intent_id;
 IF e.id IS NULL OR d.id IS NULL OR ih.status<>'PREPARED' OR ih.revision<>expected OR ih.action_id<>a.id THEN PERFORM authority.fail('EXACT_ACTION_REVISION_REQUIRED'); END IF;
 IF family='approve' THEN
 PERFORM authority.require_role(m,'REFUND_APPROVER');
 IF m.principal=a.requester OR NOT d.approval_required THEN PERFORM authority.fail('INDEPENDENT_APPROVAL_REQUIRED',403); END IF;
 expires:=(body->>'expires_at')::timestamptz;
 IF expires IS NULL OR expires<=clock_timestamp() OR expires>clock_timestamp()+interval '1 hour' THEN PERFORM authority.fail('APPROVAL_EXPIRY_INVALID',422); END IF;
 apid:='return_approval_'||gen_random_uuid()::text;
 INSERT INTO authority.approvals VALUES(apid,m.tenant_id,a.id,e.id,d.id,e.digest,m.principal,m.revision,expires,clock_timestamp());
 INSERT INTO authority.approval_heads VALUES(apid,m.tenant_id,'AVAILABLE',NULL);
 result:=jsonb_build_object('approval_id',apid,'revision',ih.revision);
 ELSE
 PERFORM authority.require_role(m,'REFUND_REQUESTER');
 IF m.principal<>a.requester THEN PERFORM authority.fail('EXACT_REQUESTER_REQUIRED',403); END IF;
 IF d.approval_required AND body->>'approval_id' IS NULL THEN PERFORM authority.fail('REFUND_APPROVAL_REQUIRED',403); END IF;
 IF NOT d.approval_required AND body->>'approval_id' IS NOT NULL THEN PERFORM authority.fail('UNEXPECTED_APPROVAL',422); END IF;
 PERFORM returns.guard(a.id,body->>'approval_id');
 SELECT * INTO h FROM authority.grant_heads WHERE grant_id=a.grant_id;
 SELECT * INTO l FROM returns.locks WHERE id=e.return_lock_id;
 SELECT * INTO f FROM economic.facts WHERE id=l.capture_fact_id;
 SELECT * INTO pp FROM returns.principal WHERE capture_fact_id=f.id FOR UPDATE;
 -- Last principal lock may wait; recheck temporal and current authority afterwards.
 PERFORM authority.actor(token); PERFORM returns.guard(a.id,body->>'approval_id');
 IF pp.processed+pp.unresolved+a.amount>pp.captured THEN PERFORM authority.fail('REFUND_PRINCIPAL_EXHAUSTED',403); END IF;
 IF h.free<a.amount THEN PERFORM authority.fail('REFUND_GRANT_EXHAUSTED',403); END IF;
 rsid:='return_reservation_'||gen_random_uuid()::text; oid:=gen_random_uuid(); opid:=gen_random_uuid();
 INSERT INTO authority.reservations VALUES(rsid,m.tenant_id,a.grant_id,e.grant_version_id,e.grant_epoch,a.id,a.intent_revision_id,rid,a.amount,a.currency,a.request_digest,clock_timestamp());
 INSERT INTO authority.reservation_heads VALUES(rsid,m.tenant_id,'RESERVED');
 UPDATE authority.grant_heads SET reserved=reserved+a.amount,free=free-a.amount WHERE grant_id=a.grant_id;
 INSERT INTO authority.authority_movements VALUES('return_movement_'||gen_random_uuid()::text,m.tenant_id,a.grant_id,e.grant_version_id,e.grant_epoch,rsid,rid,
 'RESERVE',a.amount,0,a.amount,-a.amount,'COMMERCIAL_REFUND_ADMISSION',clock_timestamp());
 UPDATE authority.intent_heads SET revision=revision+1,status='ADMITTED' WHERE intent_id=a.intent_id;
 IF d.approval_required THEN UPDATE authority.approval_heads SET status='CONSUMED',consumed_by=rsid WHERE approval_id=body->>'approval_id'; END IF;
 INSERT INTO authority.admitted_work VALUES('return_work_'||gen_random_uuid()::text,m.tenant_id,rsid,a.id,'LOCAL_ADMISSION_ONLY',false);
 UPDATE returns.principal SET unresolved=unresolved+a.amount WHERE capture_fact_id=f.id;
 INSERT INTO returns.principal_reservations VALUES(oid,f.id,a.amount,clock_timestamp());
 INSERT INTO returns.obligations VALUES(oid,m.tenant_id,l.purchase_operation,f.purchase_id,f.effect_id,(SELECT order_id FROM economic.observations WHERE id=f.observation_id),f.id,l.id,a.amount,a.currency,
 f.account_id,f.integration,f.provider,rsid,a.id,e.id,d.id,body->>'approval_id',clock_timestamp());
 payload:=jsonb_build_object('payment_id',f.effect_id,'amount',a.amount,'currency','INR',
 'notes',jsonb_build_object('interlock_operation',opid,'interlock_obligation',oid,'interlock_account',f.account_id,'interlock_integration',f.integration));
 INSERT INTO returns.operations(id,obligation_id,operation_key,body,request_digest) VALUES(opid,oid,'refund_'||opid,payload,dispatch.hash(payload));
 INSERT INTO returns.runtime(operation_id) VALUES(opid); INSERT INTO returns.outbox(operation_id) VALUES(opid);
 result:=jsonb_build_object('obligation_id',oid,'operation_id',opid,'reservation_id',rsid,'revision',ih.revision+1,'state','QUEUED');
 END IF;
 END IF;
 result:=result||jsonb_build_object('command_id',rid);
 INSERT INTO authority.request_identities VALUES(rid,m.tenant_id,m.principal,'return-'||family,idem,digest,result,clock_timestamp());
 INSERT INTO authority.audit_events VALUES('return_audit_'||gen_random_uuid()::text,m.tenant_id,m.principal,rid,'return-'||family,result,clock_timestamp());
 RETURN result;
END $$;

CREATE FUNCTION returns.binding(secret text,opid uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,returns,pg_temp AS $$
DECLARE o returns.operations; b returns.obligations; BEGIN
 SELECT * INTO o FROM returns.operations WHERE id=opid;
 SELECT * INTO b FROM returns.obligations WHERE id=o.obligation_id;
 PERFORM dispatch.service_actor(secret,b.tenant_id,'OBSERVE');
 RETURN jsonb_build_object('operation',to_jsonb(o),'obligation',to_jsonb(b));
END $$;

CREATE FUNCTION returns.claim(secret text,opid uuid DEFAULT NULL) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,returns,pg_temp AS $$
DECLARE q returns.outbox; b returns.obligations; cid uuid:=gen_random_uuid(); cred dispatch.service_credentials; BEGIN
 SELECT x.* INTO q FROM returns.outbox x JOIN returns.operations o ON o.id=x.operation_id JOIN returns.obligations ob ON ob.id=o.obligation_id
 WHERE NOT x.completed AND (opid IS NULL OR x.operation_id=opid) AND (x.lease_until IS NULL OR x.lease_until<clock_timestamp())
 AND EXISTS(SELECT 1 FROM dispatch.service_scopes s JOIN dispatch.service_credentials c ON c.id=s.credential_id
 WHERE c.token_hash=encode(public.digest(secret,'sha256'),'hex') AND s.tenant_id=ob.tenant_id AND 'CLAIM'=ANY(s.verbs))
 ORDER BY x.operation_id FOR UPDATE OF x SKIP LOCKED LIMIT 1;
 IF q.operation_id IS NULL THEN RETURN NULL; END IF;
 SELECT ob.* INTO b FROM returns.obligations ob JOIN returns.operations o ON o.obligation_id=ob.id WHERE o.id=q.operation_id;
 cred:=dispatch.service_actor(secret,b.tenant_id,'CLAIM');
 UPDATE returns.outbox SET claim_id=cid,credential_id=cred.id,lease_until=clock_timestamp()+interval '30 seconds' WHERE operation_id=q.operation_id;
 RETURN jsonb_build_object('operation_id',q.operation_id,'claim_id',cid);
END $$;

CREATE FUNCTION returns.cutoff(secret text,opid uuid,claimid uuid,request_hash text) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,returns,pg_temp AS $$
DECLARE o returns.operations; b returns.obligations; c dispatch.service_credentials; r returns.runtime; q returns.outbox;
 a authority.action_revisions; e authority.evidence_revisions; gv authority.grant_versions; cfg authority.tenant_control; attempt uuid;
BEGIN
 SELECT * INTO o FROM returns.operations WHERE id=opid;
 SELECT * INTO b FROM returns.obligations WHERE id=o.obligation_id;
 SELECT * INTO cfg FROM authority.tenant_control WHERE tenant_id=b.tenant_id AND kind='REFUND' FOR SHARE;
 c:=dispatch.service_actor(secret,b.tenant_id,'DISPATCH');
 PERFORM 1 FROM dispatch.service_credentials WHERE id=c.id FOR SHARE;
 PERFORM 1 FROM dispatch.service_scopes WHERE credential_id=c.id AND tenant_id=b.tenant_id FOR SHARE;
 PERFORM 1 FROM dispatch.integrations WHERE tenant_id=b.tenant_id FOR SHARE;
 PERFORM returns.guard(b.action_id,b.approval_id,true);
 SELECT * INTO a FROM authority.action_revisions WHERE id=b.action_id;
 SELECT * INTO e FROM authority.evidence_revisions WHERE id=b.evidence_id;
 SELECT * INTO gv FROM authority.grant_versions WHERE id=e.grant_version_id;
 PERFORM 1 FROM authority.reservation_heads WHERE reservation_id=b.reservation_id AND status='RESERVED' FOR SHARE;
 IF NOT FOUND THEN PERFORM authority.fail('REFUND_RESERVATION_REQUIRED',403); END IF;
 IF b.approval_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM authority.approval_heads WHERE approval_id=b.approval_id AND consumed_by=b.reservation_id) THEN
 PERFORM authority.fail('EXACT_CONSUMED_APPROVAL_REQUIRED',403); END IF;
 PERFORM 1 FROM returns.principal WHERE capture_fact_id=b.capture_fact_id FOR SHARE;
 SELECT * INTO r FROM returns.runtime WHERE operation_id=o.id FOR UPDATE;
 SELECT * INTO q FROM returns.outbox WHERE operation_id=o.id FOR UPDATE;
 -- Fresh checks after every possibly blocking lock; one mutation only, no replay endpoint.
 c:=dispatch.service_actor(secret,b.tenant_id,'DISPATCH');
 PERFORM returns.guard(b.action_id,b.approval_id,true);
 IF r.state<>'QUEUED' OR r.blocked OR q.completed OR q.claim_id IS DISTINCT FROM claimid OR q.lease_until<=clock_timestamp()
 OR c.expires_at<=clock_timestamp() OR request_hash IS DISTINCT FROM o.request_digest OR dispatch.hash(o.body)<>o.request_digest
 OR o.replay_class<>'NEVER_AUTOMATIC_REPLAY' OR b.provider<>'REFERENCE_SYNTHETIC'
 OR NOT EXISTS(SELECT 1 FROM dispatch.integrations WHERE tenant_id=b.tenant_id AND account_id=b.account_id AND integration=b.integration AND adapter=b.provider)
 THEN PERFORM authority.fail('REFUND_HANDOFF_DENIED',403); END IF;
 INSERT INTO returns.attempts(operation_id,request_digest) VALUES(o.id,o.request_digest) RETURNING id INTO attempt;
 INSERT INTO returns.dispatch_records VALUES(attempt,o.id,c.id,c.revision,e.grant_version_id,e.grant_epoch,e.config_epoch,b.approval_id,clock_timestamp(),
 jsonb_build_object('obligation',b.id,'action',a.id,'evidence',e.id,'decision',b.decision_id,'reservation',b.reservation_id,
 'lock',b.lock_id,'principal',b.capture_fact_id,'account',b.account_id,'integration',b.integration,'payment',b.payment_id,
 'amount',b.amount,'currency',b.currency,'grant_expiry',gv.expires_at));
 INSERT INTO returns.recovery_duties VALUES(o.id,b.id,clock_timestamp());
 UPDATE returns.runtime SET state='UNKNOWN',updated_at=clock_timestamp() WHERE operation_id=o.id;
 UPDATE returns.outbox SET completed=true WHERE operation_id=o.id;
 RETURN attempt;
END $$;

CREATE FUNCTION returns.observe(rid uuid) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,returns,pg_temp AS $$
DECLARE rc economic.receipts; ch economic.channels; v jsonb; p jsonb; o returns.operations; b returns.obligations;
 oid uuid; reason text; amt bigint; kstatus text; known uuid;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM economic.receipt_states WHERE receipt_id=rid AND authentication='AUTHENTICATED') THEN RETURN NULL; END IF;
 SELECT * INTO rc FROM economic.receipts WHERE id=rid;
 SELECT * INTO ch FROM economic.channels WHERE id=rc.channel_id;
 PERFORM pg_advisory_xact_lock(hashtextextended('refund-receipt:'||rid,0));
 SELECT id INTO oid FROM returns.observations WHERE receipt_id=rid;
 IF oid IS NOT NULL THEN RETURN oid; END IF;
 BEGIN
 v:=convert_from(public.pgp_sym_decrypt_bytea(rc.body,ch.secret),'UTF8')::jsonb;
 IF NOT (v->'payload' ? 'refund') THEN RETURN NULL; END IF;
 p:=v->'payload'->'refund'->'entity'; amt:=(p->>'amount')::bigint; kstatus:=upper(p->>'status');
 IF amt IS NULL OR amt<=0 OR p->>'currency' IS DISTINCT FROM 'INR' OR p->>'id' IS NULL OR p->>'payment_id' IS NULL
 OR kstatus IS NULL OR kstatus NOT IN ('PENDING','PROCESSED','FAILED') OR ch.provider NOT IN ('REFERENCE_SYNTHETIC','RAZORPAY_TEST') THEN RAISE EXCEPTION 'schema'; END IF;
 IF v->>'account_id' IS DISTINCT FROM ch.account_id THEN RAISE EXCEPTION 'account'; END IF;
 IF ch.provider='REFERENCE_SYNTHETIC' AND (v->>'integration' IS DISTINCT FROM ch.integration OR v->>'tenant_id' IS DISTINCT FROM ch.tenant_id) THEN RAISE EXCEPTION 'scope'; END IF;
 EXCEPTION WHEN OTHERS THEN reason:='MALFORMED_OR_SCOPE'; END;
 IF reason IS NULL THEN
 -- Serialize exact-payment evidence with admission/cutoff even when the refund
 -- cannot yet be attributed to a local commercial obligation.
 PERFORM 1 FROM returns.principal pp JOIN economic.facts cap ON cap.id=pp.capture_fact_id
 WHERE cap.provider=ch.provider AND cap.account_id=ch.account_id AND cap.integration=ch.integration
 AND cap.effect_id=p->>'payment_id' FOR UPDATE OF pp;
 -- The operation token is indispensable; equal amount/payment never creates ownership.
 SELECT * INTO o FROM returns.operations WHERE id::text=p->'notes'->>'interlock_operation';
 SELECT * INTO b FROM returns.obligations WHERE id=o.obligation_id;
 IF o.id IS NOT NULL AND (b.account_id IS DISTINCT FROM ch.account_id OR b.integration IS DISTINCT FROM ch.integration
 OR b.provider IS DISTINCT FROM ch.provider OR b.tenant_id IS DISTINCT FROM ch.tenant_id OR b.payment_id IS DISTINCT FROM p->>'payment_id'
 OR b.amount<>amt OR b.currency IS DISTINCT FROM p->>'currency' OR b.id::text IS DISTINCT FROM p->'notes'->>'interlock_obligation'
 OR b.account_id IS DISTINCT FROM p->'notes'->>'interlock_account' OR b.integration IS DISTINCT FROM p->'notes'->>'interlock_integration') THEN
 reason:='EXACT_REFUND_SUBSTITUTION'; END IF;
 IF o.id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM returns.dispatch_records WHERE operation_id=o.id) THEN reason:='UNCOMMITTED_REFUND_EFFECT'; END IF;
 END IF;
 IF reason IS NOT NULL THEN
 INSERT INTO returns.conflicts(receipt_id,operation_id,reason) VALUES(rid,o.id,reason);
 IF o.id IS NOT NULL THEN UPDATE returns.runtime SET blocked=true WHERE operation_id=o.id; END IF;
 RETURN NULL; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('refund-effect:'||ch.provider||':'||ch.account_id||':'||(p->>'id'),0));
 SELECT operation_id INTO known FROM returns.observations WHERE provider=ch.provider AND account_id=ch.account_id AND refund_id=p->>'id' LIMIT 1;
 IF known IS NOT NULL AND known IS DISTINCT FROM o.id THEN reason:='REFUND_ID_OWNERSHIP_CONFLICT'; END IF;
 IF o.id IS NOT NULL THEN
 PERFORM 1 FROM returns.runtime WHERE operation_id=o.id FOR UPDATE;
 IF EXISTS(SELECT 1 FROM returns.observations obx WHERE obx.operation_id=o.id AND (obx.refund_id IS DISTINCT FROM p->>'id'
 OR (obx.status='FAILED' AND kstatus='PROCESSED') OR (obx.status='PROCESSED' AND kstatus='FAILED'))) THEN reason:='CONTRADICTORY_REFUND'; END IF;
 END IF;
 INSERT INTO returns.observations(receipt_id,provider,account_id,integration,tenant_id,payment_id,refund_id,amount,currency,status,operation_id,obligation_id,body)
 VALUES(rid,ch.provider,ch.account_id,ch.integration,b.tenant_id,p->>'payment_id',p->>'id',amt,'INR',kstatus,o.id,b.id,v) RETURNING id INTO oid;
 IF reason IS NOT NULL THEN
 INSERT INTO returns.conflicts(receipt_id,operation_id,reason) VALUES(rid,o.id,reason);
 UPDATE returns.runtime SET blocked=true WHERE operation_id=o.id;
 IF known IS NOT NULL THEN UPDATE returns.runtime SET blocked=true WHERE operation_id=known; END IF;
 END IF;
 UPDATE economic.receipt_states SET parsing='REFUND_PARSED',interpreted=true,classification=CASE WHEN o.id IS NULL THEN 'UNMATCHED_REFUND' ELSE 'MATCHED_REFUND' END WHERE receipt_id=rid;
 RETURN oid;
END $$;

CREATE FUNCTION returns.derive(opid uuid,observation uuid DEFAULT NULL) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,returns,pg_temp AS $$
DECLARE o returns.operations; b returns.obligations; ob returns.observations; r returns.runtime;
 res authority.reservations; h authority.grant_heads; ch economic.channels;
BEGIN
 SELECT * INTO o FROM returns.operations WHERE id=opid;
 SELECT * INTO b FROM returns.obligations WHERE id=o.obligation_id;
 IF b.id IS NULL THEN PERFORM authority.fail('ACCEPTED_OBLIGATION_REQUIRED',403); END IF;
 -- Same lock order as admission: grant -> principal -> runtime, then exact fact identity.
 SELECT * INTO res FROM authority.reservations WHERE id=b.reservation_id;
 SELECT * INTO h FROM authority.grant_heads WHERE grant_id=res.grant_id FOR UPDATE;
 PERFORM 1 FROM returns.principal WHERE capture_fact_id=b.capture_fact_id FOR UPDATE;
 SELECT * INTO r FROM returns.runtime WHERE operation_id=o.id FOR UPDATE;
 INSERT INTO returns.facts(kind,obligation_id,operation_id,capture_fact_id,amount,currency)
 VALUES('REFUND_ACCEPTED',b.id,o.id,b.capture_fact_id,b.amount,b.currency) ON CONFLICT DO NOTHING;
 IF observation IS NOT NULL THEN
 SELECT * INTO ob FROM returns.observations WHERE id=observation;
 SELECT c.* INTO ch FROM economic.channels c JOIN economic.receipts rc ON rc.channel_id=c.id WHERE rc.id=ob.receipt_id;
 IF ob.operation_id IS DISTINCT FROM o.id OR ob.obligation_id IS DISTINCT FROM b.id THEN PERFORM authority.fail('EXACT_REFUND_OBSERVATION_REQUIRED',403); END IF;
 IF ob.status='PROCESSED' AND NOT r.blocked AND EXISTS(SELECT 1 FROM returns.dispatch_records WHERE operation_id=o.id)
 AND (ch.provider<>'RAZORPAY_TEST' OR ch.source='CANONICAL_READ')
 AND NOT EXISTS(SELECT 1 FROM returns.facts WHERE obligation_id=b.id AND kind='REFUND_PROCESSED') THEN
 INSERT INTO returns.facts(kind,obligation_id,operation_id,observation_id,capture_fact_id,amount,currency,refund_id)
 VALUES('REFUND_PROCESSED',b.id,o.id,ob.id,b.capture_fact_id,b.amount,b.currency,ob.refund_id);
 UPDATE returns.principal SET unresolved=unresolved-b.amount,processed=processed+b.amount WHERE capture_fact_id=b.capture_fact_id;
 UPDATE authority.grant_heads SET reserved=reserved-b.amount,consumed=consumed+b.amount WHERE grant_id=res.grant_id;
 INSERT INTO authority.authority_movements VALUES('refund_processed_'||gen_random_uuid()::text,res.tenant_id,res.grant_id,res.grant_version_id,res.epoch,res.id,res.request_id,
 'REFUND_PROCESSED',b.amount,b.amount,-b.amount,0,'TRUSTED_REFUND_PROCESSED',clock_timestamp());
 UPDATE authority.reservation_heads SET status='REFUND_PROCESSED' WHERE reservation_id=res.id;
 UPDATE returns.runtime SET state='PROCESSED',refund_id=ob.refund_id,updated_at=clock_timestamp() WHERE operation_id=o.id;
 END IF;
 END IF;
 RETURN (SELECT jsonb_agg(id ORDER BY CASE kind WHEN 'REFUND_ACCEPTED' THEN 1 ELSE 2 END) FROM returns.facts WHERE obligation_id=b.id);
END $$;

CREATE FUNCTION returns.post(fid uuid) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,returns,pg_temp AS $$
DECLARE f returns.facts; pid uuid; dr text; cr text; tmpl text; BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('refund-post:'||fid,0));
 SELECT id INTO pid FROM returns.postings WHERE fact_id=fid; IF pid IS NOT NULL THEN RETURN pid; END IF;
 SELECT * INTO f FROM returns.facts WHERE id=fid;
 IF f.id IS NULL THEN PERFORM authority.fail('TYPED_REFUND_FACT_REQUIRED',403); END IF;
 IF f.kind='REFUND_ACCEPTED' THEN dr:='SalesReturns'; cr:='RefundPayable'; tmpl:='RETURN_TO_PAYABLE';
 IF NOT EXISTS(SELECT 1 FROM returns.obligations ob JOIN economic.facts li ON li.parent_fact_id=ob.capture_fact_id AND li.kind='ORDER_LIABILITY' JOIN economic.facts ef ON ef.parent_fact_id=li.id AND ef.kind='REVENUE'
 JOIN economic.postings ep ON ep.fact_id=ef.id WHERE ob.id=f.obligation_id) THEN PERFORM authority.fail('BOOKED_REVENUE_REQUIRED'); END IF;
 ELSE dr:='RefundPayable'; cr:='ProviderClearing'; tmpl:='PAYABLE_TO_PROVIDER';
 IF NOT EXISTS(SELECT 1 FROM returns.facts af JOIN returns.postings ap ON ap.fact_id=af.id WHERE af.obligation_id=f.obligation_id AND af.kind='REFUND_ACCEPTED') THEN
 PERFORM authority.fail('REFUND_PAYABLE_POSTING_REQUIRED'); END IF;
 END IF;
 INSERT INTO returns.postings(fact_id,template) VALUES(fid,tmpl) RETURNING id INTO pid;
 INSERT INTO returns.lines VALUES(pid,1,dr,'DEBIT',f.amount,f.currency),(pid,2,cr,'CREDIT',f.amount,f.currency);
 RETURN pid;
END $$;

CREATE FUNCTION returns.bundle() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,returns,pg_temp AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM authority.reservations r JOIN authority.evidence_revisions e ON e.action_id=r.action_id
 WHERE e.return_lock_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM returns.obligations b WHERE b.reservation_id=r.id)) THEN
 RAISE EXCEPTION 'return admission requires the complete commercial bundle' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM returns.obligations b JOIN authority.reservation_heads h ON h.reservation_id=b.reservation_id
 WHERE h.status IS DISTINCT FROM CASE WHEN EXISTS(SELECT 1 FROM returns.facts f WHERE f.obligation_id=b.id AND f.kind='REFUND_PROCESSED')
 THEN 'REFUND_PROCESSED' ELSE 'RESERVED' END
 OR (SELECT coalesce(sum(m.delta_reserved),0) FROM authority.authority_movements m WHERE m.reservation_id=b.reservation_id) IS DISTINCT FROM
 CASE WHEN EXISTS(SELECT 1 FROM returns.facts f WHERE f.obligation_id=b.id AND f.kind='REFUND_PROCESSED') THEN 0 ELSE b.amount END
 OR (SELECT coalesce(sum(m.delta_consumed),0) FROM authority.authority_movements m WHERE m.reservation_id=b.reservation_id) IS DISTINCT FROM
 CASE WHEN EXISTS(SELECT 1 FROM returns.facts f WHERE f.obligation_id=b.id AND f.kind='REFUND_PROCESSED') THEN b.amount ELSE 0 END
 OR (SELECT coalesce(sum(m.delta_free),0) FROM authority.authority_movements m WHERE m.reservation_id=b.reservation_id) IS DISTINCT FROM -b.amount) THEN
 RAISE EXCEPTION 'commercial refund merchant reservation reconstruction mismatch' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM returns.principal p WHERE p.unresolved IS DISTINCT FROM
 (SELECT coalesce(sum(r.amount),0) FROM returns.principal_reservations r WHERE r.capture_fact_id=p.capture_fact_id AND NOT EXISTS(
 SELECT 1 FROM returns.facts f WHERE f.obligation_id=r.obligation_id AND kind='REFUND_PROCESSED')) OR p.processed IS DISTINCT FROM
 (SELECT coalesce(sum(f.amount),0) FROM returns.facts f WHERE f.capture_fact_id=p.capture_fact_id AND f.kind='REFUND_PROCESSED')) THEN
 RAISE EXCEPTION 'refund principal reconstruction mismatch' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM returns.obligations b WHERE NOT EXISTS(SELECT 1 FROM returns.operations o WHERE o.obligation_id=b.id)
 OR NOT EXISTS(SELECT 1 FROM returns.principal_reservations r WHERE r.obligation_id=b.id AND r.capture_fact_id=b.capture_fact_id AND r.amount=b.amount)
 OR NOT EXISTS(SELECT 1 FROM authority.reservations r WHERE r.id=b.reservation_id AND r.amount=b.amount AND r.action_id=b.action_id)
 OR NOT EXISTS(SELECT 1 FROM returns.locks l JOIN authority.evidence_revisions e ON e.return_lock_id=l.id JOIN authority.action_revisions a ON a.id=e.action_id
 WHERE l.id=b.lock_id AND l.capture_fact_id=b.capture_fact_id AND e.id=b.evidence_id AND a.id=b.action_id AND a.created_at>=l.confirmed_at)) THEN
 RAISE EXCEPTION 'incomplete exact commercial refund bundle' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM returns.attempts a WHERE NOT EXISTS(SELECT 1 FROM returns.dispatch_records d WHERE d.attempt_id=a.id)
 OR NOT EXISTS(SELECT 1 FROM returns.recovery_duties r WHERE r.operation_id=a.operation_id)
 OR EXISTS(SELECT 1 FROM returns.outbox q WHERE q.operation_id=a.operation_id AND NOT q.completed)) THEN
 RAISE EXCEPTION 'incomplete refund dispatch bundle' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER bundle AFTER INSERT OR UPDATE ON returns.principal DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION returns.bundle();
CREATE CONSTRAINT TRIGGER bundle AFTER INSERT ON returns.obligations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION returns.bundle();
CREATE CONSTRAINT TRIGGER bundle AFTER INSERT ON returns.attempts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION returns.bundle();

CREATE FUNCTION returns.journal_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,returns,pg_temp AS $$
DECLARE p returns.postings; f returns.facts; actual jsonb; expected jsonb; BEGIN
 SELECT * INTO p FROM returns.postings WHERE id=NEW.id; SELECT * INTO f FROM returns.facts WHERE id=p.fact_id;
 expected:=CASE f.kind WHEN 'REFUND_ACCEPTED' THEN '["SalesReturns","RefundPayable"]'::jsonb ELSE '["RefundPayable","ProviderClearing"]'::jsonb END;
 SELECT jsonb_agg(account ORDER BY ordinal) INTO actual FROM returns.lines WHERE posting_id=p.id;
 IF actual IS DISTINCT FROM expected OR p.template IS DISTINCT FROM (CASE f.kind WHEN 'REFUND_ACCEPTED' THEN 'RETURN_TO_PAYABLE' ELSE 'PAYABLE_TO_PROVIDER' END)
 OR EXISTS(SELECT 1 FROM returns.lines WHERE posting_id=p.id AND (amount<>f.amount OR currency<>f.currency OR side<>CASE ordinal WHEN 1 THEN 'DEBIT' ELSE 'CREDIT' END)) THEN
 RAISE EXCEPTION 'refund journal semantic bundle invalid' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER journal_bundle AFTER INSERT ON returns.postings DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION returns.journal_guard();

CREATE FUNCTION returns.close_proof(token text,lid uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,returns,pg_temp AS $$
DECLARE l returns.locks; p returns.principal; raw text; key text; m authority.memberships; BEGIN
 m:=authority.actor(token); PERFORM authority.require_role(m,'REFUND_REQUESTER');
 SELECT * INTO l FROM returns.locks WHERE id=lid AND tenant_id=m.tenant_id;
 SELECT * INTO p FROM returns.principal WHERE capture_fact_id=l.capture_fact_id FOR UPDATE;
 IF l.id IS NULL OR p.processed<>p.captured OR p.unresolved<>0 OR EXISTS(
 SELECT 1 FROM returns.obligations b JOIN returns.operations o ON o.obligation_id=b.id JOIN returns.runtime r ON r.operation_id=o.id
 WHERE b.lock_id=l.id AND r.blocked) OR EXISTS(SELECT 1 FROM returns.facts f WHERE f.capture_fact_id=p.capture_fact_id AND
 NOT EXISTS(SELECT 1 FROM returns.postings jp WHERE jp.fact_id=f.id)) OR EXISTS(
 SELECT 1 FROM returns.observations ob JOIN economic.facts cap ON cap.id=l.capture_fact_id WHERE ob.operation_id IS NULL
 AND ob.provider=cap.provider AND ob.account_id=cap.account_id AND ob.integration=cap.integration AND ob.payment_id=cap.effect_id
 AND NOT EXISTS(SELECT 1 FROM returns.facts rf JOIN returns.observations mo ON mo.id=rf.observation_id
 WHERE rf.capture_fact_id=cap.id AND rf.kind='REFUND_PROCESSED' AND mo.refund_id=ob.refund_id)) THEN
 PERFORM authority.fail('FULL_BOOKED_PRINCIPAL_RESOLUTION_REQUIRED',403); END IF;
 raw:=dispatch.canonical(jsonb_build_object('kind','RETURN_CLOSE','lock_id',l.id,'lock_hash',encode(public.digest(l.raw,'sha256'),'hex'),
 'capture_fact_id',p.capture_fact_id,'processed',p.processed,'unresolved',p.unresolved));
 SELECT secret INTO key FROM economic.channels WHERE id='seller-capability-key';
 RETURN jsonb_build_object('raw',raw,'signature',encode(public.hmac(raw,key,'sha256'),'hex'));
END $$;

CREATE FUNCTION returns.confirm_close(token text,lid uuid,raw text,sig text) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,returns,pg_temp AS $$
DECLARE l returns.locks; v jsonb; proof jsonb; key text; existing returns.closures; BEGIN
 proof:=returns.close_proof(token,lid); SELECT * INTO l FROM returns.locks WHERE id=lid;
 SELECT secret INTO key FROM economic.channels WHERE id='seller-'||l.tenant_id;
 IF sig IS DISTINCT FROM encode(public.hmac(raw,key,'sha256'),'hex') THEN PERFORM authority.fail('SELLER_CLOSURE_AUTHENTICATION_REQUIRED',403); END IF;
 v:=raw::jsonb;
 IF v->>'kind' IS DISTINCT FROM 'RETURN_COMPLETED' OR v->>'closure_proof_hash' IS DISTINCT FROM encode(public.digest(proof->>'raw','sha256'),'hex')
 OR (v-'kind'-'closure_proof_hash') IS DISTINCT FROM (l.raw::jsonb-'kind') THEN PERFORM authority.fail('EXACT_SELLER_CLOSURE_REQUIRED',403); END IF;
 SELECT * INTO existing FROM returns.closures WHERE lock_id=lid;
 IF existing.lock_id IS NOT NULL THEN IF existing.raw<>raw THEN PERFORM authority.fail('CLOSURE_SUBSTITUTION'); END IF; RETURN lid; END IF;
 INSERT INTO returns.closures VALUES(lid,raw,sig,clock_timestamp()); RETURN lid;
END $$;

CREATE FUNCTION returns.inspect(token text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,returns,pg_temp AS $$
DECLARE m authority.memberships; result jsonb; BEGIN
 m:=authority.actor(token);
 IF NOT (ARRAY['REFUND_REQUESTER','REFUND_ISSUER','REFUND_APPROVER']::text[] && m.roles) THEN PERFORM authority.fail('REFUND_VISIBILITY_REQUIRED',403); END IF;
 SELECT jsonb_build_object('tenant_id',m.tenant_id,'principal_id',m.principal,
 'mismatches',jsonb_build_object(
 'CAPTURED_BUT_UNFULFILLED',(SELECT count(*) FROM economic.facts cap WHERE cap.tenant_id=m.tenant_id AND cap.kind='CAPTURE' AND NOT EXISTS(SELECT 1 FROM economic.facts sf WHERE sf.operation_id=cap.operation_id AND sf.kind='SELLER_FULFILLED')),
 'FULFILLED_WITHOUT_MATCHED_CAPTURE',(SELECT count(*) FROM economic.observations ob WHERE ob.tenant_id=m.tenant_id AND ob.kind='FULFILLED' AND NOT EXISTS(SELECT 1 FROM economic.facts cap WHERE cap.operation_id=ob.operation_id AND cap.kind='CAPTURE')),
 'REFUND_PROCESSED_ENTITLEMENT_ACTIVE',(SELECT count(DISTINCT b.lock_id) FROM returns.obligations b JOIN returns.facts f ON f.obligation_id=b.id AND f.kind='REFUND_PROCESSED' WHERE b.tenant_id=m.tenant_id AND NOT EXISTS(SELECT 1 FROM returns.closures c WHERE c.lock_id=b.lock_id)),
 'PROVIDER_REFUND_MISSING_LOCALLY',(SELECT count(*) FROM returns.observations ob JOIN economic.facts cap ON cap.provider=ob.provider
 AND cap.account_id=ob.account_id AND cap.integration=ob.integration AND cap.effect_id=ob.payment_id AND cap.kind='CAPTURE'
 WHERE cap.tenant_id=m.tenant_id AND ob.operation_id IS NULL),
 'ECONOMIC_FACT_UNPOSTED',(SELECT count(*) FROM returns.facts f JOIN returns.obligations b ON b.id=f.obligation_id WHERE b.tenant_id=m.tenant_id AND NOT EXISTS(SELECT 1 FROM returns.postings p WHERE p.fact_id=f.id)),
 'MUTATION_OUTCOME_UNKNOWN',(SELECT count(*) FROM returns.runtime r JOIN returns.operations o ON o.id=r.operation_id JOIN returns.obligations b ON b.id=o.obligation_id WHERE b.tenant_id=m.tenant_id AND r.state='UNKNOWN')),
 'locks',coalesce((SELECT jsonb_agg(to_jsonb(l)-'signature') FROM returns.locks l WHERE tenant_id=m.tenant_id),'[]'::jsonb),
 'principal',coalesce((SELECT jsonb_agg(to_jsonb(p)||jsonb_build_object('refundable',p.captured-p.processed-p.unresolved)) FROM returns.principal p JOIN returns.locks l ON l.capture_fact_id=p.capture_fact_id WHERE l.tenant_id=m.tenant_id),'[]'::jsonb),
 'grants',coalesce((SELECT jsonb_agg(to_jsonb(h)||jsonb_build_object('binding',to_jsonb(b),'expires_at',v.expires_at)) FROM authority.grant_heads h JOIN returns.grant_bindings b ON b.grant_id=h.grant_id JOIN authority.grant_versions v ON v.id=h.version_id WHERE b.tenant_id=m.tenant_id),'[]'::jsonb),
 'obligations',coalesce((SELECT jsonb_agg(to_jsonb(b)) FROM returns.obligations b WHERE tenant_id=m.tenant_id),'[]'::jsonb),
 'operations',coalesce((SELECT jsonb_agg(to_jsonb(o)||jsonb_build_object('runtime',to_jsonb(r),'recovery_duty',EXISTS(SELECT 1 FROM returns.recovery_duties d WHERE d.operation_id=o.id),'dispatch_records',coalesce((SELECT jsonb_agg(to_jsonb(dr)) FROM returns.dispatch_records dr WHERE dr.operation_id=o.id),'[]'::jsonb))) FROM returns.operations o JOIN returns.obligations b ON b.id=o.obligation_id JOIN returns.runtime r ON r.operation_id=o.id WHERE b.tenant_id=m.tenant_id),'[]'::jsonb),
 'observations',coalesce((SELECT jsonb_agg(to_jsonb(ob)) FROM returns.observations ob WHERE tenant_id=m.tenant_id),'[]'::jsonb),
 'facts',coalesce((SELECT jsonb_agg(to_jsonb(f)) FROM returns.facts f JOIN returns.obligations b ON b.id=f.obligation_id WHERE b.tenant_id=m.tenant_id),'[]'::jsonb),
 'postings',coalesce((SELECT jsonb_agg(to_jsonb(p)||jsonb_build_object('lines',(SELECT jsonb_agg(to_jsonb(x)) FROM returns.lines x WHERE x.posting_id=p.id))) FROM returns.postings p JOIN returns.facts f ON f.id=p.fact_id JOIN returns.obligations b ON b.id=f.obligation_id WHERE b.tenant_id=m.tenant_id),'[]'::jsonb),
 'closures',coalesce((SELECT jsonb_agg(to_jsonb(c)-'signature') FROM returns.closures c JOIN returns.locks l ON l.id=c.lock_id WHERE l.tenant_id=m.tenant_id),'[]'::jsonb)) INTO result;
 RETURN result;
END $$;

CREATE FUNCTION returns.pending() RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,returns,pg_temp AS $$
 SELECT jsonb_build_object('operations',coalesce((SELECT jsonb_agg(jsonb_build_object('operation_id',o.id,'observation_id',
 (SELECT ob.id FROM returns.observations ob WHERE ob.operation_id=o.id AND ob.status='PROCESSED' ORDER BY ob.observed_at LIMIT 1))) FROM returns.operations o
 WHERE NOT EXISTS(SELECT 1 FROM returns.facts f WHERE f.operation_id=o.id AND f.kind='REFUND_PROCESSED') OR EXISTS(
 SELECT 1 FROM returns.facts f WHERE f.operation_id=o.id AND NOT EXISTS(SELECT 1 FROM returns.postings p WHERE p.fact_id=f.id))),'[]'::jsonb))
$$;

REVOKE ALL ON ALL TABLES IN SCHEMA returns FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA returns FROM PUBLIC;
DO $$ DECLARE item record; BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='interlock_returns_owner') THEN CREATE ROLE interlock_returns_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; END IF;
 GRANT USAGE ON SCHEMA public,authority,dispatch,economic TO interlock_returns_owner;
 GRANT SELECT ON ALL TABLES IN SCHEMA authority TO interlock_returns_owner;
 GRANT INSERT ON authority.grants,authority.grant_versions,authority.grant_heads,authority.purchase_intents,authority.intent_revisions,
 authority.action_revisions,authority.intent_heads,authority.evidence_revisions,authority.evidence_members,authority.policy_decisions,
 authority.approvals,authority.approval_heads,authority.reservations,authority.reservation_heads,authority.authority_movements,
 authority.admitted_work,authority.request_identities,authority.audit_events TO interlock_returns_owner;
 GRANT UPDATE ON authority.grant_heads,authority.intent_heads,authority.approval_heads,authority.reservation_heads TO interlock_returns_owner;
 GRANT UPDATE(principal) ON authority.memberships TO interlock_returns_owner;
 GRANT UPDATE(epoch) ON authority.tenant_control TO interlock_returns_owner;
 GRANT UPDATE(id) ON authority.approvals TO interlock_returns_owner;
 GRANT UPDATE(parsing,interpreted,classification) ON economic.receipt_states TO interlock_returns_owner;
 GRANT SELECT ON economic.facts,economic.observations,economic.object_heads,economic.channels,economic.receipts,economic.receipt_states,economic.accounts,economic.postings TO interlock_returns_owner;
 GRANT SELECT ON dispatch.integrations,dispatch.service_credentials,dispatch.service_scopes,dispatch.business_operations TO interlock_returns_owner;
 GRANT UPDATE(id) ON dispatch.service_credentials TO interlock_returns_owner;
 GRANT UPDATE(credential_id) ON dispatch.service_scopes TO interlock_returns_owner;
 GRANT UPDATE(tenant_id) ON dispatch.integrations TO interlock_returns_owner;
 GRANT EXECUTE ON FUNCTION authority.actor(text),authority.require_role(authority.memberships,text),authority.fail(text,int),
 dispatch.canonical(jsonb),dispatch.hash(jsonb),dispatch.service_actor(text,text,text) TO interlock_returns_owner;
 ALTER SCHEMA returns OWNER TO interlock_returns_owner;
 FOR item IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='returns' AND c.relkind='r' LOOP
 EXECUTE format('ALTER TABLE returns.%I OWNER TO interlock_returns_owner',item.relname); END LOOP;
 FOR item IN SELECT p.oid::regprocedure AS name FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='returns' LOOP
 EXECUTE format('ALTER FUNCTION %s OWNER TO interlock_returns_owner',item.name); END LOOP;
 GRANT USAGE ON SCHEMA returns TO interlock_api,interlock_broker,interlock_worker,interlock_economic;
 GRANT EXECUTE ON FUNCTION returns.command(text,text,jsonb,bigint,text),returns.prepare(text,text),returns.confirm_lock(text,text,text,text),
 returns.close_proof(text,uuid),returns.confirm_close(text,uuid,text,text),returns.inspect(text) TO interlock_api;
 GRANT EXECUTE ON FUNCTION returns.binding(text,uuid),returns.cutoff(text,uuid,uuid,text) TO interlock_broker;
 GRANT EXECUTE ON FUNCTION returns.claim(text,uuid) TO interlock_worker;
 GRANT EXECUTE ON FUNCTION returns.observe(uuid),returns.derive(uuid,uuid),returns.post(uuid),returns.pending() TO interlock_economic;
END $$;
