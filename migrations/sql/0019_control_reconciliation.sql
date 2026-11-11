CREATE SCHEMA reconciliation;
REVOKE ALL ON SCHEMA reconciliation FROM PUBLIC;
CREATE TYPE reconciliation.finding_class AS ENUM ('CAPTURED_BUT_UNFULFILLED','FULFILLED_WITHOUT_MATCHED_CAPTURE',
 'REFUND_PROCESSED_ENTITLEMENT_ACTIVE','PROVIDER_REFUND_MISSING_LOCALLY','ECONOMIC_FACT_UNPOSTED','MUTATION_OUTCOME_UNKNOWN');
CREATE TYPE reconciliation.lifecycle AS ENUM ('OPEN','RESOLVED');
CREATE TYPE reconciliation.next_step AS ENUM ('READ_PROVIDER_STATE','RECHECK_MUTATION_OUTCOME',
 'MATERIALIZE_MISSING_ECONOMIC_FACT','POST_ALREADY_AUTHORIZED_ECONOMIC_FACT','VERIFY_SELLER_ENTITLEMENT_STATE',
 'CREATE_OR_REFRESH_RECOVERY_OBLIGATION','ESCALATE_FOR_MANUAL_REVIEW');
CREATE TABLE reconciliation.runs(
 id uuid PRIMARY KEY, tenant_id text NOT NULL REFERENCES public.tenants(id), principal text NOT NULL,
 after_operation text NOT NULL, batch_size int NOT NULL CHECK(batch_size BETWEEN 1 AND 50),
 state text NOT NULL DEFAULT 'RUNNING' CHECK(state IN ('RUNNING','COMPLETE','INCOMPLETE')),
 has_more boolean NOT NULL, next_cursor text, started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz, FOREIGN KEY(tenant_id,principal) REFERENCES authority.memberships(tenant_id,principal)
);
CREATE TABLE reconciliation.members(
 run_id uuid NOT NULL REFERENCES reconciliation.runs(id), operation_id text NOT NULL REFERENCES dispatch.business_operations(id),
 state text NOT NULL DEFAULT 'PENDING' CHECK(state IN ('PENDING','RECORDED','INCONCLUSIVE')),
 evidence_count int NOT NULL DEFAULT 0, PRIMARY KEY(run_id,operation_id)
);
CREATE TABLE reconciliation.operation_heads(
 operation_id text PRIMARY KEY REFERENCES dispatch.business_operations(id), revision bigint NOT NULL DEFAULT 0
);
CREATE TABLE reconciliation.seller_reads(
 run_id uuid NOT NULL, operation_id text NOT NULL, revision bigint NOT NULL CHECK(revision>=0),
 observed_at timestamptz NOT NULL, received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 fingerprint text NOT NULL, state jsonb NOT NULL, PRIMARY KEY(run_id,operation_id),
 FOREIGN KEY(run_id,operation_id) REFERENCES reconciliation.members(run_id,operation_id)
);
CREATE TABLE reconciliation.findings(
 id uuid PRIMARY KEY, tenant_id text NOT NULL, operation_id text NOT NULL,
 class reconciliation.finding_class NOT NULL, resource_key text NOT NULL,
 state reconciliation.lifecycle NOT NULL, revision int NOT NULL CHECK(revision>0),
 evidence_fingerprint text NOT NULL CHECK(length(evidence_fingerprint)=64), evidence jsonb NOT NULL,
 next_step reconciliation.next_step NOT NULL, duty_id text REFERENCES dispatch.recovery_obligations(id),
 refund_duty_id uuid REFERENCES returns.recovery_duties(operation_id),
 first_seen_at timestamptz NOT NULL, last_seen_at timestamptz NOT NULL, resolved_at timestamptz,
 resolution_run uuid REFERENCES reconciliation.runs(id),
 UNIQUE(operation_id,class,resource_key), UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,operation_id) REFERENCES dispatch.business_operations(tenant_id,id),
 CHECK((state='RESOLVED')=(resolved_at IS NOT NULL)), CHECK((state='RESOLVED')=(resolution_run IS NOT NULL))
);
CREATE INDEX reconciliation_findings_scope ON reconciliation.findings(tenant_id,id);
CREATE INDEX reconciliation_runs_scope ON reconciliation.runs(tenant_id,id);
CREATE INDEX reconciliation_operations_scope ON dispatch.business_operations(tenant_id,id);
CREATE INDEX reconciliation_refund_observation_lookup ON returns.observations(provider,account_id,integration,payment_id,status);
CREATE TABLE reconciliation.revisions(
 finding_id uuid NOT NULL REFERENCES reconciliation.findings(id), revision int NOT NULL,
 run_id uuid NOT NULL REFERENCES reconciliation.runs(id), state reconciliation.lifecycle NOT NULL,
 evidence_fingerprint text NOT NULL, evidence jsonb NOT NULL, observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(finding_id,revision)
);
CREATE TABLE reconciliation.results(
 run_id uuid NOT NULL, operation_id text NOT NULL, snapshot_at timestamptz NOT NULL,
 local_evidence jsonb NOT NULL, finding_ids uuid[] NOT NULL,
 PRIMARY KEY(run_id,operation_id), FOREIGN KEY(run_id,operation_id) REFERENCES reconciliation.members(run_id,operation_id)
);
-- Reuse the existing duty model for fact/seller consequences; no additional authority powers.
ALTER TABLE dispatch.recovery_obligations DROP CONSTRAINT recovery_obligations_origin_kind_check;
ALTER TABLE dispatch.recovery_obligations ADD CHECK(origin_kind IN ('OPERATION','EXPOSURE','FACT'));
ALTER TABLE dispatch.recovery_obligations DROP CONSTRAINT recovery_obligations_duty_kind_check;
ALTER TABLE dispatch.recovery_obligations ADD CHECK(duty_kind IN ('RESOLVE_EXTERNAL_EFFECT','POST_VERIFIED_FACT','COMPLETE_SELLER_RETURN'));
ALTER TABLE dispatch.recovery_obligations DROP CONSTRAINT recovery_obligations_predicate_check;
ALTER TABLE dispatch.recovery_obligations ADD CHECK(predicate IN ('EXACT_ORDER_EFFECT','EXACT_EXPOSED_PAYMENT_STATUS','EXACT_FACT_POSTING','EXACT_SELLER_RETURN'));
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='dispatch.recovery_obligations'::regclass AND contype='c'
 AND pg_get_constraintdef(oid) LIKE '%exposure_id%' LOOP
 EXECUTE format('ALTER TABLE dispatch.recovery_obligations DROP CONSTRAINT %I',c.conname); END LOOP;
