CREATE SCHEMA economic;
REVOKE ALL ON SCHEMA economic FROM PUBLIC;
CREATE TABLE economic.channels (
 id text PRIMARY KEY, provider text NOT NULL CHECK(provider IN ('REFERENCE_SYNTHETIC','RAZORPAY_TEST','CONTROLLED_SELLER')),
 account_id text NOT NULL, integration text NOT NULL, tenant_id text,
 source text NOT NULL CHECK(source IN ('WEBHOOK','CANONICAL_READ','SELLER_ATTESTATION')),
 secret text NOT NULL CHECK(length(secret)>=16), active boolean NOT NULL DEFAULT true,
 UNIQUE(provider,account_id,integration,source)
);
CREATE TABLE economic.receipts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), channel_id text NOT NULL REFERENCES economic.channels(id),
 body bytea NOT NULL CHECK(octet_length(body)<=257000), body_hash text NOT NULL,
 headers jsonb NOT NULL, delivery_id text NOT NULL CHECK(length(delivery_id)<=150),
 received_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE economic.receipt_states (
 receipt_id uuid PRIMARY KEY REFERENCES economic.receipts(id),
 authentication text NOT NULL DEFAULT 'PENDING' CHECK(authentication IN ('PENDING','AUTHENTICATED','REJECTED')),
 parsing text NOT NULL DEFAULT 'PENDING', classification text NOT NULL DEFAULT 'RECEIVED',
 observation_id uuid, interpreted boolean NOT NULL DEFAULT false, updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE economic.observations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), receipt_id uuid NOT NULL REFERENCES economic.receipts(id),
 channel_id text NOT NULL REFERENCES economic.channels(id), provider text NOT NULL, account_id text NOT NULL,
 integration text NOT NULL, tenant_id text, operation_id text, purchase_id text,
 object_id text NOT NULL, order_id text, kind text NOT NULL CHECK(kind IN ('AUTHORIZED','CAPTURED','FAILED','FULFILLED','REVOKED')),
 amount bigint NOT NULL CHECK(amount>0), currency text NOT NULL CHECK(currency='INR'),
 strength int NOT NULL, payload jsonb NOT NULL, semantic_hash text NOT NULL,
 authentication_basis text NOT NULL, observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(channel_id,semantic_hash)
);
CREATE TABLE economic.object_heads (
 provider text NOT NULL, account_id text NOT NULL, object_id text NOT NULL,
 observation_id uuid NOT NULL REFERENCES economic.observations(id), strength int NOT NULL,
 blocked boolean NOT NULL DEFAULT false, PRIMARY KEY(provider,account_id,object_id)
);
CREATE TABLE economic.conflicts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), receipt_id uuid NOT NULL REFERENCES economic.receipts(id),
 observation_id uuid REFERENCES economic.observations(id), reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(receipt_id,reason)
);
CREATE TABLE economic.facts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), kind text NOT NULL CHECK(kind IN ('CAPTURE','ORDER_LIABILITY','SELLER_FULFILLED','REVENUE')),
 provider text NOT NULL, account_id text NOT NULL, integration text NOT NULL,
 tenant_id text, operation_id text, purchase_id text, effect_id text NOT NULL,
 amount bigint NOT NULL CHECK(amount>0), currency text NOT NULL CHECK(currency='INR'),
 observation_id uuid NOT NULL REFERENCES economic.observations(id),
 parent_fact_id uuid REFERENCES economic.facts(id), fulfillment_fact_id uuid REFERENCES economic.facts(id),
 derivation_version text NOT NULL DEFAULT 'purchase-v1' CHECK(derivation_version='purchase-v1'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(provider,account_id,effect_id,kind),
 CHECK(kind='CAPTURE' OR (tenant_id IS NOT NULL AND operation_id IS NOT NULL AND purchase_id IS NOT NULL)),
 CHECK((kind='CAPTURE' AND parent_fact_id IS NULL) OR (kind<>'CAPTURE' AND parent_fact_id IS NOT NULL)),
 CHECK((kind='REVENUE')=(fulfillment_fact_id IS NOT NULL))
);
CREATE UNIQUE INDEX one_purchase_capture ON economic.facts(operation_id) WHERE kind='CAPTURE' AND operation_id IS NOT NULL;
CREATE UNIQUE INDEX one_purchase_recognition ON economic.facts(operation_id,kind) WHERE kind<>'CAPTURE';
CREATE TABLE economic.accounts(name text PRIMARY KEY);
INSERT INTO economic.accounts VALUES ('ProviderClearing'),('CustomerOrderLiability'),('SalesRevenue'),('SalesReturns'),('RefundPayable'),('UnmatchedProviderSuspense');
CREATE TABLE economic.postings (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), fact_id uuid NOT NULL UNIQUE REFERENCES economic.facts(id),
 template text NOT NULL CHECK(template IN ('CAPTURE_TO_SUSPENSE','MATCH_TO_LIABILITY','FULFILLMENT_TO_REVENUE')),
 template_version int NOT NULL DEFAULT 1 CHECK(template_version=1),
 provider text NOT NULL, account_id text NOT NULL, tenant_id text, amount bigint NOT NULL CHECK(amount>0),
 currency text NOT NULL CHECK(currency='INR'), created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE economic.lines (
 posting_id uuid NOT NULL REFERENCES economic.postings(id), ordinal int NOT NULL CHECK(ordinal IN (1,2)),
 account text NOT NULL REFERENCES economic.accounts(name), side text NOT NULL CHECK(side IN ('DEBIT','CREDIT')),
 amount bigint NOT NULL CHECK(amount>0), currency text NOT NULL CHECK(currency='INR'), PRIMARY KEY(posting_id,ordinal)
);
CREATE FUNCTION economic.immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION 'immutable economic provenance' USING ERRCODE='42501'; END $$;
DO $$ DECLARE t text; BEGIN FOREACH t IN ARRAY ARRAY['receipts','observations','conflicts','facts','postings','lines','accounts'] LOOP
 EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON economic.%I FOR EACH ROW EXECUTE FUNCTION economic.immutable()',t);
