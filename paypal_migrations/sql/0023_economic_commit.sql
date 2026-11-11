-- Deferred trigger checks must execute while the economic definer is active.
-- Preserve the existing journal guard and posting implementation unchanged.
CREATE FUNCTION paypal_v1.post_fact(fid uuid) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE result uuid; BEGIN
 IF session_user<>'interlock_economic' OR NOT EXISTS(
  SELECT 1 FROM economic.facts f JOIN paypal_v1.bindings b ON b.operation_id=f.operation_id
  JOIN paypal_v1.runtime r ON r.operation_id=b.operation_id
  WHERE f.id=fid AND f.currency='USD' AND NOT r.blocked) THEN
  PERFORM authority.fail('PAYPAL_AUTHENTICATED_UNBLOCKED_FACT_REQUIRED',403); END IF;
 result:=economic.post(fid);
 SET CONSTRAINTS economic.journal_bundle IMMEDIATE;
 SET CONSTRAINTS economic.journal_bundle DEFERRED;
 RETURN result;
END $$;
ALTER FUNCTION paypal_v1.post_fact(uuid) OWNER TO interlock_economic_owner;
REVOKE ALL ON FUNCTION paypal_v1.post_fact(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION paypal_v1.post_fact(uuid) TO interlock_economic;

CREATE OR REPLACE FUNCTION paypal_v1.process_receipt(rid uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE oid uuid; facts jsonb; fid jsonb; BEGIN
 IF session_user<>'interlock_economic' THEN PERFORM authority.fail('ECONOMIC_ROLE_REQUIRED',403); END IF;
 IF economic.authenticate(rid)<>'AUTHENTICATED' THEN RETURN '[]'::jsonb; END IF;
 SELECT observation_id INTO oid FROM economic.receipt_states WHERE receipt_id=rid;
 IF oid IS NULL THEN oid:=paypal_v1.observe_seller(rid); END IF;
 IF oid IS NULL OR NOT EXISTS(SELECT 1 FROM economic.observations ob JOIN dispatch.business_operations o ON o.id=ob.operation_id
  WHERE ob.id=oid AND o.adapter='PAYPAL_SANDBOX' AND ob.currency='USD') THEN RETURN '[]'::jsonb; END IF;
 IF EXISTS(SELECT 1 FROM paypal_v1.runtime r JOIN economic.observations ob ON ob.operation_id=r.operation_id
  WHERE ob.id=oid AND r.blocked) THEN RETURN '[]'::jsonb; END IF;
 facts:=economic.derive(oid);
 FOR fid IN SELECT value FROM jsonb_array_elements(facts) LOOP PERFORM paypal_v1.post_fact((fid#>>'{}')::uuid); END LOOP;
 RETURN facts;
END $$;
