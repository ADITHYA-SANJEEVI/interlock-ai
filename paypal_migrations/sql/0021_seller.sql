-- Separate seller database. The original INR quote/finalize routes are unchanged.
ALTER TABLE seller_truth.orders DROP CONSTRAINT orders_currency_check;
ALTER TABLE seller_truth.orders ADD CHECK(currency IN ('INR','USD'));
CREATE TABLE seller_truth.paypal_tenants (
 tenant_id text PRIMARY KEY REFERENCES seller_truth.bindings(tenant_id),
 merchant_id text NOT NULL CHECK(merchant_id='JB7TR6VHB4GTQ'),
 currency text NOT NULL CHECK(currency='USD'), unit_price_minor bigint NOT NULL CHECK(unit_price_minor=100)
);
CREATE TRIGGER immutable_paypal_tenant BEFORE UPDATE OR DELETE ON seller_truth.paypal_tenants
 FOR EACH ROW EXECUTE FUNCTION seller_truth.immutable();
CREATE FUNCTION seller_truth.paypal_new_tenant_v1() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,seller_truth,pg_temp AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM public.quotes WHERE tenant_id=NEW.tenant_id)
 OR EXISTS(SELECT 1 FROM seller_truth.orders WHERE tenant_id=NEW.tenant_id)
 OR NOT EXISTS(SELECT 1 FROM seller_truth.bindings WHERE tenant_id=NEW.tenant_id AND account_id=NEW.merchant_id) THEN
  RAISE EXCEPTION 'new exact USD merchant tenant required'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER new_paypal_tenant BEFORE INSERT ON seller_truth.paypal_tenants
 FOR EACH ROW EXECUTE FUNCTION seller_truth.paypal_new_tenant_v1();
