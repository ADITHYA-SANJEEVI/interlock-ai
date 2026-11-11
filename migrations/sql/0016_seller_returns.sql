CREATE TABLE seller_truth.return_locks(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), order_id text NOT NULL UNIQUE REFERENCES seller_truth.orders(id),
 raw text NOT NULL, signature text NOT NULL, request_raw text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE seller_truth.return_closures(
 lock_id uuid PRIMARY KEY REFERENCES seller_truth.return_locks(id), raw text NOT NULL, signature text NOT NULL,
 proof_raw text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON seller_truth.return_locks FOR EACH ROW EXECUTE FUNCTION seller_truth.immutable();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON seller_truth.return_closures FOR EACH ROW EXECUTE FUNCTION seller_truth.immutable();

CREATE FUNCTION seller_truth.return_lock(raw text,sig text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,seller_truth,pg_temp AS $$
DECLARE key text; v jsonb; o seller_truth.orders; l seller_truth.return_locks; seats jsonb; att text; lid uuid;
BEGIN
 SELECT secret INTO key FROM seller_truth.keys WHERE name='capability';
 IF key IS NULL OR sig IS DISTINCT FROM encode(public.hmac(raw,key,'sha256'),'hex') THEN
 RAISE EXCEPTION 'authenticated return preparation required' USING ERRCODE='42501'; END IF;
 v:=raw::jsonb;
 IF v->>'kind' IS DISTINCT FROM 'RETURN_PREPARE' THEN RAISE EXCEPTION 'return preparation kind required'; END IF;
 SELECT * INTO o FROM seller_truth.orders WHERE operation_id=v->>'operation_id' FOR UPDATE;
 IF o.id IS NULL OR v->>'tenant_id' IS DISTINCT FROM o.tenant_id OR v->>'purchase_id' IS DISTINCT FROM o.purchase_id
 OR v->>'account_id' IS DISTINCT FROM o.account_id OR v->>'integration' IS DISTINCT FROM o.integration
 OR v->>'payment_id' IS DISTINCT FROM o.payment_id OR v->>'provider_order' IS DISTINCT FROM o.provider_order
 OR v->>'capture_fact_id' IS DISTINCT FROM o.capture_fact_id::text OR v->>'terms_digest' IS DISTINCT FROM o.terms_digest
 OR (v->>'amount')::bigint IS DISTINCT FROM o.amount OR v->>'currency' IS DISTINCT FROM o.currency
 THEN RAISE EXCEPTION 'exact fulfilled capture required'; END IF;
 PERFORM 1 FROM seller_truth.entitlements WHERE order_id=o.id ORDER BY id FOR UPDATE;
 SELECT jsonb_agg(jsonb_build_object('id',id,'beneficiary',beneficiary) ORDER BY id) INTO seats
 FROM seller_truth.entitlements WHERE order_id=o.id;
 IF seats IS DISTINCT FROM v->'seats' OR v->>'workspace_id' IS DISTINCT FROM o.workspace_id
 OR v->>'seller_finalization_hash' IS DISTINCT FROM
 (SELECT encode(public.digest(f.raw,'sha256'),'hex') FROM seller_truth.finalizations f WHERE order_id=o.id)
 THEN RAISE EXCEPTION 'exact entitlement/finalization required'; END IF;
 SELECT * INTO l FROM seller_truth.return_locks WHERE order_id=o.id;
 IF l.id IS NOT NULL THEN
 IF l.request_raw<>raw THEN RAISE EXCEPTION 'return preparation substitution'; END IF;
 RETURN jsonb_build_object('raw',l.raw,'signature',l.signature); END IF;
 lid:=gen_random_uuid();
 att:=seller_truth.canonical((v-'kind')||jsonb_build_object('kind','RETURN_LOCKED','lock_id',lid,'order_id',o.id));
 SELECT secret INTO key FROM seller_truth.keys WHERE name='attestation';
 sig:=encode(public.hmac(att,key,'sha256'),'hex');
 INSERT INTO seller_truth.return_locks VALUES(lid,o.id,att,sig,raw,clock_timestamp());
 INSERT INTO seller_truth.events(tenant_id,order_id,kind,detail) VALUES(o.tenant_id,o.id,'RETURN_LOCKED',jsonb_build_object('lock_id',lid,'seats',seats));
 RETURN jsonb_build_object('raw',att,'signature',sig);
END $$;

-- The same order lock serializes competing capability with return preparation.
CREATE OR REPLACE FUNCTION seller_truth.workspace(token text,seatid text,note text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,seller_truth,pg_temp AS $$
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
 IF note IS NOT NULL THEN INSERT INTO seller_truth.entries(entitlement_id,text) VALUES(e.id,note); END IF;
 INSERT INTO seller_truth.events(tenant_id,order_id,kind,detail) VALUES(e.tenant_id,e.order_id,'WORKSPACE_ACCESSED',jsonb_build_object('beneficiary',u.beneficiary,'seat_id',e.id));
 RETURN jsonb_build_object('seat_id',e.id,'workspace_id',e.workspace_id,'beneficiary',e.beneficiary,'product',e.product,
 'entries',coalesce((SELECT jsonb_agg(jsonb_build_object('id',id,'text',text) ORDER BY created_at,id) FROM seller_truth.entries WHERE entitlement_id=e.id),'[]'::jsonb));
END $$;

CREATE FUNCTION seller_truth.return_close(raw text,sig text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,seller_truth,pg_temp AS $$
DECLARE v jsonb; key text; l seller_truth.return_locks; o seller_truth.orders; c seller_truth.return_closures; att text;
BEGIN
 SELECT secret INTO key FROM seller_truth.keys WHERE name='capability';
 IF key IS NULL OR sig IS DISTINCT FROM encode(public.hmac(raw,key,'sha256'),'hex') THEN
 RAISE EXCEPTION 'authenticated full return proof required' USING ERRCODE='42501'; END IF;
 v:=raw::jsonb;
 SELECT * INTO l FROM seller_truth.return_locks WHERE id=(v->>'lock_id')::uuid;
 SELECT * INTO o FROM seller_truth.orders WHERE id=l.order_id FOR UPDATE;
 IF l.id IS NULL OR v->>'kind' IS DISTINCT FROM 'RETURN_CLOSE' OR v->>'lock_hash' IS DISTINCT FROM encode(public.digest(l.raw,'sha256'),'hex')
 OR (v->>'processed')::bigint IS DISTINCT FROM o.amount OR (v->>'unresolved')::bigint IS DISTINCT FROM 0::bigint
 OR v->>'capture_fact_id' IS DISTINCT FROM o.capture_fact_id::text THEN RAISE EXCEPTION 'exact full principal resolution required'; END IF;
 SELECT * INTO c FROM seller_truth.return_closures WHERE lock_id=l.id;
 IF c.lock_id IS NOT NULL THEN
 IF c.proof_raw<>raw THEN RAISE EXCEPTION 'closure proof substitution'; END IF;
 RETURN jsonb_build_object('raw',c.raw,'signature',c.signature); END IF;
 PERFORM 1 FROM seller_truth.entitlements WHERE order_id=o.id ORDER BY id FOR UPDATE;
 INSERT INTO seller_truth.revocations(entitlement_id,reason) SELECT id,'COMMERCIAL_RETURN:'||l.id FROM seller_truth.entitlements WHERE order_id=o.id
 ON CONFLICT DO NOTHING;
 INSERT INTO seller_truth.events(tenant_id,order_id,kind,detail) VALUES(o.tenant_id,o.id,'ACCESS_REVOKED',jsonb_build_object('lock_id',l.id));
 att:=seller_truth.canonical(l.raw::jsonb||jsonb_build_object('kind','RETURN_COMPLETED','closure_proof_hash',encode(public.digest(raw,'sha256'),'hex')));
 SELECT secret INTO key FROM seller_truth.keys WHERE name='attestation'; sig:=encode(public.hmac(att,key,'sha256'),'hex');
 INSERT INTO seller_truth.return_closures VALUES(l.id,att,sig,raw,clock_timestamp());
 INSERT INTO seller_truth.events(tenant_id,order_id,kind,detail) VALUES(o.tenant_id,o.id,'RETURN_COMPLETED',jsonb_build_object('lock_id',l.id));
 RETURN jsonb_build_object('raw',att,'signature',sig);
END $$;
ALTER TABLE seller_truth.return_locks OWNER TO interlock_seller_truth_owner;
ALTER TABLE seller_truth.return_closures OWNER TO interlock_seller_truth_owner;
ALTER FUNCTION seller_truth.return_lock(text,text) OWNER TO interlock_seller_truth_owner;
ALTER FUNCTION seller_truth.return_close(text,text) OWNER TO interlock_seller_truth_owner;
REVOKE ALL ON seller_truth.return_locks,seller_truth.return_closures FROM PUBLIC;
REVOKE ALL ON FUNCTION seller_truth.return_lock(text,text),seller_truth.return_close(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION seller_truth.return_lock(text,text),seller_truth.return_close(text,text) TO interlock_seller;
