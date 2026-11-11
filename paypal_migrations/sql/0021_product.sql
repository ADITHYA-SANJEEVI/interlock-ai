CREATE TABLE paypal_v1.proposals (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id text NOT NULL REFERENCES paypal_v1.tenants(tenant_id),
 principal text NOT NULL REFERENCES authority.memberships(principal), grant_id text NOT NULL REFERENCES authority.grants(id),
 idempotency_key text NOT NULL, intent_sha256 text NOT NULL CHECK(intent_sha256 ~ '^[a-f0-9]{64}$'),
 result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,principal,idempotency_key)
);
CREATE TABLE paypal_v1.proposal_quotes (
 quote_id text PRIMARY KEY REFERENCES authority.seller_quotes(id), proposal_id uuid NOT NULL REFERENCES paypal_v1.proposals(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER immutable_proposal BEFORE UPDATE OR DELETE ON paypal_v1.proposals
 FOR EACH ROW EXECUTE FUNCTION authority.immutable();
CREATE TRIGGER immutable_proposal_quote BEFORE UPDATE OR DELETE ON paypal_v1.proposal_quotes
 FOR EACH ROW EXECUTE FUNCTION authority.immutable();

CREATE FUNCTION paypal_v1.snapshot(token text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE person authority.memberships; BEGIN
 person:=authority.actor(token);
 IF NOT EXISTS(SELECT 1 FROM paypal_v1.tenants WHERE tenant_id=person.tenant_id) THEN
  PERFORM authority.fail('USD_TENANT_REQUIRED',403); END IF;
 RETURN authority.snapshot(token)||jsonb_build_object('currency','USD','contract_version','paypal-v1');
END $$;
CREATE FUNCTION paypal_v1.action_detail(token text,actionid text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE person authority.memberships; BEGIN
 person:=authority.actor(token);
 IF NOT EXISTS(SELECT 1 FROM authority.action_revisions a JOIN paypal_v1.tenants t ON t.tenant_id=a.tenant_id
  WHERE a.id=actionid AND a.tenant_id=person.tenant_id AND a.currency='USD') THEN
  PERFORM authority.fail('USD_ACTION_NOT_VISIBLE',403); END IF;
 RETURN authority.action_detail(token,actionid)||jsonb_build_object('currency','USD','contract_version','paypal-v1');
END $$;

CREATE FUNCTION paypal_v1.proposal_scope(token text,grantid text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE person authority.memberships; result jsonb; BEGIN
 person:=authority.actor(token); PERFORM authority.require_role(person,'PURCHASE_REQUESTER');
 SELECT jsonb_build_object('tenant_id',g.tenant_id,'grant_id',g.id,'currency',g.currency,'beneficiaries',gv.beneficiaries)
 INTO result FROM authority.grants g JOIN authority.grant_heads h ON h.grant_id=g.id
 JOIN authority.grant_versions gv ON gv.id=h.version_id JOIN paypal_v1.tenants t ON t.tenant_id=g.tenant_id
 WHERE g.id=grantid AND g.tenant_id=person.tenant_id AND g.subject=person.principal AND g.currency='USD'
 AND g.kind='PURCHASE' AND h.status='ACTIVE' AND gv.expires_at>clock_timestamp();
 IF result IS NULL THEN PERFORM authority.fail('USD_PROPOSAL_SCOPE_DENIED',403); END IF;
 RETURN result;
END $$;
CREATE FUNCTION paypal_v1.proposal(token text,pid uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE person authority.memberships; p paypal_v1.proposals; BEGIN
 person:=authority.actor(token);
 SELECT * INTO p FROM paypal_v1.proposals WHERE id=pid AND principal=person.principal AND tenant_id=person.tenant_id;
 IF p.id IS NULL THEN PERFORM authority.fail('PROPOSAL_NOT_VISIBLE',403); END IF;
 PERFORM paypal_v1.proposal_scope(token,p.grant_id);
 RETURN jsonb_build_object('proposal_id',p.id,'grant_id',p.grant_id,'tenant_id',p.tenant_id,
 'intent_sha256',p.intent_sha256,'result',p.result,'financial_authority',false);
END $$;
CREATE FUNCTION paypal_v1.recover_proposal(token text,grantid text,idem text,intent_hash text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE person authority.memberships; p paypal_v1.proposals; BEGIN
 person:=authority.actor(token); PERFORM paypal_v1.proposal_scope(token,grantid);
 SELECT * INTO p FROM paypal_v1.proposals WHERE tenant_id=person.tenant_id AND principal=person.principal AND idempotency_key=idem;
 IF p.id IS NULL THEN RETURN NULL; END IF;
 IF p.grant_id<>grantid OR p.intent_sha256<>intent_hash THEN PERFORM authority.fail('IDEMPOTENCY_CONFLICT'); END IF;
 RETURN paypal_v1.proposal(token,p.id);
END $$;
CREATE FUNCTION paypal_v1.record_proposal(token text,grantid text,idem text,intent_hash text,result jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE person authority.memberships; scope jsonb; prior jsonb; pid uuid; suggestion jsonb; BEGIN
 person:=authority.actor(token); scope:=paypal_v1.proposal_scope(token,grantid);
 IF idem IS NULL OR idem !~ '^[a-zA-Z0-9_-]{8,100}$' OR intent_hash !~ '^[a-f0-9]{64}$' THEN
  PERFORM authority.fail('INVALID_PROPOSAL_IDENTITY',422); END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(person.tenant_id||':'||person.principal||':ai:'||idem,0));
 prior:=paypal_v1.recover_proposal(token,grantid,idem,intent_hash); IF prior IS NOT NULL THEN RETURN prior; END IF;
 suggestion:=result->'extraction'->'proposal';
 IF result->'financial_authority' IS DISTINCT FROM 'false'::jsonb OR result->'extraction'->>'status' IS DISTINCT FROM 'PROPOSED'
 OR suggestion->>'product_id' IS DISTINCT FROM 'workspace-seat' OR suggestion->'quantity' IS DISTINCT FROM '1'::jsonb
 OR suggestion->>'currency' IS DISTINCT FROM 'USD' OR NOT scope->'beneficiaries' ? (suggestion->>'beneficiary')
 OR jsonb_typeof(suggestion->'spending_limit_minor') IS DISTINCT FROM 'number'
 OR (suggestion->>'spending_limit_minor') !~ '^[1-9][0-9]{0,9}$'
 OR (suggestion->>'spending_limit_minor')::bigint>1000000000 THEN
  PERFORM authority.fail('UNTRUSTED_PROPOSAL_REJECTED',422); END IF;
 INSERT INTO paypal_v1.proposals(tenant_id,principal,grant_id,idempotency_key,intent_sha256,result)
 VALUES(person.tenant_id,person.principal,grantid,idem,intent_hash,result) RETURNING id INTO pid;
 RETURN paypal_v1.proposal(token,pid);
END $$;
CREATE FUNCTION paypal_v1.link_quote(token text,pid uuid,quoteid text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE p jsonb; q authority.seller_quotes; suggestion jsonb; BEGIN
 p:=paypal_v1.proposal(token,pid); suggestion:=p->'result'->'extraction'->'proposal';
 SELECT * INTO q FROM authority.seller_quotes WHERE id=quoteid AND tenant_id=p->>'tenant_id';
 IF q.id IS NULL OR q.currency<>'USD' OR q.amount>(suggestion->>'spending_limit_minor')::bigint
 OR jsonb_array_length(q.body->'lines')<>1 OR q.body->'lines'->0->>'beneficiary' IS DISTINCT FROM suggestion->>'beneficiary' THEN
  PERFORM authority.fail('SIGNED_QUOTE_EXCEEDS_PROPOSAL_OR_SCOPE',403); END IF;
 INSERT INTO paypal_v1.proposal_quotes(quote_id,proposal_id) VALUES(quoteid,pid) ON CONFLICT DO NOTHING;
 IF NOT EXISTS(SELECT 1 FROM paypal_v1.proposal_quotes WHERE quote_id=quoteid AND proposal_id=pid) THEN
  PERFORM authority.fail('PROPOSAL_QUOTE_CONFLICT'); END IF;
 RETURN jsonb_build_object('proposal_id',pid,'quote',q.body,'quote_id',q.id,'currency','USD');
END $$;

CREATE FUNCTION paypal_v1.consent(token text,opid text,expected bigint,idem text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE d jsonb; r paypal_v1.runtime; BEGIN
 -- Existing disclosure checks buyer ownership, exact authority, epoch and expiry,
 -- and persists exposure plus its recovery obligation atomically.
 d:=dispatch.disclose(token,opid,expected,idem);
 SELECT * INTO r FROM paypal_v1.runtime WHERE operation_id=opid;
 IF r.operation_id IS NULL OR r.blocked OR r.capture_confirmed THEN PERFORM authority.fail('CONSENT_NOT_AVAILABLE'); END IF;
 RETURN d||jsonb_build_object('approval_url','https://www.sandbox.paypal.com/checkoutnow?token='||r.provider_order_id,
 'settlement_verified',false,'next_action','MANUAL_PAYPAL_BUYER_CONSENT');
END $$;

CREATE FUNCTION paypal_v1.inspect(token text,opid text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE o dispatch.business_operations; r paypal_v1.runtime; BEGIN
 o:=dispatch.user_operation(token,opid,'VIEW');
 SELECT * INTO r FROM paypal_v1.runtime WHERE operation_id=opid;
 IF r.operation_id IS NULL THEN PERFORM authority.fail('PAYPAL_OPERATION_NOT_VISIBLE',403); END IF;
 RETURN jsonb_build_object('contract_version','paypal-v1','classification','PROTECTED_PAYPAL_SANDBOX',
 'operation_id',o.id,'action_id',o.action_id,'evidence_id',o.evidence_id,'decision_id',o.decision_id,
 'approval_id',o.approval_id,'reservation_id',o.reservation_id,'merchant_id',o.account_id,'amount_minor',o.amount,'currency',o.currency,
 'provider_order_id',r.provider_order_id,'provider_order_status',r.provider_status,'capture_id',r.capture_id,
 'capture_confirmed',r.capture_confirmed,'blocked',r.blocked,'blocked_reason',r.blocked_reason,'revision',r.revision,
 'last_canonical_read_at',r.latest_read_at,'receipt_id',r.latest_receipt_id,
 'create_committed',EXISTS(SELECT 1 FROM paypal_v1.phase_commits pc WHERE pc.operation_id=opid AND pc.phase='CREATE'),
 'capture_committed',EXISTS(SELECT 1 FROM paypal_v1.phase_commits pc WHERE pc.operation_id=opid AND pc.phase='CAPTURE'),
 'dispatch_revision',(SELECT revision FROM dispatch.operation_runtime WHERE operation_id=opid),
 'economic',economic.inspect(token,opid));
END $$;