END LOOP; END $$;

CREATE FUNCTION economic.receive(channel text,raw bytea,headers jsonb,delivery text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,economic,pg_temp AS $$
DECLARE rid uuid; key text; BEGIN
 IF NOT EXISTS(SELECT 1 FROM economic.channels WHERE id=channel AND active) OR jsonb_typeof(headers)<>'object' THEN
 RAISE EXCEPTION 'unknown evidence endpoint' USING ERRCODE='42501'; END IF;
 IF octet_length(raw)>256000 THEN RAISE EXCEPTION 'raw envelope exceeds limit'; END IF;
 SELECT secret INTO key FROM economic.channels WHERE id=channel;
 INSERT INTO economic.receipts(channel_id,body,body_hash,headers,delivery_id)
 VALUES(channel,public.pgp_sym_encrypt_bytea(raw,key,'cipher-algo=aes256,compress-algo=0'),encode(public.digest(raw,'sha256'),'hex'),jsonb_build_object('signature',headers->>'signature'),delivery)
 RETURNING id INTO rid;
 INSERT INTO economic.receipt_states(receipt_id) VALUES(rid); RETURN rid;
END $$;

CREATE FUNCTION economic.authenticate(rid uuid) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,economic,pg_temp AS $$
DECLARE r economic.receipts; c economic.channels; s economic.receipt_states; BEGIN
 SELECT * INTO s FROM economic.receipt_states WHERE receipt_id=rid FOR UPDATE;
 IF s.receipt_id IS NULL THEN RAISE EXCEPTION 'receipt missing'; END IF;
 IF s.authentication<>'PENDING' THEN RETURN s.authentication; END IF;
 SELECT * INTO r FROM economic.receipts WHERE id=rid;
 SELECT * INTO c FROM economic.channels WHERE id=r.channel_id;
 IF c.active AND r.headers->>'signature'=encode(public.hmac(public.pgp_sym_decrypt_bytea(r.body,c.secret),convert_to(c.secret,'UTF8'),'sha256'),'hex') THEN
 UPDATE economic.receipt_states SET authentication='AUTHENTICATED',classification='AUTHENTICATED',updated_at=clock_timestamp() WHERE receipt_id=rid;
 RETURN 'AUTHENTICATED'; END IF;
 UPDATE economic.receipt_states SET authentication='REJECTED',classification='BAD_SIGNATURE',updated_at=clock_timestamp() WHERE receipt_id=rid;
 RETURN 'REJECTED';
END $$;

CREATE FUNCTION economic.observe(rid uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,economic,pg_temp AS $$
DECLARE r economic.receipts; c economic.channels; s economic.receipt_states; v jsonb; p jsonb;
 o dispatch.business_operations; h economic.object_heads; old economic.observations; oid uuid;
 k text; str int; amt bigint; cur text; obj text; ord text; sem text; reason text;
BEGIN
 SELECT * INTO s FROM economic.receipt_states WHERE receipt_id=rid FOR UPDATE;
 IF s.authentication IS DISTINCT FROM 'AUTHENTICATED' THEN RETURN NULL; END IF;
 IF s.parsing<>'PENDING' THEN RETURN s.observation_id; END IF;
 SELECT * INTO r FROM economic.receipts WHERE id=rid;
 SELECT * INTO c FROM economic.channels WHERE id=r.channel_id;
 PERFORM pg_advisory_xact_lock(hashtextextended(c.id||':'||r.delivery_id,0));
 IF EXISTS(SELECT 1 FROM economic.receipts x JOIN economic.receipt_states st ON st.receipt_id=x.id
 WHERE x.channel_id=r.channel_id AND x.delivery_id=r.delivery_id AND x.body_hash<>r.body_hash
 AND st.authentication='AUTHENTICATED' AND st.parsing='PARSED') THEN reason:='EVENT_ID_CONFLICT'; END IF;
 BEGIN
 v:=convert_from(public.pgp_sym_decrypt_bytea(r.body,c.secret),'UTF8')::jsonb;
 IF jsonb_typeof(v)<>'object' THEN RAISE EXCEPTION 'schema'; END IF;
 IF c.provider='CONTROLLED_SELLER' THEN
 p:=v; obj:=p->>'order_id'; ord:=p->>'provider_order'; k:=p->>'state'; str:=3;
 ELSE
 p:=v->'payload'->'payment'->'entity'; obj:=p->>'id'; ord:=p->>'order_id';
 k:=CASE p->>'status' WHEN 'authorized' THEN 'AUTHORIZED' WHEN 'captured' THEN 'CAPTURED'
 WHEN 'refunded' THEN 'CAPTURED' WHEN 'failed' THEN 'FAILED' ELSE NULL END;
 str:=CASE k WHEN 'CAPTURED' THEN 2 ELSE 1 END;
 IF k='CAPTURED' AND p->>'captured' IS DISTINCT FROM 'true' THEN RAISE EXCEPTION 'capture flag'; END IF;
 END IF;
 amt:=(p->>'amount')::bigint; cur:=p->>'currency';
 IF amt IS NULL OR amt<=0 OR cur IS DISTINCT FROM 'INR' OR obj IS NULL OR ord IS NULL OR k IS NULL
 OR (c.provider<>'CONTROLLED_SELLER' AND (obj!~'^pay_[A-Za-z0-9]+$' OR ord!~'^order_[A-Za-z0-9]+$'))
 OR (c.provider='CONTROLLED_SELLER' AND k NOT IN ('FULFILLED','REVOKED')) THEN RAISE EXCEPTION 'schema'; END IF;
 EXCEPTION WHEN OTHERS THEN reason:='MALFORMED_OR_UNSUPPORTED'; END;
 IF reason IS NULL AND (v->>'account_id' IS DISTINCT FROM c.account_id OR
 (c.provider<>'RAZORPAY_TEST' AND (v->>'integration' IS DISTINCT FROM c.integration OR v->>'tenant_id' IS DISTINCT FROM c.tenant_id))) THEN
 reason:='ENDPOINT_SCOPE_MISMATCH'; END IF;
 IF reason IS NULL THEN
 SELECT b.* INTO o FROM dispatch.business_operations b JOIN dispatch.operation_runtime rt ON rt.operation_id=b.id
 WHERE rt.provider_order=ord AND b.account_id=c.account_id AND b.integration=c.integration
 AND (c.provider='CONTROLLED_SELLER' OR b.adapter=c.provider);
 IF o.id IS NOT NULL THEN
 IF o.tenant_id IS DISTINCT FROM c.tenant_id OR amt<>o.amount OR cur<>o.currency
 OR (c.provider<>'RAZORPAY_TEST' AND v->>'operation_id' IS DISTINCT FROM o.id) THEN reason:='EXACT_PURCHASE_MISMATCH'; END IF;
 IF c.provider='CONTROLLED_SELLER' AND (p->>'purchase_id' IS DISTINCT FROM o.domain_root OR
 p->>'operation_id' IS DISTINCT FROM o.id OR p->>'quote_id' IS DISTINCT FROM
 (SELECT quote_id FROM authority.evidence_revisions WHERE id=o.evidence_id)) THEN reason:='SELLER_CORRELATION_MISMATCH'; END IF;
 ELSIF c.provider='CONTROLLED_SELLER' THEN reason:='SELLER_UNMATCHED';
 ELSIF EXISTS(SELECT 1 FROM dispatch.operation_runtime WHERE provider_order=ord) THEN reason:='PROVIDER_OBJECT_SUBSTITUTION';
 END IF;
 END IF;
 IF reason IS NOT NULL THEN
 INSERT INTO economic.conflicts(receipt_id,reason) VALUES(rid,reason) ON CONFLICT DO NOTHING;
 UPDATE economic.receipt_states SET parsing='QUARANTINED',classification=reason WHERE receipt_id=rid; RETURN NULL; END IF;
 -- Arrival order is not truth. Serialize the exact provider effect across channels.
 PERFORM pg_advisory_xact_lock(hashtextextended(c.provider||':'||c.account_id||':'||obj,0));
 sem:=encode(public.digest(jsonb_build_object('object',obj,'order',ord,'kind',k,'amount',amt,'currency',cur,
 'operation',o.id,'seller_seats',CASE WHEN c.provider='CONTROLLED_SELLER' THEN p->'seats' ELSE NULL END)::text,'sha256'),'hex');
 SELECT id INTO oid FROM economic.observations WHERE channel_id=c.id AND semantic_hash=sem;
 IF oid IS NULL THEN
 INSERT INTO economic.observations(receipt_id,channel_id,provider,account_id,integration,tenant_id,operation_id,purchase_id,
 object_id,order_id,kind,amount,currency,strength,payload,semantic_hash,authentication_basis)
 VALUES(rid,c.id,c.provider,c.account_id,c.integration,o.tenant_id,o.id,o.domain_root,obj,ord,k,amt,cur,str,v,sem,
 CASE c.source WHEN 'CANONICAL_READ' THEN 'BROKER_AUTHENTICATED_CANONICAL_READ' ELSE 'RAW_BODY_HMAC_SHA256' END) RETURNING id INTO oid;
 END IF;
 SELECT * INTO h FROM economic.object_heads WHERE provider=c.provider AND account_id=c.account_id AND object_id=obj FOR UPDATE;
 IF h.observation_id IS NOT NULL THEN
 SELECT * INTO old FROM economic.observations WHERE id=h.observation_id;
 IF old.amount<>amt OR old.currency<>cur OR old.order_id<>ord OR old.operation_id IS DISTINCT FROM o.id
 OR (old.kind IN ('CAPTURED','FULFILLED') AND k IN ('FAILED','REVOKED')) OR (old.kind='FAILED' AND k='CAPTURED') THEN
 UPDATE economic.object_heads SET blocked=true WHERE provider=c.provider AND account_id=c.account_id AND object_id=obj;
 INSERT INTO economic.conflicts(receipt_id,observation_id,reason) VALUES(rid,oid,'CONTRADICTORY_OBJECT') ON CONFLICT DO NOTHING;
 reason:='CONTRADICTORY_OBJECT';
 ELSIF str<h.strength THEN reason:='STALE_WEAKER'; END IF;
 END IF;
 IF h.observation_id IS NULL THEN
 INSERT INTO economic.object_heads VALUES(c.provider,c.account_id,obj,oid,str,false);
 ELSIF reason IS NULL AND NOT h.blocked THEN
 UPDATE economic.object_heads SET observation_id=oid,strength=str WHERE provider=c.provider AND account_id=c.account_id AND object_id=obj;
 END IF;
 UPDATE economic.receipt_states SET parsing='PARSED',observation_id=oid,
 classification=coalesce(reason,CASE WHEN c.provider='RAZORPAY_TEST' AND c.source='WEBHOOK' THEN 'CANONICAL_READ_REQUIRED'
 WHEN o.id IS NULL THEN 'UNMATCHED' ELSE 'MATCHED' END),updated_at=clock_timestamp() WHERE receipt_id=rid;
 RETURN oid;
END $$;

CREATE FUNCTION economic.derive(oid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,economic,pg_temp AS $$
DECLARE ob economic.observations; c economic.channels; cap economic.facts; liab economic.facts;
 sf economic.facts; o dispatch.business_operations; q authority.seller_quotes; seats jsonb;
BEGIN
 SELECT * INTO ob FROM economic.observations WHERE id=oid;
 IF ob.id IS NULL THEN RETURN '[]'::jsonb; END IF;
 UPDATE economic.receipt_states SET interpreted=true WHERE observation_id=oid;
 SELECT * INTO c FROM economic.channels WHERE id=ob.channel_id;
 PERFORM pg_advisory_xact_lock(hashtextextended(ob.provider||':'||ob.account_id||':'||ob.object_id,0));
 IF EXISTS(SELECT 1 FROM economic.object_heads WHERE provider=ob.provider AND account_id=ob.account_id
 AND object_id=ob.object_id AND blocked) OR NOT c.active THEN RETURN '[]'::jsonb; END IF;
 IF ob.operation_id IS NOT NULL THEN PERFORM pg_advisory_xact_lock(hashtextextended('purchase:'||ob.operation_id,0)); END IF;
 IF ob.kind='CAPTURED' AND NOT(c.provider='RAZORPAY_TEST' AND c.source='WEBHOOK') THEN
 IF EXISTS(SELECT 1 FROM economic.facts WHERE kind='CAPTURE' AND operation_id=ob.operation_id AND effect_id<>ob.object_id) THEN
 INSERT INTO economic.conflicts(receipt_id,observation_id,reason) VALUES(ob.receipt_id,oid,'MULTIPLE_CAPTURE_UNSUPPORTED') ON CONFLICT DO NOTHING;
 RETURN '[]'::jsonb; END IF;
 INSERT INTO economic.facts(kind,provider,account_id,integration,tenant_id,operation_id,purchase_id,effect_id,amount,currency,observation_id)
 VALUES('CAPTURE',ob.provider,ob.account_id,ob.integration,ob.tenant_id,ob.operation_id,ob.purchase_id,ob.object_id,ob.amount,ob.currency,oid)
 ON CONFLICT DO NOTHING;
 SELECT * INTO cap FROM economic.facts WHERE provider=ob.provider AND account_id=ob.account_id AND effect_id=ob.object_id AND kind='CAPTURE';
 IF cap.amount<>ob.amount OR cap.operation_id IS DISTINCT FROM ob.operation_id THEN RAISE EXCEPTION 'immutable effect conflict'; END IF;
 IF ob.operation_id IS NOT NULL THEN
 INSERT INTO economic.facts(kind,provider,account_id,integration,tenant_id,operation_id,purchase_id,effect_id,amount,currency,observation_id,parent_fact_id)
 VALUES('ORDER_LIABILITY',cap.provider,cap.account_id,cap.integration,cap.tenant_id,cap.operation_id,cap.purchase_id,cap.effect_id,cap.amount,cap.currency,oid,cap.id)
 ON CONFLICT DO NOTHING; END IF;
 ELSIF ob.kind='FULFILLED' THEN
 SELECT * INTO cap FROM economic.facts WHERE operation_id=ob.operation_id AND kind='CAPTURE';
 SELECT * INTO o FROM dispatch.business_operations WHERE id=ob.operation_id;
 SELECT sq.* INTO q FROM authority.seller_quotes sq JOIN authority.evidence_revisions e ON e.quote_id=sq.id WHERE e.id=o.evidence_id;
 SELECT jsonb_agg(jsonb_build_object('id',x->>'allocation_id','beneficiary',x->>'beneficiary') ORDER BY x->>'allocation_id')
 INTO seats FROM jsonb_array_elements(q.body->'lines') x;
 IF cap.id IS NULL OR ob.amount<>cap.amount OR ob.currency<>cap.currency OR
 ob.payload->>'capture_fact_id' IS DISTINCT FROM cap.id::text OR ob.payload->>'payment_id' IS DISTINCT FROM cap.effect_id OR
 ob.payload->'seats' IS DISTINCT FROM seats OR ob.payload->>'terms_digest' IS DISTINCT FROM q.body_digest THEN RETURN '[]'::jsonb; END IF;
 INSERT INTO economic.facts(kind,provider,account_id,integration,tenant_id,operation_id,purchase_id,effect_id,amount,currency,observation_id,parent_fact_id)
 VALUES('SELLER_FULFILLED',cap.provider,cap.account_id,cap.integration,cap.tenant_id,cap.operation_id,cap.purchase_id,cap.effect_id,cap.amount,cap.currency,oid,cap.id)
 ON CONFLICT DO NOTHING;
 END IF;
 SELECT * INTO liab FROM economic.facts WHERE operation_id=ob.operation_id AND kind='ORDER_LIABILITY';
 SELECT * INTO sf FROM economic.facts WHERE operation_id=ob.operation_id AND kind='SELLER_FULFILLED';
 IF liab.id IS NOT NULL AND sf.id IS NOT NULL THEN
 INSERT INTO economic.facts(kind,provider,account_id,integration,tenant_id,operation_id,purchase_id,effect_id,amount,currency,observation_id,parent_fact_id,fulfillment_fact_id)
 VALUES('REVENUE',liab.provider,liab.account_id,liab.integration,liab.tenant_id,liab.operation_id,liab.purchase_id,liab.effect_id,
 liab.amount,liab.currency,sf.observation_id,liab.id,sf.id) ON CONFLICT DO NOTHING; END IF;
 RETURN coalesce((SELECT jsonb_agg(id ORDER BY CASE kind WHEN 'CAPTURE' THEN 1 WHEN 'ORDER_LIABILITY' THEN 2 WHEN 'SELLER_FULFILLED' THEN 3 ELSE 4 END)
 FROM economic.facts WHERE (ob.operation_id IS NOT NULL AND operation_id=ob.operation_id) OR
 (ob.operation_id IS NULL AND provider=ob.provider AND account_id=ob.account_id AND effect_id=ob.object_id)),'[]'::jsonb);
END $$;

CREATE FUNCTION economic.post(fid uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,economic,pg_temp AS $$
DECLARE f economic.facts; pid uuid; tmpl text; dr text; cr text; BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('fact:'||fid::text,0));
 SELECT id INTO pid FROM economic.postings WHERE fact_id=fid; IF pid IS NOT NULL THEN RETURN pid; END IF;
 SELECT * INTO f FROM economic.facts WHERE id=fid;
 IF f.id IS NULL THEN RAISE EXCEPTION 'typed fact required'; END IF;
 IF f.kind='SELLER_FULFILLED' THEN RETURN NULL; END IF;
 IF f.parent_fact_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM economic.postings WHERE fact_id=f.parent_fact_id) THEN
 RAISE EXCEPTION 'parent posting required'; END IF;
 CASE f.kind
 WHEN 'CAPTURE' THEN tmpl:='CAPTURE_TO_SUSPENSE'; dr:='ProviderClearing'; cr:='UnmatchedProviderSuspense';
 WHEN 'ORDER_LIABILITY' THEN tmpl:='MATCH_TO_LIABILITY'; dr:='UnmatchedProviderSuspense'; cr:='CustomerOrderLiability';
 WHEN 'REVENUE' THEN tmpl:='FULFILLMENT_TO_REVENUE'; dr:='CustomerOrderLiability'; cr:='SalesRevenue';
 ELSE RAISE EXCEPTION 'unsupported fact'; END CASE;
 INSERT INTO economic.postings(fact_id,template,provider,account_id,tenant_id,amount,currency)
 VALUES(f.id,tmpl,f.provider,f.account_id,f.tenant_id,f.amount,f.currency) RETURNING id INTO pid;
 INSERT INTO economic.lines VALUES(pid,1,dr,'DEBIT',f.amount,f.currency),(pid,2,cr,'CREDIT',f.amount,f.currency);
 RETURN pid;
END $$;
CREATE FUNCTION economic.journal_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,economic,pg_temp AS $$
DECLARE p economic.postings; f economic.facts; actual jsonb; expected jsonb; BEGIN
 SELECT * INTO p FROM economic.postings WHERE id=NEW.id;
 SELECT * INTO f FROM economic.facts WHERE id=p.fact_id;
 expected:=CASE f.kind WHEN 'CAPTURE' THEN '["ProviderClearing","UnmatchedProviderSuspense"]'::jsonb
 WHEN 'ORDER_LIABILITY' THEN '["UnmatchedProviderSuspense","CustomerOrderLiability"]'::jsonb
 WHEN 'REVENUE' THEN '["CustomerOrderLiability","SalesRevenue"]'::jsonb ELSE NULL END;
 SELECT jsonb_agg(account ORDER BY ordinal) INTO actual FROM economic.lines WHERE posting_id=p.id;
 IF actual IS DISTINCT FROM expected OR p.amount<>f.amount OR p.currency<>f.currency OR
 p.provider<>f.provider OR p.account_id<>f.account_id OR p.tenant_id IS DISTINCT FROM f.tenant_id OR
 EXISTS(SELECT 1 FROM economic.lines WHERE posting_id=p.id AND (amount<>f.amount OR currency<>f.currency OR
 side<>CASE ordinal WHEN 1 THEN 'DEBIT' ELSE 'CREDIT' END)) THEN RAISE EXCEPTION 'journal semantic bundle invalid'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER journal_bundle AFTER INSERT ON economic.postings DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION economic.journal_guard();

CREATE FUNCTION economic.capability(opid text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,economic,pg_temp AS $$
DECLARE cap economic.facts; o dispatch.business_operations; q authority.seller_quotes; key text; raw text; BEGIN
 SELECT * INTO cap FROM economic.facts WHERE operation_id=opid AND kind='CAPTURE';
 IF cap.id IS NULL OR EXISTS(SELECT 1 FROM economic.object_heads WHERE provider=cap.provider AND account_id=cap.account_id AND object_id=cap.effect_id AND blocked)
 THEN RAISE EXCEPTION 'matched unblocked capture required'; END IF;
 SELECT * INTO o FROM dispatch.business_operations WHERE id=opid;
 SELECT sq.* INTO q FROM authority.seller_quotes sq JOIN authority.evidence_revisions e ON e.quote_id=sq.id WHERE e.id=o.evidence_id;
 SELECT secret INTO key FROM economic.channels WHERE id='seller-capability-key' AND active;
 IF key IS NULL THEN RAISE EXCEPTION 'seller capability key missing'; END IF;
 raw:=dispatch.canonical(jsonb_build_object('tenant_id',cap.tenant_id,'account_id',cap.account_id,'integration',cap.integration,
 'operation_id',opid,'purchase_id',cap.purchase_id,'capture_fact_id',cap.id,'payment_id',cap.effect_id,
 'provider_order',(SELECT provider_order FROM dispatch.operation_runtime WHERE operation_id=opid),
 'quote_id',q.id,'terms_digest',q.body_digest,'amount',cap.amount,'currency',cap.currency,'quote',q.body));
 RETURN jsonb_build_object('raw',raw,'signature',encode(public.hmac(raw,key,'sha256'),'hex'));
END $$;

CREATE FUNCTION economic.inspect(token text,opid text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,economic,pg_temp AS $$
DECLARE o dispatch.business_operations; BEGIN
 o:=dispatch.user_operation(token,opid,'VIEW');
 RETURN jsonb_build_object('operation_id',o.id,'purchase_id',o.domain_root,'tenant_id',o.tenant_id,'provider',o.adapter,
 'amount',o.amount,'currency',o.currency,'intended',jsonb_build_object('action_id',o.action_id,'evidence_id',o.evidence_id,'quote_id',
 (SELECT quote_id FROM authority.evidence_revisions WHERE id=o.evidence_id)),
 'observations',coalesce((SELECT jsonb_agg(to_jsonb(x)-'payload' ORDER BY observed_at,id) FROM economic.observations x WHERE operation_id=o.id),'[]'::jsonb),
 'facts',coalesce((SELECT jsonb_agg(to_jsonb(x) ORDER BY created_at,id) FROM economic.facts x WHERE operation_id=o.id),'[]'::jsonb),
 'postings',coalesce((SELECT jsonb_agg(to_jsonb(x)||jsonb_build_object('lines',(SELECT jsonb_agg(to_jsonb(l) ORDER BY ordinal) FROM economic.lines l WHERE posting_id=x.id))
 ORDER BY x.created_at,x.id) FROM economic.postings x JOIN economic.facts f ON f.id=x.fact_id WHERE f.operation_id=o.id),'[]'::jsonb),
 'unresolved',coalesce((SELECT jsonb_agg(jsonb_build_object('reason',c.reason,'receipt_id',c.receipt_id)) FROM economic.conflicts c
 JOIN economic.observations ob ON ob.id=c.observation_id WHERE ob.operation_id=o.id),'[]'::jsonb),
 'seller_entitlement',(SELECT jsonb_build_object('order_id',ob.payload->>'order_id','workspace_id',ob.payload->>'workspace_id',
 'state','FINALIZED_DELIVERY','seats',ob.payload->'seats','capture_fact_id',ob.payload->>'capture_fact_id')
 FROM economic.facts sf JOIN economic.observations ob ON ob.id=sf.observation_id WHERE sf.operation_id=o.id AND sf.kind='SELLER_FULFILLED'));
END $$;

CREATE FUNCTION economic.read_binding(secret text,opid text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,economic,pg_temp AS $$
DECLARE o dispatch.business_operations; BEGIN
 SELECT * INTO o FROM dispatch.business_operations WHERE id=opid;
 IF o.id IS NULL THEN RAISE EXCEPTION 'operation missing' USING ERRCODE='42501'; END IF;
 PERFORM dispatch.service_actor(secret,o.tenant_id,'OBSERVE');
 RETURN jsonb_build_object('operation_id',o.id,'tenant_id',o.tenant_id,'account_id',o.account_id,'integration',o.integration,
 'provider',o.adapter,'amount',o.amount,'currency',o.currency,'receipt',o.operation_key,
 'provider_order',(SELECT provider_order FROM dispatch.operation_runtime WHERE operation_id=o.id));
END $$;

CREATE FUNCTION economic.pending() RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,economic,pg_temp AS $$
 SELECT jsonb_build_object(
 'receipts',coalesce((SELECT jsonb_agg(receipt_id ORDER BY r.received_at) FROM economic.receipt_states s JOIN economic.receipts r ON r.id=s.receipt_id
 WHERE authentication='PENDING' OR (authentication='AUTHENTICATED' AND (parsing='PENDING' OR (parsing='PARSED' AND NOT interpreted)))),'[]'::jsonb),
 'facts',coalesce((SELECT jsonb_agg(f.id ORDER BY CASE f.kind WHEN 'CAPTURE' THEN 1 WHEN 'ORDER_LIABILITY' THEN 2 ELSE 3 END,f.created_at,f.id)
 FROM economic.facts f WHERE f.kind<>'SELLER_FULFILLED' AND NOT EXISTS(SELECT 1 FROM economic.postings p WHERE p.fact_id=f.id)),'[]'::jsonb),
 'fulfillments',coalesce((SELECT jsonb_agg(f.operation_id ORDER BY f.created_at) FROM economic.facts f WHERE f.kind='ORDER_LIABILITY'
 AND NOT EXISTS(SELECT 1 FROM economic.facts sf WHERE sf.operation_id=f.operation_id AND sf.kind='SELLER_FULFILLED')
 AND NOT EXISTS(SELECT 1 FROM economic.object_heads h WHERE h.provider=f.provider AND h.account_id=f.account_id AND h.object_id=f.effect_id AND h.blocked)),'[]'::jsonb))
$$;
REVOKE ALL ON ALL TABLES IN SCHEMA economic FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA economic FROM PUBLIC;
DO $$ DECLARE item record; BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='interlock_economic_owner') THEN CREATE ROLE interlock_economic_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='interlock_economic') THEN CREATE ROLE interlock_economic LOGIN PASSWORD 'local_only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; END IF;
 GRANT USAGE ON SCHEMA public,authority,dispatch TO interlock_economic_owner;
 GRANT SELECT ON dispatch.business_operations,dispatch.operation_runtime,authority.evidence_revisions,authority.seller_quotes TO interlock_economic_owner;
 GRANT EXECUTE ON FUNCTION dispatch.canonical(jsonb),dispatch.user_operation(text,text,text),dispatch.service_actor(text,text,text) TO interlock_economic_owner;
 ALTER SCHEMA economic OWNER TO interlock_economic_owner;
 FOR item IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='economic' AND c.relkind='r' LOOP
 EXECUTE format('ALTER TABLE economic.%I OWNER TO interlock_economic_owner',item.relname); END LOOP;
 FOR item IN SELECT p.oid::regprocedure AS name FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='economic' LOOP
 EXECUTE format('ALTER FUNCTION %s OWNER TO interlock_economic_owner',item.name); END LOOP;
 GRANT USAGE ON SCHEMA economic TO interlock_api,interlock_broker,interlock_economic;
 GRANT EXECUTE ON FUNCTION economic.receive(text,bytea,jsonb,text) TO interlock_api,interlock_broker,interlock_economic;
 GRANT EXECUTE ON FUNCTION economic.authenticate(uuid),economic.observe(uuid),economic.derive(uuid),economic.post(uuid),economic.capability(text),economic.pending() TO interlock_economic;
 GRANT EXECUTE ON FUNCTION economic.inspect(text,text) TO interlock_api;
 GRANT EXECUTE ON FUNCTION economic.read_binding(text,text) TO interlock_broker;
END $$;
