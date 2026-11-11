CREATE SCHEMA ref_economic;
REVOKE ALL ON SCHEMA ref_economic FROM PUBLIC;
CREATE TABLE ref_economic.keys(account_id text PRIMARY KEY, tenant_id text NOT NULL, integration text NOT NULL, secret text NOT NULL);
CREATE TABLE ref_economic.captures(
 order_id text PRIMARY KEY, payment_id text NOT NULL UNIQUE, raw text NOT NULL, signature text NOT NULL,
 tick bigint NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE ref_economic.delivery_schedule(
 order_id text PRIMARY KEY, kinds text[] NOT NULL DEFAULT ARRAY['CAPTURED'],
 CHECK(kinds<@ARRAY['AUTHORIZED','CAPTURED','FAILED']::text[])
);
CREATE FUNCTION ref_economic.capture(orderid text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,ref_economic,pg_temp AS $$
DECLARE e ref_dispatch.effects; c ref_economic.captures; k ref_economic.keys; v jsonb; raw text; sig text; tick bigint; pid text; BEGIN
 IF session_user<>'interlock_reference' THEN RAISE EXCEPTION 'reference role denied' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('capture:'||orderid,0));
 SELECT * INTO c FROM ref_economic.captures WHERE order_id=orderid;
 IF c.order_id IS NOT NULL THEN RETURN jsonb_build_object('raw',c.raw,'signature',c.signature,'delivery_id',c.payment_id||':captured','provenance','REFERENCE_SYNTHETIC'); END IF;
 SELECT * INTO e FROM ref_dispatch.effects WHERE resource_id=orderid;
 IF e.receipt IS NULL OR e.terminal_zero THEN RAISE EXCEPTION 'known synthetic order required'; END IF;
 SELECT * INTO k FROM ref_economic.keys WHERE account_id=e.account_id;
 IF k.account_id IS NULL OR k.integration IS DISTINCT FROM e.body->'notes'->>'interlock_integration' THEN RAISE EXCEPTION 'reference account not provisioned'; END IF;
 UPDATE ref_dispatch.clock SET tick=ref_dispatch.clock.tick+1 WHERE id=1 RETURNING ref_dispatch.clock.tick INTO tick;
 pid:='pay_'||substr(encode(public.digest(orderid,'sha256'),'hex'),1,24);
 v:=jsonb_build_object('account_id',k.account_id,'integration',k.integration,'tenant_id',k.tenant_id,
 'operation_id',substr(e.receipt,3),'provenance','REFERENCE_SYNTHETIC','virtual_tick',tick,'event','payment.captured',
 'payload',jsonb_build_object('payment',jsonb_build_object('entity',jsonb_build_object('id',pid,'order_id',orderid,
 'amount',e.body->'amount','currency',e.body->>'currency','status','captured','captured',true))));
 raw:=v::text; sig:=encode(public.hmac(raw,k.secret,'sha256'),'hex');
 INSERT INTO ref_economic.captures VALUES(orderid,pid,raw,sig,tick,clock_timestamp());
 RETURN jsonb_build_object('raw',raw,'signature',sig,'delivery_id',pid||':captured','provenance','REFERENCE_SYNTHETIC');
END $$;
CREATE FUNCTION ref_economic.deliver(orderid text,ordinal int) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,ref_economic,pg_temp AS $$
DECLARE c ref_economic.captures; k ref_economic.keys; kind text; v jsonb; raw text; BEGIN
 IF session_user<>'interlock_reference' THEN RAISE EXCEPTION 'reference role denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO c FROM ref_economic.captures WHERE order_id=orderid;
 SELECT kinds[ordinal] INTO kind FROM ref_economic.delivery_schedule WHERE order_id=orderid;
 IF c.order_id IS NULL OR kind IS NULL THEN RAISE EXCEPTION 'synthetic delivery missing'; END IF;
 v:=c.raw::jsonb; SELECT * INTO k FROM ref_economic.keys WHERE account_id=v->>'account_id';
 v:=jsonb_set(v,'{payload,payment,entity,status}',to_jsonb(lower(kind)));
 v:=jsonb_set(v,'{payload,payment,entity,captured}',to_jsonb(kind='CAPTURED'));
 raw:=v::text;
 RETURN jsonb_build_object('raw',raw,'signature',encode(public.hmac(raw,k.secret,'sha256'),'hex'),
 'delivery_id',c.payment_id||':'||ordinal::text,'provenance','REFERENCE_SYNTHETIC');
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA ref_economic FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ref_economic FROM PUBLIC;
DO $$ DECLARE item record; BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='interlock_reference_economic_owner') THEN CREATE ROLE interlock_reference_economic_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; END IF;
 GRANT USAGE ON SCHEMA public,ref_dispatch TO interlock_reference_economic_owner;
 GRANT SELECT ON ref_dispatch.effects,ref_dispatch.clock TO interlock_reference_economic_owner;
 GRANT UPDATE(tick) ON ref_dispatch.clock TO interlock_reference_economic_owner;
 ALTER SCHEMA ref_economic OWNER TO interlock_reference_economic_owner;
 FOR item IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='ref_economic' AND c.relkind='r' LOOP
 EXECUTE format('ALTER TABLE ref_economic.%I OWNER TO interlock_reference_economic_owner',item.relname); END LOOP;
 FOR item IN SELECT p.oid::regprocedure AS name FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='ref_economic' LOOP
 EXECUTE format('ALTER FUNCTION %s OWNER TO interlock_reference_economic_owner',item.name); END LOOP;
 GRANT USAGE ON SCHEMA ref_economic TO interlock_reference;
 GRANT EXECUTE ON FUNCTION ref_economic.capture(text),ref_economic.deliver(text,int) TO interlock_reference;
END $$;
