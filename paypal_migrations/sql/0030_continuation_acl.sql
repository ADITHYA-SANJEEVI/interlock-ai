ALTER SCHEMA paypal_v2 OWNER TO interlock_dispatch_owner;
DO $$ DECLARE item record; BEGIN
 FOR item IN SELECT c.oid::regclass AS name FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='paypal_v2' AND c.relkind='r' LOOP
  EXECUTE format('ALTER TABLE %s OWNER TO interlock_dispatch_owner',item.name);
 END LOOP;
 FOR item IN SELECT p.oid::regprocedure AS name FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='paypal_v2' LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO interlock_dispatch_owner',item.name);
 END LOOP;
END $$;
ALTER FUNCTION paypal_v2.verify_seller(text,text) OWNER TO interlock_authority_owner;
REVOKE ALL ON ALL TABLES IN SCHEMA paypal_v2 FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA paypal_v2 FROM PUBLIC;
GRANT USAGE ON SCHEMA paypal_v2 TO interlock_api,interlock_broker,interlock_authority_owner;
GRANT EXECUTE ON FUNCTION paypal_v2.verify_seller(text,text) TO interlock_dispatch_owner;
GRANT EXECUTE ON FUNCTION paypal_v2.seed(text,text,uuid,bigint),paypal_v2.register(text,text,uuid,text,text,bigint,text),
 paypal_v2.approve(text,text,uuid,text,timestamptz,bigint,text),paypal_v2.request_capture(text,text,uuid,bigint,text),
 paypal_v2.inspect(text,uuid),paypal_v2.revoke(text,text,uuid) TO interlock_api;
GRANT EXECUTE ON FUNCTION paypal_v2.commit_capture(text,text,uuid,text) TO interlock_broker;
