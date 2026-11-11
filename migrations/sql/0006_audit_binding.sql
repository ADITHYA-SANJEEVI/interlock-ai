CREATE OR REPLACE FUNCTION dispatch.record_observation(secret text,opid text,result jsonb,attemptid text DEFAULT NULL,transport_state text DEFAULT NULL) RETURNS jsonb
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
 AND result->>'coverage'='PINNED_REFERENCE_TERMINAL_REJECTION_V1' AND result->>'provenance'='REFERENCE_SYNTHETIC'
 AND result->>'receipt'=o.operation_key AND result->>'request_digest'=o.request_digest AND result->>'account_id'=o.account_id THEN classification:='EXACT_ZERO_EFFECT';
 END IF;
 END IF;
 IF classification='EXACT_ZERO_EFFECT' AND rt.provider_order IS NOT NULL OR classification='EXACT_EFFECT' AND rt.knowledge='CONFIRMED_ZERO_EFFECT' THEN classification:='CONFLICT'; END IF;
 -- Persist only typed safe observation fields; authentication/payment credentials never enter this export.
 result:=jsonb_build_object('id',remote->>'id','receipt',coalesce(remote->>'receipt',result->>'receipt'),'amount',remote->'amount','currency',remote->>'currency',
 'status',remote->>'status','notes',jsonb_build_object('interlock_operation',remote->'notes'->>'interlock_operation',
 'interlock_account',remote->'notes'->>'interlock_account','interlock_integration',remote->'notes'->>'interlock_integration'),
 'provenance',o.adapter,'terminal_zero',result->'terminal_zero',
 'request_digest',result->>'request_digest','coverage',result->>'coverage','account_id',coalesce(remote->'notes'->>'interlock_account',result->>'account_id'),'transport_state',transport_state);
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
 ELSE UPDATE dispatch.duty_runtime SET state=CASE WHEN classification='CONFLICT' THEN 'VERIFYING' ELSE 'OBSERVING' END,
 discharge_observation_id=CASE WHEN classification='CONFLICT' THEN NULL ELSE discharge_observation_id END,blocked_reason=CASE WHEN classification='CONFLICT' THEN 'EXACT_READ_CONFLICT' ELSE NULL END,updated_at=clock_timestamp()
 WHERE obligation_id IN (SELECT id FROM dispatch.recovery_obligations WHERE operation_id=opid AND origin_kind='OPERATION') AND (state<>'DISCHARGED' OR classification='CONFLICT'); END IF;
 UPDATE dispatch.outbox_items SET ready_at=clock_timestamp()+interval '10 seconds',lease_until=NULL,
 completed=classification IN ('EXACT_EFFECT','EXACT_ZERO_EFFECT') WHERE operation_id=opid AND purpose='OBSERVE';
 INSERT INTO dispatch.audit_events VALUES('audit_'||gen_random_uuid()::text,o.tenant_id,o.id,'OBSERVATION_'||classification,obsid,clock_timestamp());
 RETURN jsonb_build_object('operation_id',opid,'knowledge',knowledge_value,'state',state_value,'observation_id',obsid,'classification',classification);
END $$;

CREATE OR REPLACE FUNCTION dispatch.bundle_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,dispatch,pg_temp AS $$
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
 IF EXISTS(SELECT 1 FROM dispatch.dispatch_records d WHERE operation_id=opid AND NOT EXISTS(SELECT 1 FROM dispatch.audit_events a WHERE a.operation_id=opid AND a.event='HANDOFF_COMMITTED' AND a.reference_id=d.id)) THEN PERFORM dispatch.fail('MISSING_DISPATCH_AUDIT'); END IF;
 IF EXISTS(SELECT 1 FROM dispatch.checkout_exposures e WHERE operation_id=opid AND NOT EXISTS(SELECT 1 FROM dispatch.audit_events a WHERE a.operation_id=opid AND a.event='CHECKOUT_EXPOSURE_COMMITTED' AND a.reference_id=e.id)) THEN PERFORM dispatch.fail('MISSING_EXPOSURE_AUDIT'); END IF;
 RETURN NULL;
END $$;
