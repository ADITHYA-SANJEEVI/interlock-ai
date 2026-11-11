CREATE SCHEMA ref_returns;
REVOKE ALL ON SCHEMA ref_returns FROM PUBLIC;
CREATE TABLE ref_returns.schedule(
 operation_id text PRIMARY KEY, fault text NOT NULL CHECK(fault IN ('NORMAL','LOST_RESPONSE','DELAYED_READ','STALE_READ','OUTAGE','CONTRADICTORY','REJECTED','INCOMPLETE')),
 delay bigint NOT NULL DEFAULT 0 CHECK(delay>=0)
);
CREATE TABLE ref_returns.refunds(
 operation_id text PRIMARY KEY, payment_id text NOT NULL, refund_id text NOT NULL UNIQUE,
 body jsonb NOT NULL, envelope jsonb NOT NULL, visible_at bigint NOT NULL, status text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE ref_returns.calls(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), operation_id text NOT NULL, method text NOT NULL,
 tick bigint NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION ref_returns.envelope(body jsonb,status text,rid text) RETURNS jsonb LANGUAGE plpgsql
SET search_path=pg_catalog,ref_returns,pg_temp AS $$
DECLARE k ref_economic.keys; v jsonb; raw text; BEGIN
 SELECT * INTO k FROM ref_economic.keys WHERE account_id=body->'notes'->>'interlock_account';
 v:=jsonb_build_object('account_id',k.account_id,'integration',k.integration,'tenant_id',k.tenant_id,
 'event','refund.'||status,'provenance','REFERENCE_SYNTHETIC','payload',jsonb_build_object('refund',jsonb_build_object('entity',
 jsonb_build_object('id',rid,'payment_id',body->>'payment_id','amount',body->'amount','currency',body->>'currency','status',status,'notes',body->'notes'))));
 raw:=v::text;
 RETURN jsonb_build_object('raw',raw,'signature',encode(public.hmac(raw,k.secret,'sha256'),'hex'),
 'delivery_id',rid||':'||status,'provenance','REFERENCE_SYNTHETIC');
END $$;
CREATE FUNCTION ref_returns.mutate(body jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,ref_returns,pg_temp AS $$
DECLARE op text; p ref_economic.captures; r ref_returns.refunds; s ref_returns.schedule; tick bigint; rid text; env jsonb;
BEGIN
 IF session_user<>'interlock_reference' THEN RAISE EXCEPTION 'reference role required' USING ERRCODE='42501'; END IF;
 op:=body->'notes'->>'interlock_operation';
 IF op IS NULL OR body->'notes'->>'interlock_obligation' IS NULL OR (body->>'amount')::bigint<=0 OR body->>'currency'<>'INR'
 OR EXISTS(SELECT 1 FROM jsonb_object_keys(body) k WHERE k NOT IN ('payment_id','amount','currency','notes')) THEN RAISE EXCEPTION 'typed refund required'; END IF;
 SELECT * INTO p FROM ref_economic.captures WHERE payment_id=body->>'payment_id' FOR UPDATE;
 IF p.payment_id IS NULL OR p.raw::jsonb->>'account_id' IS DISTINCT FROM body->'notes'->>'interlock_account'
 OR p.raw::jsonb->>'integration' IS DISTINCT FROM body->'notes'->>'interlock_integration' THEN RAISE EXCEPTION 'exact captured payment required'; END IF;
 UPDATE ref_dispatch.clock SET tick=ref_dispatch.clock.tick+1 RETURNING ref_dispatch.clock.tick INTO tick;
 INSERT INTO ref_returns.calls(operation_id,method,tick) VALUES(op,'POST',tick);
 SELECT * INTO r FROM ref_returns.refunds WHERE operation_id=op;
 IF r.operation_id IS NOT NULL THEN IF r.body<>body THEN RAISE EXCEPTION 'refund key substitution'; END IF; RETURN r.envelope; END IF;
 IF (SELECT coalesce(sum((rf.body->>'amount')::bigint),0) FROM ref_returns.refunds rf WHERE rf.payment_id=p.payment_id AND rf.status='processed')+(body->>'amount')::bigint>
 (p.raw::jsonb->'payload'->'payment'->'entity'->>'amount')::bigint THEN RAISE EXCEPTION 'provider principal exceeded'; END IF;
 SELECT * INTO s FROM ref_returns.schedule WHERE operation_id=op;
 IF s.fault='OUTAGE' THEN RETURN NULL; END IF;
 rid:='rfnd_'||substr(encode(public.digest(op,'sha256'),'hex'),1,24);
 env:=ref_returns.envelope(body,CASE WHEN s.fault='REJECTED' THEN 'failed' ELSE 'processed' END,rid);
 INSERT INTO ref_returns.refunds VALUES(op,p.payment_id,rid,body,env,tick+coalesce(s.delay,0),CASE WHEN s.fault='REJECTED' THEN 'failed' ELSE 'processed' END,clock_timestamp());
 IF s.fault IN ('LOST_RESPONSE','INCOMPLETE') THEN RETURN NULL; END IF;
 RETURN env;
END $$;
CREATE FUNCTION ref_returns.read_refund(op text,account text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,ref_returns,pg_temp AS $$
DECLARE r ref_returns.refunds; s ref_returns.schedule; tick bigint; BEGIN
 IF session_user<>'interlock_reference' THEN RAISE EXCEPTION 'reference role required' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM ref_returns.refunds WHERE operation_id=op AND body->'notes'->>'interlock_account'=account;
 UPDATE ref_dispatch.clock SET tick=ref_dispatch.clock.tick+1 RETURNING ref_dispatch.clock.tick INTO tick;
 INSERT INTO ref_returns.calls(operation_id,method,tick) VALUES(op,'GET',tick);
 SELECT * INTO s FROM ref_returns.schedule WHERE operation_id=op;
 IF r.operation_id IS NULL OR tick<r.visible_at OR s.fault IN ('OUTAGE','INCOMPLETE') THEN RETURN NULL; END IF;
 IF s.fault='STALE_READ' THEN RETURN ref_returns.envelope(r.body,'pending',r.refund_id); END IF;
 IF s.fault='CONTRADICTORY' THEN RETURN ref_returns.envelope(r.body,'failed',r.refund_id); END IF;
 RETURN r.envelope;
END $$;
DO $$ DECLARE item record; BEGIN
 ALTER SCHEMA ref_returns OWNER TO interlock_reference_economic_owner;
 GRANT UPDATE(payment_id) ON ref_economic.captures TO interlock_reference_economic_owner;
 FOR item IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='ref_returns' AND c.relkind='r' LOOP
 EXECUTE format('ALTER TABLE ref_returns.%I OWNER TO interlock_reference_economic_owner',item.relname); END LOOP;
 FOR item IN SELECT p.oid::regprocedure AS name FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='ref_returns' LOOP
 EXECUTE format('ALTER FUNCTION %s OWNER TO interlock_reference_economic_owner',item.name); END LOOP;
 REVOKE ALL ON ALL TABLES IN SCHEMA ref_returns FROM PUBLIC;
 REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ref_returns FROM PUBLIC;
 GRANT USAGE ON SCHEMA ref_returns TO interlock_reference;
 GRANT EXECUTE ON FUNCTION ref_returns.mutate(jsonb),ref_returns.read_refund(text,text) TO interlock_reference;
END $$;
