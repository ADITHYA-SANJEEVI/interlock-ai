-- New seller evidence authorizes only the SAME allocation and original terms.
CREATE TABLE seller_truth.paypal_continuations (
 id uuid PRIMARY KEY, request_raw text NOT NULL, attestation_raw text NOT NULL, signature text NOT NULL
);
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON seller_truth.paypal_continuations
 FOR EACH ROW EXECUTE FUNCTION seller_truth.immutable();
CREATE FUNCTION seller_truth.paypal_continuation_v2(raw text,sig text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,seller_truth,pg_temp AS $$
DECLARE key text; signer text; v jsonb; q public.quotes; b seller_truth.bindings;
 prior seller_truth.paypal_continuations; att text; deadline timestamptz;
BEGIN
 SELECT secret INTO key FROM seller_truth.keys WHERE name='paypal-quote-request';
 IF key IS NULL OR length(raw)>32000 OR sig IS DISTINCT FROM encode(public.hmac(raw,key,'sha256'),'hex') THEN
  RAISE EXCEPTION 'authenticated continuation request required' USING ERRCODE='42501'; END IF;
 v:=raw::jsonb;
 IF raw IS DISTINCT FROM seller_truth.canonical(v) OR v->>'schema' IS DISTINCT FROM 'paypal-capture-continuation-request-v2'
 OR v->>'operation_id' IS NULL OR v->>'provider_order_id' !~ '^[A-Z0-9]{10,32}$'
 OR v->>'currency' IS DISTINCT FROM 'USD' OR v->'amount_minor' IS DISTINCT FROM '100'::jsonb
 OR v->'quantity' IS DISTINCT FROM '1'::jsonb OR v->>'product_id' IS DISTINCT FROM 'workspace-seat' THEN
  RAISE EXCEPTION 'exact continuation terms required'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('continuation:'||(v->>'continuation_id'),0));
 SELECT * INTO prior FROM seller_truth.paypal_continuations WHERE id=(v->>'continuation_id')::uuid;
 IF FOUND THEN
  IF prior.request_raw<>raw THEN RAISE EXCEPTION 'continuation command substitution'; END IF;
  RETURN jsonb_build_object('raw',prior.attestation_raw,'signature',prior.signature);
 END IF;
 SELECT * INTO b FROM seller_truth.bindings WHERE tenant_id=v->>'tenant_id';
 SELECT * INTO q FROM public.quotes WHERE id=v->>'quote_id' AND tenant_id=b.tenant_id FOR SHARE;
 deadline:=(v->>'expires_at')::timestamptz;
 IF b.tenant_id IS NULL OR b.account_id IS DISTINCT FROM v->>'merchant_id' OR b.seller IS DISTINCT FROM v->>'seller'
 OR q.id IS NULL OR q.body IS DISTINCT FROM v->'quote'
 OR encode(public.digest(seller_truth.canonical(q.body),'sha256'),'hex') IS DISTINCT FROM v->>'quote_digest'
 OR q.body->>'merchant_id' IS DISTINCT FROM b.account_id OR q.body->>'provider' IS DISTINCT FROM 'PAYPAL_SANDBOX'
 OR q.body->>'currency' IS DISTINCT FROM 'USD' OR q.body->'amount' IS DISTINCT FROM '100'::jsonb
 OR jsonb_array_length(q.body->'lines')<>1 OR q.body->'lines'->0->>'sku' IS DISTINCT FROM 'workspace-seat'
 OR q.body->'lines'->0->>'beneficiary' IS DISTINCT FROM v->>'beneficiary'
 OR q.body->'lines'->0->'unit_price' IS DISTINCT FROM '100'::jsonb
 OR deadline IS NULL OR deadline<=clock_timestamp() OR deadline>clock_timestamp()+interval '30 minutes'
 OR NOT EXISTS(SELECT 1 FROM seller_truth.identities WHERE tenant_id=b.tenant_id AND beneficiary=v->>'beneficiary')
 OR EXISTS(SELECT 1 FROM seller_truth.orders WHERE operation_id=v->>'operation_id' OR quote_id=q.id)
 OR EXISTS(SELECT 1 FROM seller_truth.entitlements WHERE id=q.body->'lines'->0->>'allocation_id') THEN
  RAISE EXCEPTION 'seller cannot continue these exact original terms'; END IF;
 att:=seller_truth.canonical(v||jsonb_build_object('schema','paypal-capture-continuation-attestation-v2'));
 SELECT secret INTO signer FROM seller_truth.keys WHERE name='paypal-quote';
 IF signer IS NULL THEN RAISE EXCEPTION 'seller continuation signer unavailable'; END IF;
 sig:=encode(public.hmac(att,signer,'sha256'),'hex');
 INSERT INTO seller_truth.paypal_continuations VALUES((v->>'continuation_id')::uuid,raw,att,sig);
 RETURN jsonb_build_object('raw',att,'signature',sig);
END $$;
ALTER TABLE seller_truth.paypal_continuations OWNER TO interlock_seller_truth_owner;
ALTER FUNCTION seller_truth.paypal_continuation_v2(text,text) OWNER TO interlock_seller_truth_owner;
REVOKE ALL ON seller_truth.paypal_continuations FROM PUBLIC;
REVOKE ALL ON FUNCTION seller_truth.paypal_continuation_v2(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION seller_truth.paypal_continuation_v2(text,text) TO interlock_seller;
