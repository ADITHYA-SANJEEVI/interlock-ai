CREATE OR REPLACE FUNCTION seller_truth.workspace(token text, seatid text, note text DEFAULT NULL::text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'seller_truth', 'pg_temp'
AS $function$
DECLARE u seller_truth.identities; e seller_truth.entitlements; oid text;
BEGIN
 SELECT * INTO u FROM seller_truth.identities WHERE token_hash=encode(public.digest(token,'sha256'),'hex');
 IF u.username IS NULL THEN RAISE EXCEPTION 'seller identity required' USING ERRCODE='42501'; END IF;
 SELECT order_id INTO oid FROM seller_truth.entitlements WHERE id=seatid AND tenant_id=u.tenant_id AND beneficiary=u.beneficiary;
 PERFORM 1 FROM seller_truth.orders WHERE id=oid FOR SHARE;
 SELECT * INTO e FROM seller_truth.entitlements WHERE id=seatid AND tenant_id=u.tenant_id AND beneficiary=u.beneficiary FOR SHARE;
 IF e.id IS NULL OR EXISTS(SELECT 1 FROM seller_truth.revocations WHERE entitlement_id=e.id)
 OR EXISTS(SELECT 1 FROM seller_truth.return_locks WHERE order_id=e.order_id) THEN
 RAISE EXCEPTION 'entitlement outside active beneficiary scope' USING ERRCODE='42501'; END IF;
 IF note IS NOT NULL AND (length(note)>500 OR length(note)=0) THEN
 RAISE EXCEPTION 'INVALID_REQUEST' USING ERRCODE='P0001', DETAIL='422'; END IF;
 IF note IS NOT NULL THEN INSERT INTO seller_truth.entries(entitlement_id,text) VALUES(e.id,note); END IF;
 INSERT INTO seller_truth.events(tenant_id,order_id,kind,detail) VALUES(e.tenant_id,e.order_id,'WORKSPACE_ACCESSED',jsonb_build_object('beneficiary',u.beneficiary,'seat_id',e.id));
 RETURN jsonb_build_object('seat_id',e.id,'workspace_id',e.workspace_id,'beneficiary',e.beneficiary,'product',e.product,
 'entries',coalesce((SELECT jsonb_agg(jsonb_build_object('id',id,'text',text) ORDER BY created_at,id) FROM (SELECT * FROM seller_truth.entries WHERE entitlement_id=e.id ORDER BY created_at DESC,id DESC LIMIT 50) recent),'[]'::jsonb));
END $function$
;