CREATE FUNCTION seller_truth.paypal_quote_v1(raw text,sig text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,seller_truth,pg_temp AS $$
DECLARE key text; qkey text; v jsonb; b seller_truth.bindings; p seller_truth.paypal_tenants;
 prior public.quotes; q jsonb; qraw text; quoteid text; expires timestamptz; BEGIN
 SELECT secret INTO key FROM seller_truth.keys WHERE name='paypal-quote-request';
 IF key IS NULL OR sig IS DISTINCT FROM encode(public.hmac(raw,key,'sha256'),'hex') THEN
  RAISE EXCEPTION 'authenticated quote request required' USING ERRCODE='42501'; END IF;
 v:=raw::jsonb;
 IF raw IS DISTINCT FROM seller_truth.canonical(v) OR v->>'schema' IS DISTINCT FROM 'paypal-quote-request-v1'
 OR v->>'product_id' IS DISTINCT FROM 'workspace-seat' OR v->'quantity' IS DISTINCT FROM '1'::jsonb
 OR v->>'currency' IS DISTINCT FROM 'USD' OR v->>'request_id' IS NULL OR length(v->>'request_id')>100
 OR (v->>'expires_at')::timestamptz<=clock_timestamp() THEN RAISE EXCEPTION 'bounded exact quote request required'; END IF;
 SELECT * INTO b FROM seller_truth.bindings WHERE tenant_id=v->>'tenant_id';
 SELECT * INTO p FROM seller_truth.paypal_tenants WHERE tenant_id=b.tenant_id;
 IF p.tenant_id IS NULL OR p.merchant_id<>b.account_id OR NOT EXISTS(SELECT 1 FROM seller_truth.identities
  WHERE tenant_id=b.tenant_id AND beneficiary=v->>'beneficiary') THEN
  RAISE EXCEPTION 'registered USD seller beneficiary required'; END IF;
 quoteid:='usdquote_'||encode(public.digest(b.tenant_id||':'||(v->>'request_id'),'sha256'),'hex');
 PERFORM pg_advisory_xact_lock(hashtextextended(quoteid,0));
 SELECT * INTO prior FROM public.quotes WHERE id=quoteid;
 IF prior.id IS NOT NULL THEN
  IF prior.body->'lines'->0->>'beneficiary' IS DISTINCT FROM v->>'beneficiary' THEN RAISE EXCEPTION 'quote command substitution'; END IF;
  q:=prior.body;
 ELSE
  expires:=least((v->>'expires_at')::timestamptz,clock_timestamp()+interval '15 minutes');
  q:=jsonb_build_object('id',quoteid,'tenant_id',b.tenant_id,'seller',b.seller,'revision',1,
   'provider','PAYPAL_SANDBOX','merchant_id',p.merchant_id,'amount',100,'currency','USD','expires_at',expires,
   'lines',jsonb_build_array(jsonb_build_object('sku','workspace-seat','beneficiary',v->>'beneficiary',
    'unit_price',100,'allocation_id','usdseat_'||replace(gen_random_uuid()::text,'-',''))));
  INSERT INTO public.quotes(id,tenant_id,body,expires_at) VALUES(quoteid,b.tenant_id,q,expires);
 END IF;
 SELECT secret INTO qkey FROM seller_truth.keys WHERE name='paypal-quote';
 IF qkey IS NULL THEN RAISE EXCEPTION 'USD quote signer missing'; END IF;
 qraw:=seller_truth.canonical(q);
 RETURN jsonb_build_object('raw',qraw,'signature',encode(public.hmac(qraw,qkey,'sha256'),'hex'));
END $$;

-- Mechanical fixed-price USD specialization of certified seller finalization.
CREATE FUNCTION seller_truth.paypal_finalize_v1(raw text,sig text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,seller_truth,pg_temp AS $$
DECLARE key text; v jsonb; b seller_truth.bindings; q public.quotes; o seller_truth.orders;
 line jsonb; orderid text; att text; attest_key text; seats jsonb; BEGIN
 SELECT secret INTO key FROM seller_truth.keys WHERE name='capability';
 IF key IS NULL OR sig IS DISTINCT FROM encode(public.hmac(raw,key,'sha256'),'hex') THEN
 RAISE EXCEPTION 'capture capability authentication required' USING ERRCODE='42501'; END IF;
 v:=raw::jsonb;
 IF raw IS DISTINCT FROM seller_truth.canonical(v)
 OR v->>'payment_id' IS NULL OR v->>'payment_id' !~ '^[A-Z0-9]{10,32}$'
 OR v->>'provider_order' IS NULL OR v->>'provider_order' !~ '^[A-Z0-9]{10,32}$'
 OR v->'quote'->>'provider' IS DISTINCT FROM 'PAYPAL_SANDBOX'
 OR NOT EXISTS(SELECT 1 FROM seller_truth.paypal_tenants t WHERE t.tenant_id=v->>'tenant_id'
 AND t.merchant_id=v->>'account_id' AND t.merchant_id=v->'quote'->>'merchant_id') THEN
 RAISE EXCEPTION 'exact native PayPal USD capability required'; END IF;
 SELECT * INTO b FROM seller_truth.bindings WHERE tenant_id=v->>'tenant_id';
 IF b.tenant_id IS NULL OR b.account_id IS DISTINCT FROM v->>'account_id' OR b.integration IS DISTINCT FROM v->>'integration' THEN
 RAISE EXCEPTION 'seller tenant/account binding mismatch'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('seller:'||(v->>'operation_id'),0));
 SELECT * INTO o FROM seller_truth.orders WHERE operation_id=v->>'operation_id';
 IF o.id IS NOT NULL THEN
 IF o.capability_raw<>raw THEN RAISE EXCEPTION 'seller command substitution'; END IF;
 RETURN (SELECT jsonb_build_object('raw',f.raw,'signature',f.signature) FROM seller_truth.finalizations f WHERE order_id=o.id); END IF;
 SELECT * INTO q FROM public.quotes WHERE id=v->>'quote_id' AND tenant_id=b.tenant_id FOR SHARE;
 IF q.id IS NULL OR q.body IS DISTINCT FROM v->'quote' OR q.body->>'seller' IS DISTINCT FROM b.seller OR
 encode(public.digest(seller_truth.canonical(q.body),'sha256'),'hex') IS DISTINCT FROM v->>'terms_digest'
 OR (q.body->>'amount')::bigint IS DISTINCT FROM (v->>'amount')::bigint OR q.body->>'currency' IS DISTINCT FROM v->>'currency'
 OR jsonb_array_length(q.body->'lines') NOT BETWEEN 1 AND 5 OR v->>'currency' IS DISTINCT FROM 'USD'
 OR (v->>'amount')::bigint<>100*jsonb_array_length(q.body->'lines') OR v->>'operation_id' IS NULL
 OR v->>'purchase_id' IS NULL OR v->>'payment_id' IS NULL OR v->>'provider_order' IS NULL THEN RAISE EXCEPTION 'exact immutable quote required'; END IF;
 FOR line IN SELECT * FROM jsonb_array_elements(q.body->'lines') LOOP
 IF line->>'sku' IS DISTINCT FROM 'workspace-seat' OR (line->>'unit_price')::bigint IS DISTINCT FROM 100 OR
 NOT EXISTS(SELECT 1 FROM seller_truth.identities WHERE tenant_id=b.tenant_id AND beneficiary=line->>'beneficiary') THEN
 RAISE EXCEPTION 'registered exact beneficiary required'; END IF; END LOOP;
 orderid:='seller3_'||replace(gen_random_uuid()::text,'-','');
 INSERT INTO seller_truth.orders VALUES(orderid,v->>'operation_id',v->>'purchase_id',b.tenant_id,b.workspace_id,b.account_id,b.integration,
 q.id,v->>'terms_digest',q.body,(v->>'capture_fact_id')::uuid,v->>'payment_id',v->>'provider_order',(v->>'amount')::bigint,v->>'currency',raw,sig,clock_timestamp());
 INSERT INTO seller_truth.entitlements(id,tenant_id,order_id,workspace_id,beneficiary,product)
 SELECT x->>'allocation_id',b.tenant_id,orderid,b.workspace_id,x->>'beneficiary','workspace-seat' FROM jsonb_array_elements(q.body->'lines') x;
 SELECT jsonb_agg(jsonb_build_object('id',id,'beneficiary',beneficiary) ORDER BY id) INTO seats FROM seller_truth.entitlements WHERE order_id=orderid;
 att:=seller_truth.canonical((v-'quote')||jsonb_build_object('order_id',orderid,'state','FULFILLED','seats',seats,'workspace_id',b.workspace_id));
 SELECT secret INTO attest_key FROM seller_truth.keys WHERE name='attestation';
 IF attest_key IS NULL THEN RAISE EXCEPTION 'seller attestation key missing'; END IF;
 sig:=encode(public.hmac(att,attest_key,'sha256'),'hex');
 INSERT INTO seller_truth.finalizations VALUES(orderid,att,sig,clock_timestamp());
 INSERT INTO seller_truth.events(tenant_id,order_id,kind,detail) VALUES(b.tenant_id,orderid,'FULFILLMENT_FINALIZED',jsonb_build_object('capture_fact_id',v->>'capture_fact_id','seat_count',jsonb_array_length(seats)));
 RETURN jsonb_build_object('raw',att,'signature',sig);
END $$;

ALTER TABLE seller_truth.paypal_tenants OWNER TO interlock_seller_truth_owner;
ALTER FUNCTION seller_truth.paypal_new_tenant_v1() OWNER TO interlock_seller_truth_owner;
ALTER FUNCTION seller_truth.paypal_quote_v1(text,text) OWNER TO interlock_seller_truth_owner;
ALTER FUNCTION seller_truth.paypal_finalize_v1(text,text) OWNER TO interlock_seller_truth_owner;
GRANT INSERT ON public.quotes TO interlock_seller_truth_owner;
REVOKE ALL ON seller_truth.paypal_tenants FROM PUBLIC;
REVOKE ALL ON FUNCTION seller_truth.paypal_quote_v1(text,text),seller_truth.paypal_finalize_v1(text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION seller_truth.paypal_new_tenant_v1() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION seller_truth.paypal_quote_v1(text,text),seller_truth.paypal_finalize_v1(text,text) TO interlock_seller;
