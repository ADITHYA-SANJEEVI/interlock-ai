CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA seller_truth;
REVOKE ALL ON SCHEMA seller_truth FROM PUBLIC;
CREATE TABLE seller_truth.keys(name text PRIMARY KEY, secret text NOT NULL CHECK(length(secret)>=16));
CREATE TABLE seller_truth.identities(
 username text PRIMARY KEY, tenant_id text NOT NULL, beneficiary text NOT NULL,
 token_hash text NOT NULL UNIQUE, UNIQUE(tenant_id,beneficiary)
);
CREATE TABLE seller_truth.bindings(
 tenant_id text PRIMARY KEY, account_id text NOT NULL, integration text NOT NULL, workspace_id text NOT NULL UNIQUE,
 seller text NOT NULL CHECK(seller='workspace-seller-v1')
);
CREATE TABLE seller_truth.orders(
 id text PRIMARY KEY, operation_id text NOT NULL UNIQUE, purchase_id text NOT NULL UNIQUE,
 tenant_id text NOT NULL REFERENCES seller_truth.bindings(tenant_id), workspace_id text NOT NULL,
 account_id text NOT NULL, integration text NOT NULL, quote_id text NOT NULL UNIQUE REFERENCES public.quotes(id),
 terms_digest text NOT NULL, terms jsonb NOT NULL, capture_fact_id uuid NOT NULL UNIQUE, payment_id text NOT NULL,
 provider_order text NOT NULL, amount bigint NOT NULL CHECK(amount>0), currency text NOT NULL CHECK(currency='INR'),
 capability_raw text NOT NULL, capability_signature text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(tenant_id,id)
);
CREATE TABLE seller_truth.entitlements(
 id text PRIMARY KEY, tenant_id text NOT NULL, order_id text NOT NULL, workspace_id text NOT NULL,
 beneficiary text NOT NULL, product text NOT NULL CHECK(product='workspace-seat'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(order_id,beneficiary),
 FOREIGN KEY(tenant_id,order_id) REFERENCES seller_truth.orders(tenant_id,id)
);
CREATE TABLE seller_truth.finalizations(
 order_id text PRIMARY KEY REFERENCES seller_truth.orders(id), raw text NOT NULL, signature text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE seller_truth.revocations(
 entitlement_id text PRIMARY KEY REFERENCES seller_truth.entitlements(id), reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE seller_truth.events(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id text NOT NULL, order_id text NOT NULL,
 kind text NOT NULL, detail jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE seller_truth.entries(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), entitlement_id text NOT NULL REFERENCES seller_truth.entitlements(id),
 text text NOT NULL CHECK(length(text) BETWEEN 1 AND 500), created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION seller_truth.immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION 'immutable seller truth' USING ERRCODE='42501'; END $$;
DO $$ DECLARE t text; BEGIN FOREACH t IN ARRAY ARRAY['identities','orders','entitlements','finalizations','revocations','events','entries'] LOOP
 EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON seller_truth.%I FOR EACH ROW EXECUTE FUNCTION seller_truth.immutable()',t);
END LOOP; END $$;
CREATE FUNCTION seller_truth.canonical(v jsonb) RETURNS text LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,seller_truth,pg_temp AS $$
DECLARE result text; BEGIN CASE jsonb_typeof(v)
 WHEN 'object' THEN SELECT '{'||coalesce(string_agg(to_jsonb(key)::text||':'||seller_truth.canonical(value),',' ORDER BY key COLLATE "C"),'')||'}' INTO result FROM jsonb_each(v);
 WHEN 'array' THEN SELECT '['||coalesce(string_agg(seller_truth.canonical(value),',' ORDER BY ord),'')||']' INTO result FROM jsonb_array_elements(v) WITH ORDINALITY a(value,ord);
 ELSE result:=v::text; END CASE; RETURN result; END $$;
CREATE FUNCTION seller_truth.finalize(raw text,sig text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,seller_truth,pg_temp AS $$
DECLARE key text; v jsonb; b seller_truth.bindings; q public.quotes; o seller_truth.orders;
 line jsonb; orderid text; att text; attest_key text; seats jsonb; BEGIN
 SELECT secret INTO key FROM seller_truth.keys WHERE name='capability';
 IF key IS NULL OR sig IS DISTINCT FROM encode(public.hmac(raw,key,'sha256'),'hex') THEN
 RAISE EXCEPTION 'capture capability authentication required' USING ERRCODE='42501'; END IF;
 v:=raw::jsonb;
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
 OR jsonb_array_length(q.body->'lines') NOT BETWEEN 1 AND 5 OR v->>'currency' IS DISTINCT FROM 'INR'
 OR (v->>'amount')::bigint<>3000*jsonb_array_length(q.body->'lines') OR v->>'operation_id' IS NULL
 OR v->>'purchase_id' IS NULL OR v->>'payment_id' IS NULL OR v->>'provider_order' IS NULL THEN RAISE EXCEPTION 'exact immutable quote required'; END IF;
 FOR line IN SELECT * FROM jsonb_array_elements(q.body->'lines') LOOP
 IF line->>'sku' IS DISTINCT FROM 'workspace-seat' OR (line->>'unit_price')::bigint IS DISTINCT FROM 3000 OR
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
CREATE FUNCTION seller_truth.workspace(token text,seatid text,note text DEFAULT NULL) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,seller_truth,pg_temp AS $$
DECLARE u seller_truth.identities; e seller_truth.entitlements; BEGIN
 SELECT * INTO u FROM seller_truth.identities WHERE token_hash=encode(public.digest(token,'sha256'),'hex');
 IF u.username IS NULL THEN RAISE EXCEPTION 'seller identity required' USING ERRCODE='42501'; END IF;
 SELECT * INTO e FROM seller_truth.entitlements WHERE id=seatid AND tenant_id=u.tenant_id AND beneficiary=u.beneficiary FOR SHARE;
 IF e.id IS NULL OR EXISTS(SELECT 1 FROM seller_truth.revocations WHERE entitlement_id=e.id) THEN
 RAISE EXCEPTION 'entitlement outside active beneficiary scope' USING ERRCODE='42501'; END IF;
 IF note IS NOT NULL THEN INSERT INTO seller_truth.entries(entitlement_id,text) VALUES(e.id,note); END IF;
 INSERT INTO seller_truth.events(tenant_id,order_id,kind,detail) VALUES(e.tenant_id,e.order_id,'WORKSPACE_ACCESSED',jsonb_build_object('beneficiary',u.beneficiary,'seat_id',e.id));
 RETURN jsonb_build_object('seat_id',e.id,'workspace_id',e.workspace_id,'beneficiary',e.beneficiary,'product',e.product,
 'entries',coalesce((SELECT jsonb_agg(jsonb_build_object('id',id,'text',text) ORDER BY created_at,id) FROM seller_truth.entries WHERE entitlement_id=e.id),'[]'::jsonb));
END $$;
-- Access revocation is a local seller administrative action, not a commercial refund.
-- No runtime role gets this function; retained history proves previously finalized delivery.
CREATE FUNCTION seller_truth.revoke(seatid text,why text) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,seller_truth,pg_temp AS $$
DECLARE e seller_truth.entitlements; BEGIN
 SELECT * INTO e FROM seller_truth.entitlements WHERE id=seatid FOR UPDATE;
 IF e.id IS NULL OR length(why)<1 THEN RAISE EXCEPTION 'exact entitlement and reason required'; END IF;
 INSERT INTO seller_truth.revocations VALUES(e.id,why,clock_timestamp()) ON CONFLICT DO NOTHING;
 INSERT INTO seller_truth.events(tenant_id,order_id,kind,detail) VALUES(e.tenant_id,e.order_id,'ACCESS_REVOKED',jsonb_build_object('seat_id',e.id,'reason',why));
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA seller_truth FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA seller_truth FROM PUBLIC;
DO $$ DECLARE item record; BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='interlock_seller_truth_owner') THEN CREATE ROLE interlock_seller_truth_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; END IF;
 GRANT USAGE ON SCHEMA public TO interlock_seller_truth_owner;
 GRANT SELECT,REFERENCES ON public.quotes,public.users TO interlock_seller_truth_owner;
 GRANT UPDATE(id) ON public.quotes TO interlock_seller_truth_owner;
 ALTER SCHEMA seller_truth OWNER TO interlock_seller_truth_owner;
 FOR item IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='seller_truth' AND c.relkind='r' LOOP
 EXECUTE format('ALTER TABLE seller_truth.%I OWNER TO interlock_seller_truth_owner',item.relname); END LOOP;
 FOR item IN SELECT p.oid::regprocedure AS name FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='seller_truth' LOOP
 EXECUTE format('ALTER FUNCTION %s OWNER TO interlock_seller_truth_owner',item.name); END LOOP;
 GRANT USAGE ON SCHEMA seller_truth TO interlock_seller;
 GRANT EXECUTE ON FUNCTION seller_truth.finalize(text,text),seller_truth.workspace(text,text,text) TO interlock_seller;
END $$;
