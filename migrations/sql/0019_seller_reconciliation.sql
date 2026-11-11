-- Read-only, exact-operation capability. No seller mutation is possible through it.
CREATE FUNCTION seller_truth.reconciliation_read(raw text, signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,seller_truth,pg_temp AS $$
DECLARE v jsonb; k text; att text; state jsonb;
BEGIN
 SELECT secret INTO k FROM seller_truth.keys WHERE name='capability';
 IF k IS NULL OR signature IS DISTINCT FROM encode(public.hmac(raw,k,'sha256'),'hex') THEN
 RAISE EXCEPTION 'authenticated reconciliation read required' USING ERRCODE='42501'; END IF;
 v:=raw::jsonb;
 IF v->>'kind' IS DISTINCT FROM 'RECONCILIATION_READ' OR (v->>'expires_at')::bigint <= extract(epoch FROM clock_timestamp())
 OR NOT EXISTS(SELECT 1 FROM seller_truth.bindings WHERE tenant_id=v->>'tenant_id'
 AND account_id=v->>'account_id' AND integration=v->>'integration') THEN
 RAISE EXCEPTION 'exact live read scope required' USING ERRCODE='42501'; END IF;
 -- One statement snapshot. Revision counts immutable state changes, never activity events.
 SELECT jsonb_build_object('order_id',o.id,'capture_fact_id',o.capture_fact_id,'payment_id',o.payment_id,
 'revision',CASE WHEN o.id IS NULL THEN 0 ELSE 1 END+
 (SELECT count(*) FROM seller_truth.revocations r JOIN seller_truth.entitlements e ON e.id=r.entitlement_id WHERE e.order_id=o.id)+
 (SELECT count(*) FROM seller_truth.return_locks l WHERE l.order_id=o.id)+
 (SELECT count(*) FROM seller_truth.return_closures c JOIN seller_truth.return_locks l ON l.id=c.lock_id WHERE l.order_id=o.id),
 'fulfilled',o.id IS NOT NULL,'lock_id',(SELECT id FROM seller_truth.return_locks WHERE order_id=o.id),
 'closed',EXISTS(SELECT 1 FROM seller_truth.return_closures c JOIN seller_truth.return_locks l ON l.id=c.lock_id WHERE l.order_id=o.id),
 'active_entitlement_ids',coalesce((SELECT jsonb_agg(e.id ORDER BY e.id) FROM seller_truth.entitlements e
 WHERE e.order_id=o.id AND NOT EXISTS(SELECT 1 FROM seller_truth.revocations r WHERE r.entitlement_id=e.id)),'[]'::jsonb))
 INTO state FROM (SELECT 1) anchor LEFT JOIN seller_truth.orders o ON o.operation_id=v->>'operation_id'
 AND o.tenant_id=v->>'tenant_id' AND o.account_id=v->>'account_id' AND o.integration=v->>'integration';
 att:=seller_truth.canonical(jsonb_build_object('kind','RECONCILIATION_SELLER_STATE','run_id',v->>'run_id',
 'operation_id',v->>'operation_id','tenant_id',v->>'tenant_id','account_id',v->>'account_id',
 'integration',v->>'integration','observed_at',clock_timestamp(),'state',state));
 SELECT secret INTO k FROM seller_truth.keys WHERE name='attestation';
 IF k IS NULL THEN RAISE EXCEPTION 'seller attestation key missing'; END IF;
 RETURN jsonb_build_object('raw',att,'signature',encode(public.hmac(att,k,'sha256'),'hex'));
END $$;
ALTER FUNCTION seller_truth.reconciliation_read(text,text) OWNER TO interlock_seller_truth_owner;
REVOKE ALL ON FUNCTION seller_truth.reconciliation_read(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION seller_truth.reconciliation_read(text,text) TO interlock_seller;
