CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA ref_dispatch;
REVOKE ALL ON SCHEMA ref_dispatch FROM PUBLIC;
CREATE TABLE ref_dispatch.clock(id int PRIMARY KEY REFERENCES public.clock(id),tick bigint NOT NULL DEFAULT 0);
CREATE TABLE ref_dispatch.fault_schedule(
 receipt text PRIMARY KEY, clock_id int NOT NULL DEFAULT 1 REFERENCES public.clock(id),
 fault text NOT NULL CHECK(fault IN ('NONE','ACCEPT_DROP_RESPONSE','REJECT_TERMINAL_ZERO','ACCEPT_INTERRUPT')),
 visibility_delay_ticks int NOT NULL DEFAULT 0 CHECK(visibility_delay_ticks>=0)
);
CREATE TABLE ref_dispatch.effects(
 receipt text PRIMARY KEY, clock_id int NOT NULL DEFAULT 1 REFERENCES public.clock(id),
 resource_id text NOT NULL UNIQUE, request_digest text NOT NULL, account_id text NOT NULL,
 body jsonb NOT NULL, terminal_zero boolean NOT NULL DEFAULT false, visible_at_tick bigint NOT NULL
);
CREATE TABLE ref_dispatch.transport_calls(
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, clock_id int NOT NULL DEFAULT 1 REFERENCES public.clock(id),
 receipt text NOT NULL, request_digest text NOT NULL, method text NOT NULL CHECK(method='POST'),
 tick bigint NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION ref_dispatch.create_effect(body jsonb,request_digest text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,ref_dispatch,pg_temp AS $$
#variable_conflict use_variable
DECLARE now_tick bigint; previous ref_dispatch.effects; fault ref_dispatch.fault_schedule; remote jsonb; rid text;
BEGIN
 IF session_user<>'interlock_reference' THEN RAISE EXCEPTION 'reference role denied' USING ERRCODE='42501'; END IF;
 IF body->>'currency'<>'INR' OR (body->>'amount')::bigint<=0 OR body->>'partial_payment'<>'false'
 OR body->>'receipt' IS DISTINCT FROM body->'notes'->>'interlock_operation' THEN RAISE EXCEPTION 'Invalid exact reference request'; END IF;
 INSERT INTO ref_dispatch.clock VALUES(1,0) ON CONFLICT DO NOTHING;
 UPDATE ref_dispatch.clock SET tick=tick+1 WHERE id=1 RETURNING tick INTO now_tick;
 INSERT INTO ref_dispatch.transport_calls(receipt,request_digest,method,tick) VALUES(body->>'receipt',request_digest,'POST',now_tick);
 SELECT * INTO fault FROM ref_dispatch.fault_schedule WHERE receipt=body->>'receipt';
 SELECT * INTO previous FROM ref_dispatch.effects WHERE receipt=body->>'receipt';
 IF FOUND THEN
 IF previous.request_digest<>request_digest THEN RAISE EXCEPTION 'Reference same-key different-body conflict'; END IF;
 RETURN jsonb_build_object('response',previous.body,'delivery',coalesce(fault.fault,'NONE'));
 END IF;
 rid:='order_'||substr(encode(public.digest(body->>'receipt','sha256'),'hex'),1,24);
 remote:=body||jsonb_build_object('id',rid,'entity','order','status','created','amount_paid',0,'provenance','REFERENCE_SYNTHETIC');
 INSERT INTO ref_dispatch.effects VALUES(body->>'receipt',1,rid,request_digest,body->'notes'->>'interlock_account',remote,
 coalesce(fault.fault='REJECT_TERMINAL_ZERO',false),now_tick+coalesce(fault.visibility_delay_ticks,0));
 RETURN jsonb_build_object('response',remote,'delivery',coalesce(fault.fault,'NONE'));
END $$;
CREATE FUNCTION ref_dispatch.read_effect(receipt text,account text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,ref_dispatch,pg_temp AS $$
#variable_conflict use_variable
DECLARE e ref_dispatch.effects; now_tick bigint; BEGIN
 IF session_user<>'interlock_reference' THEN RAISE EXCEPTION 'reference role denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO e FROM ref_dispatch.effects x WHERE x.receipt=read_effect.receipt AND x.account_id=account;
 SELECT tick INTO now_tick FROM ref_dispatch.clock WHERE id=1;
 IF e.receipt IS NULL OR e.visible_at_tick>now_tick THEN RETURN jsonb_build_object('items','[]'::jsonb,'coverage','STALE_OR_ABSENT_NOT_ZERO_PROOF','provenance','REFERENCE_SYNTHETIC'); END IF;
 IF e.terminal_zero THEN RETURN jsonb_build_object('terminal_zero',true,'receipt',receipt,'request_digest',e.request_digest,
 'account_id',e.account_id,'coverage','PINNED_REFERENCE_TERMINAL_REJECTION_V1','provenance','REFERENCE_SYNTHETIC'); END IF;
 RETURN jsonb_build_object('items',jsonb_build_array(e.body),'count',1,'coverage','EXACT_RECEIPT_ACCOUNT_V1','provenance','REFERENCE_SYNTHETIC');
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA ref_dispatch FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ref_dispatch FROM PUBLIC;
DO $$ DECLARE item record; BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='interlock_reference_dispatch_owner') THEN CREATE ROLE interlock_reference_dispatch_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; END IF;
 GRANT USAGE ON SCHEMA public TO interlock_reference_dispatch_owner;
 GRANT SELECT,REFERENCES ON public.clock TO interlock_reference_dispatch_owner;
 ALTER SCHEMA ref_dispatch OWNER TO interlock_reference_dispatch_owner;
 FOR item IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='ref_dispatch' AND c.relkind='r' LOOP
 EXECUTE format('ALTER TABLE ref_dispatch.%I OWNER TO interlock_reference_dispatch_owner',item.relname); END LOOP;
 FOR item IN SELECT p.oid::regprocedure AS name FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='ref_dispatch' LOOP
 EXECUTE format('ALTER FUNCTION %s OWNER TO interlock_reference_dispatch_owner',item.name); END LOOP;
 GRANT USAGE ON SCHEMA ref_dispatch TO interlock_reference;
 GRANT EXECUTE ON FUNCTION ref_dispatch.create_effect(jsonb,text),ref_dispatch.read_effect(text,text) TO interlock_reference;
END $$;
