-- Keep the already-installed additive 0025 intact. Additional constraints prove
-- no partial release can commit without its terminal, auditable cancellation.
CREATE FUNCTION paypal_v1.unsent_transition_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
BEGIN
 IF NEW.state='CANCEL_UNSENT' AND OLD.state<>'CANCEL_UNSENT'
 AND EXISTS(SELECT 1 FROM paypal_v1.bindings WHERE operation_id=NEW.operation_id) THEN
  IF OLD.latest_observation_id IS NOT NULL OR OLD.blocked_reason IS NOT NULL
  OR (SELECT count(*) FROM dispatch.outbox_items WHERE operation_id=NEW.operation_id)<>2 THEN
   PERFORM authority.fail('NEVER_SENT_PROOF_UNAVAILABLE'); END IF;
 END IF;
 RETURN NEW;
END $$;
ALTER FUNCTION paypal_v1.unsent_transition_guard() OWNER TO interlock_dispatch_owner;
REVOKE ALL ON FUNCTION paypal_v1.unsent_transition_guard() FROM PUBLIC;
CREATE TRIGGER unsent_transition BEFORE UPDATE ON dispatch.operation_runtime
 FOR EACH ROW EXECUTE FUNCTION paypal_v1.unsent_transition_guard();

CREATE FUNCTION paypal_v1.unsent_movement_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE opid text; BEGIN
 SELECT operation_id INTO opid FROM paypal_v1.cancellations WHERE operation_id IN
  (SELECT id FROM dispatch.business_operations WHERE reservation_id=NEW.reservation_id);
 IF opid IS NULL THEN PERFORM authority.fail('INCOMPLETE_UNSENT_CANCELLATION_BUNDLE'); END IF;
 IF NOT EXISTS(SELECT 1 FROM authority.reservation_heads WHERE reservation_id=NEW.reservation_id AND status='CANCELLED_UNSENT') THEN
  PERFORM authority.fail('INCOMPLETE_UNSENT_CANCELLATION_BUNDLE'); END IF;
 RETURN NULL;
END $$;
ALTER FUNCTION paypal_v1.unsent_movement_guard() OWNER TO interlock_dispatch_owner;
REVOKE ALL ON FUNCTION paypal_v1.unsent_movement_guard() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER unsent_release_proof AFTER INSERT ON authority.authority_movements DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW WHEN(NEW.kind='UNSENT_RELEASE') EXECUTE FUNCTION paypal_v1.unsent_movement_guard();