END $$;
ALTER TABLE dispatch.recovery_obligations ADD CHECK((origin_kind IN ('OPERATION','FACT') AND exposure_id IS NULL)
 OR (origin_kind='EXPOSURE' AND exposure_id IS NOT NULL));
-- Retain the original conflict target used by certified dispatch functions.
CREATE UNIQUE INDEX dispatch_original_duty_identity ON dispatch.recovery_obligations(operation_id,origin_kind,duty_kind) WHERE origin_kind<>'FACT';
ALTER TABLE dispatch.recovery_obligations ADD COLUMN economic_fact_id uuid REFERENCES economic.facts(id);
ALTER TABLE dispatch.recovery_obligations ADD COLUMN refund_fact_id uuid REFERENCES returns.facts(id);
ALTER TABLE dispatch.recovery_obligations ADD COLUMN return_lock_id uuid REFERENCES returns.locks(id);
ALTER TABLE dispatch.recovery_obligations ADD CHECK((origin_kind='FACT')=
 (num_nonnulls(economic_fact_id,refund_fact_id,return_lock_id)=1));
ALTER TABLE dispatch.recovery_obligations ADD CONSTRAINT reconciliation_followup_kinds CHECK(
 (origin_kind<>'FACT' AND duty_kind='RESOLVE_EXTERNAL_EFFECT') OR
 (origin_kind='FACT' AND duty_kind='POST_VERIFIED_FACT' AND predicate='EXACT_FACT_POSTING' AND return_lock_id IS NULL) OR
 (origin_kind='FACT' AND duty_kind='COMPLETE_SELLER_RETURN' AND predicate='EXACT_SELLER_RETURN' AND return_lock_id IS NOT NULL));
CREATE UNIQUE INDEX dispatch_fact_duty_identity ON dispatch.recovery_obligations(
 operation_id,duty_kind,coalesce(economic_fact_id::text,refund_fact_id::text,return_lock_id::text)) WHERE origin_kind='FACT';
ALTER TABLE dispatch.duty_runtime ADD COLUMN discharge_reconciliation_run uuid REFERENCES reconciliation.runs(id);
ALTER TABLE dispatch.duty_runtime DROP CONSTRAINT duty_runtime_check;
ALTER TABLE dispatch.duty_runtime ADD CHECK(state<>'DISCHARGED' OR discharge_observation_id IS NOT NULL OR discharge_reconciliation_run IS NOT NULL);
CREATE TABLE reconciliation.duty_sources(
 duty_id text NOT NULL REFERENCES dispatch.recovery_obligations(id), resource_key text NOT NULL,
 economic_fact_id uuid REFERENCES economic.facts(id), refund_fact_id uuid REFERENCES returns.facts(id),
 return_lock_id uuid REFERENCES returns.locks(id), PRIMARY KEY(duty_id,resource_key),
 CHECK(num_nonnulls(economic_fact_id,refund_fact_id,return_lock_id)=1)
);

CREATE FUNCTION reconciliation.actor(token text) RETURNS authority.memberships
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,reconciliation,pg_temp AS $$
DECLARE m authority.memberships; BEGIN
 m:=authority.actor(token);
 IF NOT (ARRAY['PURCHASE_ISSUER','REFUND_ISSUER']::text[] && m.roles) THEN PERFORM authority.fail('RECONCILIATION_ADMIN_REQUIRED',403); END IF;
 RETURN m;
END $$;
CREATE FUNCTION reconciliation.start(token text,rid uuid,cursor text,lim int) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,reconciliation,pg_temp AS $$
DECLARE m authority.memberships; r reconciliation.runs; ids text[]; BEGIN
 m:=reconciliation.actor(token);
 IF lim NOT BETWEEN 1 AND 50 OR cursor IS NULL OR length(cursor)>150 THEN PERFORM authority.fail('BOUNDED_SCOPE_REQUIRED',422); END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('reconciliation-run:'||rid,0));
 SELECT * INTO r FROM reconciliation.runs WHERE id=rid;
 IF r.id IS NOT NULL THEN
 IF r.tenant_id<>m.tenant_id OR r.principal<>m.principal OR r.after_operation<>cursor OR r.batch_size<>lim THEN
 PERFORM authority.fail('RUN_IDENTITY_CONFLICT',403); END IF; RETURN rid; END IF;
 SELECT coalesce(array_agg(id ORDER BY id),'{}'::text[]) INTO ids FROM
 (SELECT id FROM dispatch.business_operations WHERE tenant_id=m.tenant_id AND id>cursor ORDER BY id LIMIT lim+1) s;
 INSERT INTO reconciliation.runs(id,tenant_id,principal,after_operation,batch_size,has_more,next_cursor)
 VALUES(rid,m.tenant_id,m.principal,cursor,lim,cardinality(ids)>lim,ids[least(cardinality(ids),lim)]);
 INSERT INTO reconciliation.members(run_id,operation_id) SELECT rid,unnest(ids[1:lim]);
 INSERT INTO reconciliation.operation_heads(operation_id) SELECT unnest(ids[1:lim]) ON CONFLICT DO NOTHING;
 RETURN rid;
