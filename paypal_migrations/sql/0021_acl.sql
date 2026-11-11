-- No runtime role receives direct reads/writes of credentials or financial tables.
CREATE FUNCTION paypal_v1.assert_enabled() RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM paypal_v1.deployment WHERE enabled AND disposable_acceptance_sha256 IS NOT NULL) THEN
  PERFORM authority.fail('PAYPAL_DISPOSABLE_ACCEPTANCE_REQUIRED',503); END IF;
END $$;

DO $$ DECLARE item record; BEGIN
 ALTER SCHEMA paypal_v1 OWNER TO interlock_dispatch_owner;
 FOR item IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='paypal_v1' AND c.relkind='r' LOOP
  EXECUTE format('ALTER TABLE paypal_v1.%I OWNER TO interlock_dispatch_owner',item.relname);
 END LOOP;
 FOR item IN SELECT p.oid::regprocedure AS name FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='paypal_v1' LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO interlock_dispatch_owner',item.name);
 END LOOP;
END $$;
ALTER FUNCTION authority.paypal_prepare_v1(text,text,jsonb,bigint,text) OWNER TO interlock_authority_owner;
ALTER FUNCTION authority.paypal_quote_v1(text,text,text,text) OWNER TO interlock_authority_owner;
ALTER FUNCTION paypal_v1.command(text,text,jsonb,bigint,text) OWNER TO interlock_authority_owner;
ALTER FUNCTION paypal_v1.denomination_guard() OWNER TO interlock_authority_owner;
ALTER FUNCTION paypal_v1.exact_policy_guard() OWNER TO interlock_authority_owner;
ALTER FUNCTION paypal_v1.new_tenant_only() OWNER TO interlock_authority_owner;
ALTER FUNCTION paypal_v1.purchase_membership_guard() OWNER TO interlock_authority_owner;
ALTER FUNCTION paypal_v1.proposal_scope(text,text) OWNER TO interlock_authority_owner;
ALTER FUNCTION paypal_v1.proposal(text,uuid) OWNER TO interlock_authority_owner;
ALTER FUNCTION paypal_v1.recover_proposal(text,text,text,text) OWNER TO interlock_authority_owner;
ALTER FUNCTION paypal_v1.record_proposal(text,text,text,text,jsonb) OWNER TO interlock_authority_owner;
ALTER FUNCTION paypal_v1.link_quote(text,uuid,text) OWNER TO interlock_authority_owner;
ALTER FUNCTION paypal_v1.snapshot(text) OWNER TO interlock_authority_owner;
ALTER FUNCTION paypal_v1.action_detail(text,text) OWNER TO interlock_authority_owner;
ALTER FUNCTION paypal_v1.record_read(text,text,text,text,text) OWNER TO interlock_economic_owner;
ALTER FUNCTION paypal_v1.note(text,text,text,jsonb) OWNER TO interlock_economic_owner;
ALTER FUNCTION paypal_v1.observe_seller(uuid) OWNER TO interlock_economic_owner;
ALTER FUNCTION paypal_v1.process_receipt(uuid) OWNER TO interlock_economic_owner;
ALTER FUNCTION paypal_v1.pending() OWNER TO interlock_economic_owner;

GRANT USAGE ON SCHEMA paypal_v1 TO interlock_authority_owner,interlock_economic_owner,
 interlock_reconciliation_owner,interlock_api,interlock_broker,interlock_economic;
GRANT USAGE ON SCHEMA dispatch TO interlock_authority_owner;
GRANT USAGE ON SCHEMA economic TO interlock_dispatch_owner;
GRANT EXECUTE ON FUNCTION authority.fail(text,int) TO interlock_dispatch_owner,interlock_economic_owner;
GRANT SELECT ON paypal_v1.tenants,paypal_v1.proposals,paypal_v1.proposal_quotes TO interlock_authority_owner;
GRANT INSERT ON paypal_v1.proposals,paypal_v1.proposal_quotes TO interlock_authority_owner;
GRANT SELECT ON dispatch.integrations,dispatch.business_operations TO interlock_authority_owner;
GRANT EXECUTE ON FUNCTION dispatch.canonical(jsonb) TO interlock_authority_owner;
GRANT SELECT ON paypal_v1.tenants,paypal_v1.bindings,paypal_v1.runtime,paypal_v1.phase_commits TO interlock_economic_owner;
GRANT UPDATE ON paypal_v1.runtime TO interlock_economic_owner;
GRANT INSERT ON paypal_v1.events,paypal_v1.transport_diagnostics TO interlock_economic_owner;
GRANT UPDATE ON dispatch.operation_runtime TO interlock_economic_owner;
GRANT EXECUTE ON FUNCTION economic.inspect(text,text) TO interlock_dispatch_owner;
GRANT SELECT ON economic.receipt_states TO interlock_dispatch_owner;
REVOKE ALL ON ALL TABLES IN SCHEMA paypal_v1 FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA paypal_v1 FROM PUBLIC;
REVOKE ALL ON FUNCTION authority.paypal_prepare_v1(text,text,jsonb,bigint,text),
 authority.paypal_quote_v1(text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION paypal_v1.assert_enabled() TO interlock_api,interlock_broker,interlock_economic;
GRANT EXECUTE ON FUNCTION authority.paypal_quote_v1(text,text,text,text),
 paypal_v1.command(text,text,jsonb,bigint,text),paypal_v1.proposal_scope(text,text),paypal_v1.proposal(text,uuid),
 paypal_v1.recover_proposal(text,text,text,text),paypal_v1.record_proposal(text,text,text,text,jsonb),
 paypal_v1.link_quote(text,uuid,text),paypal_v1.consent(text,text,bigint,text),
 paypal_v1.capture_request(text,text,bigint,text),paypal_v1.inspect(text,text),paypal_v1.snapshot(text),
 paypal_v1.action_detail(text,text) TO interlock_api;
GRANT EXECUTE ON FUNCTION paypal_v1.binding(text,text),paypal_v1.commit_phase(text,text,text,text),
 paypal_v1.order_hint(text,text,text),paypal_v1.record_read(text,text,text,text,text),paypal_v1.note(text,text,text,jsonb) TO interlock_broker;
GRANT EXECUTE ON FUNCTION paypal_v1.process_receipt(uuid),paypal_v1.pending() TO interlock_economic;