END $$;
CREATE FUNCTION reconciliation.prepare(token text,rid uuid,opid text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,reconciliation,pg_temp AS $$
DECLARE m authority.memberships; o dispatch.business_operations; raw text; key text; BEGIN
 m:=reconciliation.actor(token);
 IF NOT EXISTS(SELECT 1 FROM reconciliation.runs r JOIN reconciliation.members s ON s.run_id=r.id
 WHERE r.id=rid AND r.tenant_id=m.tenant_id AND s.operation_id=opid) THEN PERFORM authority.fail('RUN_SCOPE_DENIED',403); END IF;
 SELECT * INTO o FROM dispatch.business_operations WHERE id=opid;
 raw:=dispatch.canonical(jsonb_build_object('kind','RECONCILIATION_READ','run_id',rid,'operation_id',opid,
 'tenant_id',o.tenant_id,'account_id',o.account_id,'integration',o.integration,'expires_at',floor(extract(epoch FROM clock_timestamp()))+120));
 SELECT secret INTO key FROM economic.channels WHERE id='seller-capability-key';
 IF key IS NULL THEN PERFORM authority.fail('SELLER_READ_KEY_MISSING',403); END IF;
 RETURN jsonb_build_object('raw',raw,'signature',encode(public.hmac(raw,key,'sha256'),'hex'));
END $$;
CREATE FUNCTION reconciliation.record_seller(token text,rid uuid,opid text,raw text,sig text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,reconciliation,pg_temp AS $$
DECLARE m authority.memberships; o dispatch.business_operations; v jsonb; key text; BEGIN
 m:=reconciliation.actor(token);
 PERFORM 1 FROM reconciliation.members s JOIN reconciliation.runs r ON r.id=s.run_id
 WHERE s.run_id=rid AND s.operation_id=opid AND r.tenant_id=m.tenant_id FOR UPDATE OF s;
 IF NOT FOUND THEN PERFORM authority.fail('RUN_SCOPE_DENIED',403); END IF;
 IF EXISTS(SELECT 1 FROM reconciliation.seller_reads WHERE run_id=rid AND operation_id=opid) THEN RETURN; END IF;
 SELECT * INTO o FROM dispatch.business_operations WHERE id=opid;
 SELECT secret INTO key FROM economic.channels WHERE id='seller-'||m.tenant_id AND active;
 IF key IS NULL OR sig IS DISTINCT FROM encode(public.hmac(raw,key,'sha256'),'hex') THEN PERFORM authority.fail('SELLER_READ_AUTHENTICATION_REQUIRED',403); END IF;
 v:=raw::jsonb;
 IF v->>'kind' IS DISTINCT FROM 'RECONCILIATION_SELLER_STATE' OR v->>'run_id' IS DISTINCT FROM rid::text
 OR v->>'operation_id' IS DISTINCT FROM opid OR v->>'tenant_id' IS DISTINCT FROM o.tenant_id
 OR v->>'account_id' IS DISTINCT FROM o.account_id OR v->>'integration' IS DISTINCT FROM o.integration
 OR (v->>'observed_at')::timestamptz < (SELECT started_at FROM reconciliation.runs WHERE id=rid)
 OR (v->>'observed_at')::timestamptz > clock_timestamp()+interval '5 seconds'
 OR jsonb_typeof(v->'state'->'active_entitlement_ids') IS DISTINCT FROM 'array' THEN PERFORM authority.fail('EXACT_SELLER_READ_REQUIRED',403); END IF;
 INSERT INTO reconciliation.seller_reads(run_id,operation_id,revision,observed_at,fingerprint,state)
 VALUES(rid,opid,(v->'state'->>'revision')::bigint,(v->>'observed_at')::timestamptz,
 encode(public.digest(dispatch.canonical(v->'state'),'sha256'),'hex'),v->'state');
END $$;

-- One pinned, bounded local snapshot. Only explicit identities/states, never provider payloads.
CREATE FUNCTION reconciliation.local_state(opid text) RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,reconciliation,pg_temp AS $$
 SELECT jsonb_build_object('action_id',o.action_id,'evidence_id',o.evidence_id,'decision_id',o.decision_id,
 'approval_id',o.approval_id,'reservation_id',o.reservation_id,'principal',o.requester,'grant_id',o.grant_id,
 'grant_epoch',o.grant_epoch,'current_grant_epoch',h.epoch,'grant_state',h.status,'replay_class',rc.classification,
 'intended','CREATE_ORDER','knowledge',r.knowledge,'provider_object_id',r.provider_order,
 'provider_observation_id',r.latest_observation_id,
 'exposure', (SELECT jsonb_build_object('id',d.exposure_id,'duty_id',d.id) FROM dispatch.recovery_obligations d
 JOIN dispatch.duty_runtime dr ON dr.obligation_id=d.id WHERE d.operation_id=o.id AND d.origin_kind='EXPOSURE'
 AND dr.state<>'DISCHARGED' LIMIT 1),
 'facts',coalesce((SELECT jsonb_agg(jsonb_build_object('id',f.id,'kind',f.kind,'observation_id',f.observation_id,
 'effect_id',f.effect_id,'amount',f.amount,'posting_id',p.id) ORDER BY f.id)
 FROM economic.facts f LEFT JOIN economic.postings p ON p.fact_id=f.id WHERE f.operation_id=o.id),'[]'::jsonb),
 'refunds',coalesce((SELECT jsonb_agg(s.v ORDER BY s.id) FROM (SELECT ro.id,jsonb_build_object('id',ro.id,
 'obligation_id',b.id,'lock_id',b.lock_id,'capture_fact_id',b.capture_fact_id,'reservation_id',b.reservation_id,
 'state',rr.state,'blocked',rr.blocked,'refund_id',rr.refund_id,'replay_class',ro.replay_class,
 'duty',EXISTS(SELECT 1 FROM returns.recovery_duties rd WHERE rd.operation_id=ro.id)) v
 FROM returns.operations ro JOIN returns.obligations b ON b.id=ro.obligation_id JOIN returns.runtime rr ON rr.operation_id=ro.id
 WHERE b.purchase_operation=o.id ORDER BY ro.id LIMIT 101) s),'[]'::jsonb),
 'refund_facts',coalesce((SELECT jsonb_agg(s.v ORDER BY s.id) FROM (SELECT f.id,jsonb_build_object('id',f.id,
 'kind',f.kind,'operation_id',f.operation_id,'observation_id',f.observation_id,'refund_id',f.refund_id,
 'posting_id',p.id,'lock_id',b.lock_id) v FROM returns.facts f JOIN returns.obligations b ON b.id=f.obligation_id
 LEFT JOIN returns.postings p ON p.fact_id=f.id WHERE b.purchase_operation=o.id ORDER BY f.id LIMIT 101) s),'[]'::jsonb),
 'provider_refunds',coalesce((SELECT jsonb_agg(s.v ORDER BY s.refund_id) FROM (SELECT DISTINCT ON (ob.refund_id)
 ob.refund_id,jsonb_build_object('id',ob.id,'refund_id',ob.refund_id,'operation_id',ob.operation_id,
 'canonical',ch.provider<>'RAZORPAY_TEST' OR ch.source='CANONICAL_READ',
 'fact_id',(SELECT f.id FROM returns.facts f JOIN returns.obligations b ON b.id=f.obligation_id
 WHERE f.refund_id=ob.refund_id AND f.kind='REFUND_PROCESSED' AND b.purchase_operation=o.id)) v
 FROM returns.observations ob JOIN economic.receipts re ON re.id=ob.receipt_id JOIN economic.channels ch ON ch.id=re.channel_id
 JOIN economic.receipt_states rs ON rs.receipt_id=re.id AND rs.authentication='AUTHENTICATED'
 JOIN economic.facts cap ON cap.operation_id=o.id AND cap.kind='CAPTURE' AND cap.provider=ob.provider
 AND cap.account_id=ob.account_id AND cap.integration=ob.integration AND cap.effect_id=ob.payment_id
 WHERE ob.status='PROCESSED' ORDER BY ob.refund_id,ob.observed_at DESC,ob.id LIMIT 101) s),'[]'::jsonb),
 'duty_id',(SELECT d.id FROM dispatch.recovery_obligations d JOIN dispatch.duty_runtime dr ON dr.obligation_id=d.id
 WHERE d.operation_id=o.id AND d.duty_kind='RESOLVE_EXTERNAL_EFFECT' AND dr.state<>'DISCHARGED' ORDER BY d.id LIMIT 1))
 FROM dispatch.business_operations o JOIN dispatch.operation_runtime r ON r.operation_id=o.id
 JOIN authority.grant_heads h ON h.grant_id=o.grant_id JOIN dispatch.replay_contracts rc ON rc.id=o.replay_contract_id WHERE o.id=opid
$$;

CREATE FUNCTION reconciliation.duty(opid text,kind text,ef uuid,rf uuid,lid uuid) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,reconciliation,pg_temp AS $$
DECLARE o dispatch.business_operations; did text; BEGIN
 SELECT * INTO o FROM dispatch.business_operations WHERE id=opid;
 did:='reconciliation_duty_'||md5(opid||':'||kind);
 INSERT INTO dispatch.recovery_obligations(id,tenant_id,origin_kind,operation_id,reservation_id,duty_kind,predicate,
 read_role,mutation_requires_current_authority,replay_contract_id,economic_fact_id,refund_fact_id,return_lock_id)
 VALUES(did,o.tenant_id,'FACT',opid,o.reservation_id,kind,
 CASE WHEN kind='POST_VERIFIED_FACT' THEN 'EXACT_FACT_POSTING' ELSE 'EXACT_SELLER_RETURN' END,
 'OBSERVE',true,o.replay_contract_id,ef,rf,lid) ON CONFLICT DO NOTHING;
 INSERT INTO dispatch.duty_runtime(obligation_id,tenant_id,state) VALUES(did,o.tenant_id,'OPEN') ON CONFLICT DO NOTHING;
 INSERT INTO reconciliation.duty_sources VALUES(did,coalesce(ef::text,rf::text,lid::text),ef,rf,lid) ON CONFLICT DO NOTHING;
 UPDATE dispatch.duty_runtime SET state='OPEN',discharge_observation_id=NULL,discharge_reconciliation_run=NULL
 WHERE obligation_id=did AND state='DISCHARGED';
 RETURN did;
END $$;
CREATE FUNCTION reconciliation.refund_evidence(rop uuid) RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,reconciliation,pg_temp AS $$
 SELECT jsonb_build_object('intended','REFUND_CAPTURED_PRINCIPAL','action_id',b.action_id,'evidence_id',b.evidence_id,
 'decision_id',b.decision_id,'approval_id',b.approval_id,'reservation_id',b.reservation_id,'principal',a.requester,
 'grant_id',a.grant_id,'grant_epoch',rs.epoch,'current_grant_epoch',h.epoch,'grant_state',h.status,
 'refund_operation_id',o.id,'commercial_obligation_id',b.id,'return_lock_id',b.lock_id,'replay_class',o.replay_class,
 'booked',CASE WHEN NOT EXISTS(SELECT 1 FROM returns.facts f WHERE f.operation_id=o.id AND f.kind='REFUND_PROCESSED')
 THEN 'REFUND_NOT_BOOKED' WHEN EXISTS(SELECT 1 FROM returns.facts f JOIN returns.postings p ON p.fact_id=f.id
 WHERE f.operation_id=o.id AND f.kind='REFUND_PROCESSED') THEN 'REFUND_POSTED' ELSE 'REFUND_UNPOSTED' END,
 'observed',CASE rr.state WHEN 'PROCESSED' THEN 'CONFIRMED_EFFECT' WHEN 'UNKNOWN' THEN 'UNKNOWN'
 WHEN 'BLOCKED' THEN 'BLOCKED' ELSE 'NOT_ATTEMPTED' END)
 FROM returns.operations o JOIN returns.obligations b ON b.id=o.obligation_id
 JOIN authority.action_revisions a ON a.id=b.action_id JOIN authority.reservations rs ON rs.id=b.reservation_id
 JOIN authority.grant_heads h ON h.grant_id=a.grant_id JOIN returns.runtime rr ON rr.operation_id=o.id WHERE o.id=rop
$$;
CREATE FUNCTION reconciliation.remember(rid uuid,opid text,cls reconciliation.finding_class,key text,
 ev jsonb,step reconciliation.next_step,did text,rdid uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,reconciliation,pg_temp AS $$
DECLARE f reconciliation.findings; fid uuid; fp text; tid text; BEGIN
 fid:=md5(opid||':'||cls::text||':'||key)::uuid;
 fp:=encode(public.digest(dispatch.canonical(ev),'sha256'),'hex');
 SELECT tenant_id INTO tid FROM dispatch.business_operations WHERE id=opid;
 SELECT * INTO f FROM reconciliation.findings WHERE id=fid FOR UPDATE;
 IF f.id IS NOT NULL AND (f.operation_id<>opid OR f.class<>cls OR f.resource_key<>key) THEN
 PERFORM authority.fail('FINDING_IDENTITY_COLLISION',409); END IF;
 IF f.id IS NULL THEN
 INSERT INTO reconciliation.findings(id,tenant_id,operation_id,class,resource_key,state,revision,evidence_fingerprint,evidence,
 next_step,duty_id,refund_duty_id,first_seen_at,last_seen_at)
 VALUES(fid,tid,opid,cls,key,'OPEN',1,fp,ev,step,did,rdid,clock_timestamp(),clock_timestamp());
 ELSIF f.evidence_fingerprint<>fp OR f.state='RESOLVED' THEN
 UPDATE reconciliation.findings SET state='OPEN',revision=revision+1,evidence_fingerprint=fp,evidence=ev,next_step=step,
 duty_id=did,refund_duty_id=rdid,last_seen_at=clock_timestamp(),resolved_at=NULL,resolution_run=NULL WHERE id=fid;
 ELSE UPDATE reconciliation.findings SET last_seen_at=clock_timestamp() WHERE id=fid; END IF;
 INSERT INTO reconciliation.revisions(finding_id,revision,run_id,state,evidence_fingerprint,evidence)
 SELECT id,revision,rid,state,evidence_fingerprint,evidence FROM reconciliation.findings WHERE id=fid ON CONFLICT DO NOTHING;
 RETURN fid;
END $$;

CREATE FUNCTION reconciliation.evaluate(token text,rid uuid,opid text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,reconciliation,pg_temp AS $$
DECLARE m authority.memberships; member reconciliation.members; sr reconciliation.seller_reads; s jsonb; cap jsonb;
 base jsonb; x jsonb; ids uuid[]:='{}'; did text; old reconciliation.findings; stale boolean; resolution_ev jsonb;
BEGIN
 IF current_setting('transaction_isolation')<>'repeatable read' THEN PERFORM authority.fail('PINNED_SNAPSHOT_REQUIRED',409); END IF;
 m:=reconciliation.actor(token);
 PERFORM pg_advisory_xact_lock(hashtextextended('reconciliation-operation:'||opid,0));
 -- Every evaluation advances the same row: a waiter with an older RR snapshot
 -- must retry, including a clean scan which created no findings.
 UPDATE reconciliation.operation_heads SET revision=revision+1 WHERE operation_id=opid;
 SELECT sm.* INTO member FROM reconciliation.members sm JOIN reconciliation.runs r ON r.id=sm.run_id
 WHERE sm.run_id=rid AND sm.operation_id=opid AND r.tenant_id=m.tenant_id FOR UPDATE OF sm;
 IF member.run_id IS NULL THEN PERFORM authority.fail('RUN_SCOPE_DENIED',403); END IF;
 IF member.state<>'PENDING' THEN RETURN; END IF;
 SELECT * INTO sr FROM reconciliation.seller_reads WHERE run_id=rid AND operation_id=opid;
 SELECT EXISTS(SELECT 1 FROM reconciliation.seller_reads newer JOIN reconciliation.members sm
 ON sm.run_id=newer.run_id AND sm.operation_id=newer.operation_id WHERE newer.operation_id=opid AND sm.state='RECORDED'
 AND (newer.revision>sr.revision OR (newer.revision=sr.revision AND newer.observed_at>sr.observed_at AND newer.fingerprint<>sr.fingerprint))) INTO stale;
 s:=reconciliation.local_state(opid);
 IF sr.run_id IS NULL OR stale OR sr.observed_at<clock_timestamp()-interval '120 seconds'
 OR jsonb_array_length(s->'refunds')>100 OR jsonb_array_length(s->'refund_facts')>100 OR jsonb_array_length(s->'provider_refunds')>100 THEN
 UPDATE reconciliation.members SET state='INCONCLUSIVE' WHERE run_id=rid AND operation_id=opid; RETURN; END IF;
 SELECT value INTO cap FROM jsonb_array_elements(s->'facts') WHERE value->>'kind'='CAPTURE';
 base:=jsonb_build_object('action_id',s->'action_id','evidence_id',s->'evidence_id','decision_id',s->'decision_id',
 'approval_id',s->'approval_id','reservation_id',s->'reservation_id','principal',s->'principal','grant_id',s->'grant_id',
 'grant_epoch',s->'grant_epoch','current_grant_epoch',s->'current_grant_epoch','grant_state',s->'grant_state',
 'intended',s->'intended','observed',s->'knowledge','booked',CASE WHEN cap IS NULL THEN 'NO_MATCHED_CAPTURE'
 WHEN cap->>'posting_id' IS NULL THEN 'CAPTURE_UNPOSTED' ELSE 'CAPTURE_POSTED' END,
 'fulfilled',CASE WHEN NOT (sr.state->>'fulfilled')::boolean THEN 'UNFULFILLED' WHEN (sr.state->>'closed')::boolean THEN 'RETURN_COMPLETED'
 WHEN sr.state->>'lock_id' IS NOT NULL THEN 'RETURN_LOCKED' ELSE 'FULFILLED' END,
 'seller_order_id',sr.state->'order_id','seller_revision',sr.revision,'seller_fingerprint',sr.fingerprint,
 'entitlement_ids',sr.state->'active_entitlement_ids','return_lock_id',sr.state->'lock_id',
 'provider_object_id',s->'provider_object_id','provider_observation_id',s->'provider_observation_id',
 'fact_id',cap->'id','posting_id',cap->'posting_id','replay_class',s->'replay_class',
 'executable',false,'authority_reason','CURRENT_BROKER_AUTHORIZATION_REQUIRED');
 IF cap IS NOT NULL AND NOT (sr.state->>'fulfilled')::boolean THEN
 ids:=array_append(ids,reconciliation.remember(rid,opid,'CAPTURED_BUT_UNFULFILLED',cap->>'id',
 base||jsonb_build_object('provider_observation_id',cap->'observation_id','provider_object_id',cap->'effect_id'),
 'VERIFY_SELLER_ENTITLEMENT_STATE',s->>'duty_id',NULL)); END IF;
 IF (sr.state->>'fulfilled')::boolean AND (cap IS NULL OR sr.state->>'capture_fact_id' IS DISTINCT FROM cap->>'id'
 OR sr.state->>'payment_id' IS DISTINCT FROM cap->>'effect_id') THEN
 ids:=array_append(ids,reconciliation.remember(rid,opid,'FULFILLED_WITHOUT_MATCHED_CAPTURE',sr.state->>'order_id',
 base,'READ_PROVIDER_STATE',s->>'duty_id',NULL)); END IF;
 FOR x IN SELECT value FROM jsonb_array_elements(s->'provider_refunds') WHERE (value->>'canonical')::boolean AND value->>'fact_id' IS NULL LOOP
 ids:=array_append(ids,reconciliation.remember(rid,opid,'PROVIDER_REFUND_MISSING_LOCALLY',x->>'refund_id',
 base||coalesce(reconciliation.refund_evidence((x->>'operation_id')::uuid),'{}'::jsonb)||jsonb_build_object('provider_observation_id',x->'id','provider_object_id',x->'refund_id','refund_operation_id',x->'operation_id'),
 CASE WHEN x->>'operation_id' IS NULL THEN 'ESCALATE_FOR_MANUAL_REVIEW'::reconciliation.next_step ELSE 'MATERIALIZE_MISSING_ECONOMIC_FACT'::reconciliation.next_step END,
 NULL,NULL)); END LOOP;
 FOR x IN SELECT value FROM jsonb_array_elements(s->'refund_facts') LOOP
 IF x->>'kind'='REFUND_PROCESSED' AND jsonb_array_length(sr.state->'active_entitlement_ids')>0 THEN
 did:=reconciliation.duty(opid,'COMPLETE_SELLER_RETURN',NULL,NULL,(x->>'lock_id')::uuid);
 ids:=array_append(ids,reconciliation.remember(rid,opid,'REFUND_PROCESSED_ENTITLEMENT_ACTIVE',x->>'refund_id',
 base||reconciliation.refund_evidence((x->>'operation_id')::uuid)||jsonb_build_object('fact_id',x->'id','posting_id',x->'posting_id','provider_observation_id',x->'observation_id',
 'provider_object_id',x->'refund_id','refund_operation_id',x->'operation_id'), 'VERIFY_SELLER_ENTITLEMENT_STATE',did,NULL)); END IF;
 IF x->>'posting_id' IS NULL THEN
 did:=reconciliation.duty(opid,'POST_VERIFIED_FACT',NULL,(x->>'id')::uuid,NULL);
 ids:=array_append(ids,reconciliation.remember(rid,opid,'ECONOMIC_FACT_UNPOSTED','refund:'||(x->>'id'),
 base||reconciliation.refund_evidence((x->>'operation_id')::uuid)||jsonb_build_object('fact_id',x->'id','posting_id',NULL,'provider_observation_id',x->'observation_id',
 'refund_operation_id',x->'operation_id'), 'POST_ALREADY_AUTHORIZED_ECONOMIC_FACT',did,NULL)); END IF;
 END LOOP;
 FOR x IN SELECT value FROM jsonb_array_elements(s->'facts') WHERE value->>'kind'<>'SELLER_FULFILLED' AND value->>'posting_id' IS NULL LOOP
 did:=reconciliation.duty(opid,'POST_VERIFIED_FACT',(x->>'id')::uuid,NULL,NULL);
 ids:=array_append(ids,reconciliation.remember(rid,opid,'ECONOMIC_FACT_UNPOSTED','economic:'||(x->>'id'),
 base||jsonb_build_object('fact_id',x->'id','posting_id',NULL,'provider_observation_id',x->'observation_id'),
 'POST_ALREADY_AUTHORIZED_ECONOMIC_FACT',did,NULL)); END LOOP;
 IF s->>'knowledge' IN ('UNKNOWN','KNOWN_PENDING') THEN
 ids:=array_append(ids,reconciliation.remember(rid,opid,'MUTATION_OUTCOME_UNKNOWN',opid,base,
 'RECHECK_MUTATION_OUTCOME',s->>'duty_id',NULL)); END IF;
 IF cap IS NULL AND s->'exposure' IS DISTINCT FROM 'null'::jsonb THEN
 ids:=array_append(ids,reconciliation.remember(rid,opid,'MUTATION_OUTCOME_UNKNOWN',s->'exposure'->>'id',
 base||jsonb_build_object('intended','CHECKOUT_EXPOSURE','observed','UNKNOWN','exposure_id',s->'exposure'->'id'),
 'RECHECK_MUTATION_OUTCOME',s->'exposure'->>'duty_id',NULL)); END IF;
 FOR x IN SELECT value FROM jsonb_array_elements(s->'refunds') WHERE value->>'state'='UNKNOWN' LOOP
 ids:=array_append(ids,reconciliation.remember(rid,opid,'MUTATION_OUTCOME_UNKNOWN',x->>'id',
 base||reconciliation.refund_evidence((x->>'id')::uuid)||jsonb_build_object('refund_operation_id',x->'id','commercial_obligation_id',x->'obligation_id',
 'reservation_id',x->'reservation_id','return_lock_id',x->'lock_id','replay_class',x->'replay_class','observed','UNKNOWN'),
 'RECHECK_MUTATION_OUTCOME',NULL,CASE WHEN (x->>'duty')::boolean THEN (x->>'id')::uuid ELSE NULL END)); END LOOP;
 FOR old IN SELECT * FROM reconciliation.findings WHERE operation_id=opid AND state='OPEN' AND NOT id=ANY(ids) FOR UPDATE LOOP
 resolution_ev:=old.evidence||base||coalesce(reconciliation.refund_evidence((old.evidence->>'refund_operation_id')::uuid),'{}'::jsonb)
 ||jsonb_build_object('condition_present',false);
 IF old.class IN ('ECONOMIC_FACT_UNPOSTED','REFUND_PROCESSED_ENTITLEMENT_ACTIVE') THEN
 resolution_ev:=resolution_ev||jsonb_build_object('fact_id',old.evidence->'fact_id','provider_observation_id',old.evidence->'provider_observation_id',
 'provider_object_id',old.evidence->'provider_object_id','posting_id',(SELECT id FROM (
 SELECT p.id FROM economic.postings p WHERE p.fact_id=(old.evidence->>'fact_id')::uuid UNION ALL
 SELECT p.id FROM returns.postings p WHERE p.fact_id=(old.evidence->>'fact_id')::uuid) posted LIMIT 1));
 END IF;
 UPDATE reconciliation.findings SET state='RESOLVED',revision=revision+1,evidence=resolution_ev,
 evidence_fingerprint=encode(public.digest(dispatch.canonical(resolution_ev),'sha256'),'hex'),last_seen_at=clock_timestamp(),
 resolved_at=clock_timestamp(),resolution_run=rid WHERE id=old.id;
 INSERT INTO reconciliation.revisions(finding_id,revision,run_id,state,evidence_fingerprint,evidence)
 SELECT id,revision,rid,state,evidence_fingerprint,evidence FROM reconciliation.findings WHERE id=old.id;
 END LOOP;
 -- Finding resolution and duty discharge are separate predicates. Emergency
 -- entitlement revocation alone cannot prove completion of the seller return.
 UPDATE dispatch.duty_runtime dr SET state='DISCHARGED',discharge_reconciliation_run=rid,updated_at=clock_timestamp()
 FROM dispatch.recovery_obligations d WHERE d.operation_id=opid AND dr.obligation_id=d.id AND d.origin_kind='FACT'
 AND dr.state<>'DISCHARGED' AND NOT EXISTS(SELECT 1 FROM reconciliation.findings f WHERE f.duty_id=d.id AND f.state='OPEN')
 AND ((d.duty_kind='POST_VERIFIED_FACT' AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s->'facts') elem
 WHERE elem->>'kind'<>'SELLER_FULFILLED' AND elem->>'posting_id' IS NULL) AND NOT EXISTS(
 SELECT 1 FROM jsonb_array_elements(s->'refund_facts') elem WHERE elem->>'posting_id' IS NULL))
 OR (d.duty_kind='COMPLETE_SELLER_RETURN' AND (sr.state->>'closed')::boolean AND NOT EXISTS(
 SELECT 1 FROM jsonb_array_elements(s->'refund_facts') elem WHERE elem->>'posting_id' IS NULL) AND NOT EXISTS(
 SELECT 1 FROM jsonb_array_elements(s->'provider_refunds') elem WHERE (elem->>'canonical')::boolean AND elem->>'fact_id' IS NULL)));
 INSERT INTO reconciliation.results VALUES(rid,opid,transaction_timestamp(),s,ids);
 UPDATE reconciliation.members SET state='RECORDED',evidence_count=cardinality(ids) WHERE run_id=rid AND operation_id=opid;
END $$;

CREATE FUNCTION reconciliation.finish(token text,rid uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,reconciliation,pg_temp AS $$
DECLARE m authority.memberships; BEGIN
 m:=reconciliation.actor(token);
 PERFORM 1 FROM reconciliation.runs WHERE id=rid AND tenant_id=m.tenant_id FOR UPDATE;
 IF NOT FOUND THEN PERFORM authority.fail('RUN_SCOPE_DENIED',403); END IF;
 IF EXISTS(SELECT 1 FROM reconciliation.members WHERE run_id=rid AND state='PENDING') THEN RETURN; END IF;
 UPDATE reconciliation.runs SET state=CASE WHEN has_more OR EXISTS(SELECT 1 FROM reconciliation.members WHERE run_id=rid AND state='INCONCLUSIVE')
 THEN 'INCOMPLETE' ELSE 'COMPLETE' END,completed_at=coalesce(completed_at,clock_timestamp()) WHERE id=rid;
END $$;

CREATE FUNCTION reconciliation.inspect(token text,kind text,resource uuid,cursor uuid,lim int) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,reconciliation,pg_temp AS $$
DECLARE m authority.memberships; result jsonb; BEGIN
 m:=reconciliation.actor(token);
 IF lim NOT BETWEEN 1 AND 50 THEN PERFORM authority.fail('BOUNDED_SCOPE_REQUIRED',422); END IF;
 IF kind='findings' THEN
 SELECT coalesce(jsonb_agg(jsonb_build_object('id',f.id,'operation_id',f.operation_id,'class',f.class,'resource_key',f.resource_key,
 'state',f.state,'revision',f.revision,'evidence_fingerprint',f.evidence_fingerprint,'evidence',f.evidence,
 'next_step',f.next_step,'duty_id',f.duty_id,'refund_duty_id',f.refund_duty_id,'first_seen_at',f.first_seen_at,
 'last_seen_at',f.last_seen_at,'resolved_at',f.resolved_at,'resolution_run',f.resolution_run)
 ORDER BY f.id),'[]'::jsonb) INTO result FROM (SELECT * FROM reconciliation.findings WHERE tenant_id=m.tenant_id
 AND (resource IS NULL OR id=resource) AND (cursor IS NULL OR id>cursor) ORDER BY id LIMIT lim) f;
 ELSIF kind='runs' THEN
 SELECT coalesce(jsonb_agg(jsonb_build_object('id',r.id,'state',r.state,'has_more',r.has_more,'next_cursor',r.next_cursor,
 'started_at',r.started_at,'completed_at',r.completed_at,
 'members',coalesce((SELECT jsonb_agg(jsonb_build_object('operation_id',s.operation_id,'state',s.state,'evidence_count',s.evidence_count)
 ORDER BY s.operation_id) FROM reconciliation.members s WHERE s.run_id=r.id),'[]'::jsonb)) ORDER BY r.id),'[]'::jsonb)
 INTO result FROM (SELECT * FROM reconciliation.runs WHERE tenant_id=m.tenant_id AND (resource IS NULL OR id=resource)
 AND (cursor IS NULL OR id>cursor) ORDER BY id LIMIT lim) r;
 ELSE PERFORM authority.fail('INSPECTION_KIND_DENIED',403); END IF;
 RETURN result;
END $$;

CREATE FUNCTION reconciliation.history(token text,fid uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,reconciliation,pg_temp AS $$
DECLARE m authority.memberships; BEGIN
 m:=reconciliation.actor(token);
 IF NOT EXISTS(SELECT 1 FROM reconciliation.findings WHERE id=fid AND tenant_id=m.tenant_id) THEN PERFORM authority.fail('FINDING_SCOPE_DENIED',403); END IF;
 RETURN coalesce((SELECT jsonb_agg(jsonb_build_object('revision',s.revision,'run_id',s.run_id,'state',s.state,
 'evidence_fingerprint',s.evidence_fingerprint,'evidence',s.evidence,'observed_at',s.observed_at) ORDER BY s.revision)
 FROM (SELECT * FROM reconciliation.revisions WHERE finding_id=fid ORDER BY revision DESC LIMIT 50) s),'[]'::jsonb);
END $$;
CREATE FUNCTION reconciliation.guard_finding() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF OLD.id<>NEW.id OR OLD.tenant_id<>NEW.tenant_id OR OLD.operation_id<>NEW.operation_id OR OLD.class<>NEW.class
 OR OLD.resource_key<>NEW.resource_key OR OLD.first_seen_at<>NEW.first_seen_at OR NEW.revision<OLD.revision
 OR NEW.revision>OLD.revision+1 OR ((OLD.state<>NEW.state OR OLD.evidence IS DISTINCT FROM NEW.evidence) AND NEW.revision<>OLD.revision+1)
 OR NEW.last_seen_at<OLD.last_seen_at OR NEW.evidence_fingerprint IS DISTINCT FROM
 encode(public.digest(dispatch.canonical(NEW.evidence),'sha256'),'hex') THEN
 RAISE EXCEPTION 'immutable finding identity and revision sequence' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER finding_identity BEFORE UPDATE ON reconciliation.findings FOR EACH ROW EXECUTE FUNCTION reconciliation.guard_finding();
CREATE TRIGGER finding_history BEFORE DELETE ON reconciliation.findings FOR EACH ROW EXECUTE FUNCTION authority.immutable();
DO $$ DECLARE t text; BEGIN FOREACH t IN ARRAY ARRAY['seller_reads','revisions','results','duty_sources'] LOOP
 EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON reconciliation.%I FOR EACH ROW EXECUTE FUNCTION authority.immutable()',t);
END LOOP; END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA reconciliation FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA reconciliation FROM PUBLIC;
DO $$ DECLARE item record; BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='interlock_reconciliation_owner') THEN
 CREATE ROLE interlock_reconciliation_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; END IF;
 GRANT USAGE ON SCHEMA public,authority,dispatch,economic,returns TO interlock_reconciliation_owner;
 GRANT SELECT ON ALL TABLES IN SCHEMA authority,dispatch,economic,returns TO interlock_reconciliation_owner;
 GRANT REFERENCES ON public.tenants,authority.memberships,dispatch.business_operations,dispatch.recovery_obligations,
 returns.recovery_duties TO interlock_reconciliation_owner;
 GRANT INSERT ON dispatch.recovery_obligations,dispatch.duty_runtime TO interlock_reconciliation_owner;
 GRANT UPDATE ON dispatch.duty_runtime TO interlock_reconciliation_owner;
 GRANT EXECUTE ON FUNCTION authority.actor(text),authority.fail(text,int),dispatch.canonical(jsonb) TO interlock_reconciliation_owner;
 ALTER SCHEMA reconciliation OWNER TO interlock_reconciliation_owner;
 FOR item IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='reconciliation' AND c.relkind='r' LOOP
 EXECUTE format('ALTER TABLE reconciliation.%I OWNER TO interlock_reconciliation_owner',item.relname); END LOOP;
 FOR item IN SELECT p.oid::regprocedure AS name FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='reconciliation' LOOP
 EXECUTE format('ALTER FUNCTION %s OWNER TO interlock_reconciliation_owner',item.name); END LOOP;
 GRANT USAGE ON SCHEMA reconciliation TO interlock_api;
 GRANT EXECUTE ON FUNCTION reconciliation.start(text,uuid,text,int),reconciliation.prepare(text,uuid,text),
 reconciliation.record_seller(text,uuid,text,text,text),reconciliation.evaluate(text,uuid,text),reconciliation.finish(text,uuid),
 reconciliation.inspect(text,text,uuid,uuid,int),reconciliation.history(text,uuid) TO interlock_api;
END $$;
