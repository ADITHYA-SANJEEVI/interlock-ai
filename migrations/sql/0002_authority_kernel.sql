-- SECURITY DEFINER ownership is the dedicated NOLOGIN authority owner. Runtime roles receive only
-- command/snapshot/recovery/quote registration execution, never table write/read powers.
CREATE FUNCTION authority.fail(code text, http_status int DEFAULT 409) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION '%',code USING ERRCODE='P0001', DETAIL=http_status::text; END $$;

CREATE FUNCTION authority.actor(token text) RETURNS authority.memberships
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,authority,pg_temp AS $$
DECLARE payload text; sig text; data jsonb; key text; person authority.memberships;
BEGIN
    payload:=split_part(token,'.',1); sig:=split_part(token,'.',2);
    SELECT secret INTO key FROM authority.issuer_keys WHERE name='session';
    IF key IS NULL OR sig<>encode(public.hmac(convert_to(payload,'UTF8'),convert_to(key,'UTF8'),'sha256'),'hex') THEN
        PERFORM authority.fail('AUTHENTICATION_REQUIRED',401);
    END IF;
    BEGIN data:=convert_from(decode(translate(payload,'-_','+/'),'base64'),'UTF8')::jsonb;
    EXCEPTION WHEN OTHERS THEN PERFORM authority.fail('AUTHENTICATION_REQUIRED',401); END;
    IF (data->>'exp')::bigint <= extract(epoch FROM clock_timestamp()) THEN
        PERFORM authority.fail('AUTHENTICATION_REQUIRED',401);
    END IF;
    SELECT * INTO person FROM authority.memberships WHERE principal=data->>'user' AND active;
    IF NOT FOUND THEN PERFORM authority.fail('SCOPE_DENIED',403); END IF;
    RETURN person;
END $$;

CREATE FUNCTION authority.require_role(person authority.memberships, role_name text) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN IF NOT role_name=ANY(person.roles) THEN PERFORM authority.fail('SCOPE_DENIED',403); END IF; END $$;

CREATE FUNCTION authority.register_quote(token text, raw text, supplied_signature text, idem text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,authority,pg_temp AS $$
DECLARE person authority.memberships; key text; q jsonb; line jsonb; total bigint:=0; owner public.tenants;
    h text; rid text:='cmd_'||gen_random_uuid()::text; prior authority.request_identities; result jsonb; lines jsonb;
BEGIN
    person:=authority.actor(token); PERFORM authority.require_role(person,'PURCHASE_REQUESTER');
    SELECT secret INTO key FROM authority.issuer_keys WHERE name='seller';
    IF key IS NULL OR supplied_signature<>encode(public.hmac(convert_to(raw,'UTF8'),convert_to(key,'UTF8'),'sha256'),'hex') THEN
        PERFORM authority.fail('SELLER_SIGNATURE_INVALID',403);
    END IF;
    q:=raw::jsonb; SELECT * INTO owner FROM public.tenants WHERE id=person.tenant_id;
    IF q->>'tenant_id'<>person.tenant_id OR q->>'seller'<>owner.seller OR q->>'currency'<>'INR'
       OR (q->>'revision')::bigint<1 OR jsonb_array_length(q->'lines') NOT BETWEEN 1 AND 5 THEN
        PERFORM authority.fail('QUOTE_SCOPE_MISMATCH',403);
    END IF;
    FOR line IN SELECT value FROM jsonb_array_elements(q->'lines') LOOP
        IF line->>'sku'<>'workspace-seat' OR (line->>'unit_price')::bigint<=0 OR line->>'allocation_id' IS NULL THEN
            PERFORM authority.fail('QUOTE_TERMS_INVALID',422);
        END IF;
        total:=total+(line->>'unit_price')::bigint;
    END LOOP;
    IF total<>(q->>'amount')::bigint THEN PERFORM authority.fail('QUOTE_TERMS_INVALID',422); END IF;
    idem:=coalesce(idem,'quote_'||(q->>'id'));
    IF length(idem) NOT BETWEEN 8 AND 100 THEN PERFORM authority.fail('INVALID_COMMAND_KEY',422); END IF;
    SELECT jsonb_agg(jsonb_build_object('sku',value->>'sku','beneficiary',value->>'beneficiary')) INTO lines
        FROM jsonb_array_elements(q->'lines');
    h:=encode(public.digest(jsonb_build_object('tenant',person.tenant_id,'principal',person.principal,
        'family','quote','lines',lines)::text,'sha256'),'hex');
    PERFORM pg_advisory_xact_lock(hashtextextended(person.tenant_id||'/'||person.principal||'/quote/'||idem,0));
    SELECT * INTO prior FROM authority.request_identities WHERE tenant_id=person.tenant_id AND principal=person.principal
        AND family='quote' AND idempotency_key=idem;
    IF FOUND THEN
        IF prior.request_hash<>h THEN PERFORM authority.fail('IDEMPOTENCY_CONFLICT'); END IF;
        RETURN prior.result->'quote';
    END IF;
    INSERT INTO authority.seller_quotes VALUES(q->>'id',person.tenant_id,q->>'seller',(q->>'revision')::bigint,
        total,'INR',(q->>'expires_at')::timestamptz,q,encode(public.digest(raw,'sha256'),'hex'),'AUTHENTIC_SIGNED_CONTROLLED_SELLER')
        ON CONFLICT(id) DO NOTHING;
    IF NOT EXISTS(SELECT 1 FROM authority.seller_quotes WHERE id=q->>'id' AND tenant_id=person.tenant_id
       AND body_digest=encode(public.digest(raw,'sha256'),'hex')) THEN PERFORM authority.fail('QUOTE_CONFLICT'); END IF;
    INSERT INTO authority.quote_heads VALUES(q->>'id',person.tenant_id,(q->>'revision')::bigint) ON CONFLICT DO NOTHING;
    result:=jsonb_build_object('command_id',rid,'kind','PURCHASE','status','QUOTE_PREPARED','quote',q);
    INSERT INTO authority.request_identities VALUES(rid,person.tenant_id,person.principal,'quote',idem,h,result,clock_timestamp());
    INSERT INTO authority.audit_events VALUES('audit_'||gen_random_uuid()::text,person.tenant_id,person.principal,rid,'quote',result,clock_timestamp());
    RETURN q;
END $$;

CREATE FUNCTION authority.command(token text, family text, body jsonb, expected bigint, idem text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,authority,pg_temp AS $$
#variable_conflict use_variable
DECLARE
    person authority.memberships; again authority.memberships; owner public.tenants;
    cfg authority.tenant_control; g authority.grants; gh authority.grant_heads; gv authority.grant_versions;
    pv authority.policy_versions; pol authority.policies; q authority.seller_quotes; qh authority.quote_heads;
    fi authority.refund_fixture_sources; ih authority.intent_heads; ar authority.action_revisions;
    ev authority.evidence_revisions; deci authority.policy_decisions; ap authority.approvals;
    aph authority.approval_heads; approver authority.memberships; prior authority.request_identities;
    k text; rid text:='cmd_'||gen_random_uuid()::text; result jsonb; h text; obj text; vid text; eid text;
    did text; aid text; iid text; irid text; rsid text; amount bigint; terms jsonb; members jsonb;
    n bigint; now_at timestamptz; expires timestamptz; beneficiaries text[]; source_id text;
    allowed_fields text[];
BEGIN
    person:=authority.actor(token);
    IF family NOT IN ('create-grant','activate-grant','revoke-grant','expire-grant','revise-grant','publish-policy','propose','approve','admit')
       OR idem IS NULL OR length(idem) NOT BETWEEN 8 AND 100 OR expected IS NULL OR expected<0 THEN
        PERFORM authority.fail('INVALID_COMMAND',422);
    END IF;
    allowed_fields:=CASE family
        WHEN 'create-grant' THEN ARRAY['kind','subject','lifetime_bound','currency','beneficiaries','expires_at','resource_id']
        WHEN 'revise-grant' THEN ARRAY['grant_id','beneficiaries','expires_at']
        WHEN 'publish-policy' THEN ARRAY['kind','approval_above','max_amount']
        WHEN 'propose' THEN ARRAY['grant_id','quote_id','resource_id','amount','intent_id']
        WHEN 'approve' THEN ARRAY['action_id','evidence_id','decision_id','expires_at']
        WHEN 'admit' THEN ARRAY['action_id','evidence_id','decision_id','approval_id']
        ELSE ARRAY['grant_id'] END;
    IF jsonb_typeof(body) IS DISTINCT FROM 'object' THEN PERFORM authority.fail('INVALID_COMMAND_SCHEMA',422); END IF;
    IF EXISTS(SELECT 1 FROM jsonb_object_keys(body) AS f(name) WHERE NOT f.name=ANY(allowed_fields)) THEN
        PERFORM authority.fail('INVALID_COMMAND_SCHEMA',422);
    END IF;
    h:=encode(public.digest(jsonb_build_object('tenant',person.tenant_id,'principal',person.principal,
        'family',family,'body',body,'expected',expected)::text,'sha256'),'hex');
    -- Command identity serialization precedes the domain locks; hash collisions only add contention.
    PERFORM pg_advisory_xact_lock(hashtextextended(person.tenant_id||'/'||person.principal||'/'||family||'/'||idem,0));
    SELECT * INTO prior FROM authority.request_identities WHERE tenant_id=person.tenant_id AND principal=person.principal
        AND request_identities.family=command.family AND idempotency_key=idem;
    IF FOUND THEN
        IF prior.request_hash<>h THEN PERFORM authority.fail('IDEMPOTENCY_CONFLICT'); END IF;
        -- Current membership is always required; role/visibility is also rechecked before result retrieval.
        k:=prior.result->>'kind';
        IF NOT (k||'_ISSUER'=ANY(person.roles) OR k||'_REQUESTER'=ANY(person.roles) OR k||'_APPROVER'=ANY(person.roles)) THEN
            PERFORM authority.fail('SCOPE_DENIED',403);
        END IF;
        RETURN prior.result;
    END IF;
    IF family IN ('create-grant','publish-policy') THEN k:=body->>'kind';
    ELSIF family IN ('activate-grant','revoke-grant','expire-grant','revise-grant') THEN
        SELECT * INTO g FROM authority.grants WHERE id=body->>'grant_id' AND tenant_id=person.tenant_id;
        IF NOT FOUND THEN PERFORM authority.fail('SCOPE_DENIED',403); END IF; k:=g.kind;
    ELSIF family='propose' THEN
        SELECT * INTO g FROM authority.grants WHERE id=body->>'grant_id' AND tenant_id=person.tenant_id AND subject=person.principal;
        IF NOT FOUND THEN PERFORM authority.fail('SCOPE_DENIED',403); END IF; k:=g.kind;
    ELSE
        SELECT * INTO ar FROM authority.action_revisions WHERE id=body->>'action_id' AND tenant_id=person.tenant_id;
        IF NOT FOUND THEN PERFORM authority.fail('SCOPE_DENIED',403); END IF; k:=ar.kind;
        SELECT * INTO g FROM authority.grants WHERE id=ar.grant_id AND tenant_id=person.tenant_id;
    END IF;
    IF k NOT IN ('PURCHASE','REFUND') THEN PERFORM authority.fail('INVALID_KIND',422); END IF;
    IF family='propose' AND ((k='PURCHASE' AND (body->>'amount' IS NOT NULL OR body->>'resource_id' IS NOT NULL))
        OR (k='REFUND' AND body->>'quote_id' IS NOT NULL)) THEN PERFORM authority.fail('INVALID_ACTION_SOURCE',422); END IF;
    IF family IN ('create-grant','activate-grant','revoke-grant','expire-grant','revise-grant','publish-policy') THEN
        PERFORM authority.require_role(person,k||'_ISSUER');
        IF g.id IS NOT NULL AND g.issuer<>person.principal THEN PERFORM authority.fail('SCOPE_DENIED',403); END IF;
    ELSIF family='approve' THEN PERFORM authority.require_role(person,k||'_APPROVER');
    ELSE
        PERFORM authority.require_role(person,k||'_REQUESTER');
        IF g.subject<>person.principal THEN PERFORM authority.fail('SCOPE_DENIED',403); END IF;
    END IF;
    -- Material configuration is shared-locked, publication takes the exclusive same head.
    IF family='publish-policy' THEN
        SELECT * INTO cfg FROM authority.tenant_control WHERE tenant_id=person.tenant_id AND kind=k FOR UPDATE;
    ELSE
        SELECT * INTO cfg FROM authority.tenant_control WHERE tenant_id=person.tenant_id AND kind=k FOR SHARE;
    END IF;
    IF NOT FOUND THEN PERFORM authority.fail('CONFIGURATION_MISSING'); END IF;
    IF g.id IS NOT NULL THEN
        SELECT * INTO gh FROM authority.grant_heads WHERE grant_id=g.id AND tenant_id=person.tenant_id FOR UPDATE;
        SELECT * INTO gv FROM authority.grant_versions WHERE id=gh.version_id AND tenant_id=person.tenant_id;
    END IF;
    IF ar.id IS NOT NULL THEN
        SELECT * INTO ih FROM authority.intent_heads WHERE intent_id=ar.intent_id AND tenant_id=person.tenant_id FOR UPDATE;
    ELSIF family='propose' AND body->>'intent_id' IS NOT NULL THEN
        SELECT * INTO ih FROM authority.intent_heads WHERE intent_id=body->>'intent_id' AND tenant_id=person.tenant_id FOR UPDATE;
        IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM authority.purchase_intents WHERE id=ih.intent_id AND requester=person.principal) THEN
            PERFORM authority.fail('SCOPE_DENIED',403);
        END IF;
    END IF;
    again:=authority.actor(token);
    IF again.revision<>person.revision OR again.roles<>person.roles THEN PERFORM authority.fail('MEMBERSHIP_CHANGED'); END IF;
    now_at:=clock_timestamp();
    SELECT * INTO owner FROM public.tenants WHERE id=person.tenant_id;

    IF family IN ('activate-grant','revoke-grant','expire-grant','revise-grant') AND expected<>gh.revision THEN
        PERFORM authority.fail('REVISION_CONFLICT');
    END IF;
    IF family='create-grant' THEN
        IF expected<>0 THEN PERFORM authority.fail('REVISION_CONFLICT'); END IF;
        SELECT * INTO again FROM authority.memberships WHERE principal=body->>'subject' AND tenant_id=person.tenant_id AND active;
        IF NOT FOUND OR NOT k||'_REQUESTER'=ANY(again.roles) THEN PERFORM authority.fail('SCOPE_DENIED',403); END IF;
        amount:=(body->>'lifetime_bound')::bigint; expires:=(body->>'expires_at')::timestamptz;
        beneficiaries:=ARRAY(SELECT jsonb_array_elements_text(body->'beneficiaries'));
        IF amount<=0 OR amount>1000000000 OR expires<=now_at OR cardinality(beneficiaries)=0
           OR body->>'currency'<>'INR' OR (k='PURCHASE' AND body->>'resource_id' IS NOT NULL) THEN PERFORM authority.fail('INVALID_GRANT_TERMS',422); END IF;
        IF k='REFUND' AND NOT EXISTS(SELECT 1 FROM authority.refund_fixture_sources WHERE id=body->>'resource_id'
            AND tenant_id=person.tenant_id AND integration=owner.integration) THEN PERFORM authority.fail('SCOPE_DENIED',403); END IF;
        obj:='grant_'||gen_random_uuid()::text; vid:='gv_'||gen_random_uuid()::text;
        INSERT INTO authority.grants(id,tenant_id,issuer,subject,kind,currency,lifetime_bound)
            VALUES(obj,person.tenant_id,person.principal,again.principal,k,'INR',amount);
        INSERT INTO authority.grant_versions(id,tenant_id,grant_id,version,epoch,seller,integration,beneficiaries,resource_id,expires_at)
            VALUES(vid,person.tenant_id,obj,1,1,owner.seller,owner.integration,beneficiaries,body->>'resource_id',expires);
        INSERT INTO authority.grant_heads(grant_id,tenant_id,version_id,status,bound,free)
            VALUES(obj,person.tenant_id,vid,'DRAFT',amount,amount);
        result:=jsonb_build_object('grant_id',obj,'version_id',vid,'revision',1,'status','DRAFT','kind',k);
    ELSIF family='activate-grant' THEN
        IF gh.status<>'DRAFT' OR gv.expires_at<=now_at THEN PERFORM authority.fail('GRANT_NOT_ACTIVATABLE'); END IF;
        UPDATE authority.grant_heads SET status='ACTIVE',revision=revision+1 WHERE grant_id=g.id;
        result:=jsonb_build_object('grant_id',g.id,'revision',gh.revision+1,'status','ACTIVE','kind',k);
    ELSIF family='revoke-grant' THEN
        IF gh.status IN ('REVOKED','EXPIRED') THEN PERFORM authority.fail('AUTHORITY_TERMINAL'); END IF;
        UPDATE authority.grant_heads SET status='REVOKED',epoch=epoch+1,revision=revision+1 WHERE grant_id=g.id;
        result:=jsonb_build_object('grant_id',g.id,'revision',gh.revision+1,'status','REVOKED','kind',k);
    ELSIF family='expire-grant' THEN
        IF gh.status IN ('REVOKED','EXPIRED') OR gv.expires_at>now_at THEN PERFORM authority.fail('GRANT_NOT_EXPIRED'); END IF;
        UPDATE authority.grant_heads SET status='EXPIRED',epoch=epoch+1,revision=revision+1 WHERE grant_id=g.id;
        result:=jsonb_build_object('grant_id',g.id,'revision',gh.revision+1,'status','EXPIRED','kind',k);
    ELSIF family='revise-grant' THEN
        IF gh.status IN ('REVOKED','EXPIRED') OR gv.expires_at<=now_at THEN PERFORM authority.fail('AUTHORITY_TERMINAL'); END IF;
        expires:=(body->>'expires_at')::timestamptz;
        beneficiaries:=ARRAY(SELECT jsonb_array_elements_text(body->'beneficiaries'));
        IF expires<=now_at OR cardinality(beneficiaries)=0 THEN PERFORM authority.fail('INVALID_GRANT_TERMS',422); END IF;
        vid:='gv_'||gen_random_uuid()::text;
        INSERT INTO authority.grant_versions(id,tenant_id,grant_id,version,epoch,seller,integration,beneficiaries,resource_id,expires_at)
            VALUES(vid,person.tenant_id,g.id,gv.version+1,gh.epoch+1,gv.seller,gv.integration,beneficiaries,gv.resource_id,expires);
        UPDATE authority.grant_heads SET version_id=vid,epoch=epoch+1,revision=revision+1 WHERE grant_id=g.id;
        result:=jsonb_build_object('grant_id',g.id,'version_id',vid,'revision',gh.revision+1,'status',gh.status,'kind',k);
    ELSIF family='publish-policy' THEN
        IF expected<>cfg.epoch THEN PERFORM authority.fail('REVISION_CONFLICT'); END IF;
        IF (body->>'approval_above')::bigint<0 OR (body->>'max_amount')::bigint<=0 THEN
            PERFORM authority.fail('INVALID_POLICY',422);
        END IF;
        SELECT * INTO pol FROM authority.policies WHERE tenant_id=person.tenant_id AND kind=k;
        IF NOT FOUND THEN
            obj:='policy_'||gen_random_uuid()::text;
            INSERT INTO authority.policies VALUES(obj,person.tenant_id,k,k||' authority',person.principal);
            n:=1;
        ELSE
            obj:=pol.id; SELECT coalesce(max(version),0)+1 INTO n FROM authority.policy_versions WHERE policy_id=obj;
        END IF;
        vid:='pv_'||gen_random_uuid()::text;
        SELECT * INTO pv FROM authority.policy_versions WHERE id=cfg.policy_version_id;
        IF pv.id IS NOT NULL AND pv.approval_above=(body->>'approval_above')::bigint AND pv.max_amount=(body->>'max_amount')::bigint THEN
            result:=jsonb_build_object('policy_version_id',pv.id,'revision',cfg.epoch,'status','UNCHANGED','kind',k);
        ELSE
            INSERT INTO authority.policy_versions(id,tenant_id,policy_id,version,approval_above,max_amount,evaluator_version,published_by)
                VALUES(vid,person.tenant_id,obj,n,(body->>'approval_above')::bigint,(body->>'max_amount')::bigint,'authority-v1',person.principal);
            UPDATE authority.tenant_control SET epoch=epoch+1,policy_version_id=vid WHERE tenant_id=person.tenant_id AND kind=k;
            result:=jsonb_build_object('policy_version_id',vid,'revision',cfg.epoch+1,'status','PUBLISHED','kind',k);
        END IF;
    ELSIF family='propose' THEN
        IF gh.status<>'ACTIVE' OR gv.expires_at<=now_at THEN PERFORM authority.fail('AUTHORITY_INACTIVE',403); END IF;
        IF cfg.paused OR cfg.policy_version_id IS NULL THEN PERFORM authority.fail('POLICY_NOT_READY'); END IF;
        IF ih.intent_id IS NULL AND expected<>0 OR ih.intent_id IS NOT NULL AND expected<>ih.revision THEN
            PERFORM authority.fail('REVISION_CONFLICT');
        END IF;
        IF ih.status='ADMITTED' THEN PERFORM authority.fail('ALREADY_ADMITTED'); END IF;
        IF k='PURCHASE' THEN
            SELECT * INTO q FROM authority.seller_quotes WHERE id=body->>'quote_id' AND tenant_id=person.tenant_id;
            IF NOT FOUND THEN PERFORM authority.fail('SCOPE_DENIED',403); END IF;
            SELECT * INTO qh FROM authority.quote_heads WHERE quote_id=q.id FOR SHARE;
            IF qh.revision<>q.revision OR q.expires_at<=clock_timestamp() THEN PERFORM authority.fail('QUOTE_STALE'); END IF;
            beneficiaries:=ARRAY(SELECT value->>'beneficiary' FROM jsonb_array_elements(q.body->'lines'));
            IF q.seller<>gv.seller OR gv.integration<>owner.integration OR NOT beneficiaries<@gv.beneficiaries THEN
                PERFORM authority.fail('GRANT_SCOPE_MISMATCH',403);
            END IF;
            amount:=q.amount; terms:=jsonb_build_object('seller',q.seller,'integration',gv.integration,'quote',q.body,
                'quote_digest',q.body_digest,'provenance','AUTHENTIC_SIGNED_CONTROLLED_SELLER');
            source_id:=q.id;
        ELSE
            SELECT * INTO fi FROM authority.refund_fixture_sources WHERE id=body->>'resource_id' AND tenant_id=person.tenant_id;
            IF NOT FOUND OR fi.id IS DISTINCT FROM gv.resource_id OR fi.integration<>gv.integration THEN
                PERFORM authority.fail('GRANT_SCOPE_MISMATCH',403);
            END IF;
            amount:=(body->>'amount')::bigint;
            IF amount<=0 OR amount>fi.amount THEN PERFORM authority.fail('FIXTURE_AMOUNT_INVALID',422); END IF;
            terms:=jsonb_build_object('resource_id',fi.id,'resource_revision',fi.revision,'integration',fi.integration,
                'amount',amount,'currency','INR','provenance','LOCAL_REFERENCE_ONLY','dispatch_allowed',false);
            source_id:=fi.id;
        END IF;
        IF ih.intent_id IS NULL THEN
            iid:='intent_'||gen_random_uuid()::text; n:=1;
            INSERT INTO authority.purchase_intents(id,tenant_id,requester,kind) VALUES(iid,person.tenant_id,person.principal,k);
        ELSE iid:=ih.intent_id; n:=ih.revision+1; END IF;
        irid:='ir_'||gen_random_uuid()::text; aid:='action_'||gen_random_uuid()::text;
        eid:='evidence_'||gen_random_uuid()::text; did:='decision_'||gen_random_uuid()::text;
        terms:=terms||jsonb_build_object('amount',amount,'currency','INR');
        h:=encode(public.digest(terms::text,'sha256'),'hex');
        INSERT INTO authority.intent_revisions(id,tenant_id,intent_id,revision,terms,terms_digest)
            VALUES(irid,person.tenant_id,iid,n,terms,h);
        INSERT INTO authority.action_revisions(id,tenant_id,intent_id,intent_revision_id,requester,requester_revision,kind,grant_id,amount,currency,terms,request_digest)
            VALUES(aid,person.tenant_id,iid,irid,person.principal,person.revision,k,g.id,amount,'INR',terms,h);
        INSERT INTO authority.intent_heads VALUES(iid,person.tenant_id,n,irid,aid,'PREPARED')
            ON CONFLICT(intent_id) DO UPDATE SET revision=n,intent_revision_id=irid,action_id=aid,status='PREPARED';
        members:=jsonb_build_object('action',aid,'grant_version',gv.id,'grant_epoch',gh.epoch,'config_epoch',cfg.epoch,
            'policy',cfg.policy_version_id,'quote',q.id,'quote_revision',qh.revision,'fixture',fi.id,'fixture_revision',fi.revision);
        INSERT INTO authority.evidence_revisions(id,tenant_id,action_id,grant_version_id,grant_epoch,config_epoch,policy_version_id,
            quote_id,quote_revision,fixture_id,fixture_revision,digest)
            VALUES(eid,person.tenant_id,aid,gv.id,gh.epoch,cfg.epoch,cfg.policy_version_id,q.id,qh.revision,fi.id,fi.revision,
                encode(public.digest(members::text,'sha256'),'hex'));
        INSERT INTO authority.evidence_members VALUES
            (person.tenant_id,eid,'GRANT_VERSION',gv.id,gv.version),
            (person.tenant_id,eid,'POLICY_VERSION',cfg.policy_version_id,cfg.epoch),
            (person.tenant_id,eid,'ACTION',aid,n),
            (person.tenant_id,eid,'CONFIG',person.tenant_id||'/'||k,cfg.epoch),
            (person.tenant_id,eid,CASE WHEN k='PURCHASE' THEN 'SELLER_QUOTE' ELSE 'REFUND_FIXTURE' END,source_id,
                CASE WHEN k='PURCHASE' THEN qh.revision ELSE fi.revision END);
        SELECT * INTO pv FROM authority.policy_versions WHERE id=cfg.policy_version_id AND tenant_id=person.tenant_id;
        INSERT INTO authority.policy_decisions(id,tenant_id,action_id,evidence_id,policy_version_id,context_digest,allowed,approval_required,reasons,evaluator_version)
            VALUES(did,person.tenant_id,aid,eid,pv.id,encode(public.digest(members::text,'sha256'),'hex'),amount<=pv.max_amount,
                amount>pv.approval_above,ARRAY[CASE WHEN amount>pv.max_amount THEN 'POLICY_AMOUNT_DENIED'
                    WHEN amount>pv.approval_above THEN 'INDEPENDENT_APPROVAL_REQUIRED' ELSE 'WITHIN_AUTOMATIC_THRESHOLD' END],'authority-v1');
        result:=jsonb_build_object('intent_id',iid,'action_id',aid,'evidence_id',eid,'decision_id',did,
            'revision',n,'status','PREPARED','kind',k,'amount',amount,'approval_required',amount>pv.approval_above);
    ELSE
        IF gh.status<>'ACTIVE' THEN PERFORM authority.fail('AUTHORITY_'||gh.status,403); END IF;
        IF ih.action_id IS DISTINCT FROM ar.id OR ih.intent_revision_id IS DISTINCT FROM ar.intent_revision_id
            OR ih.revision<>expected THEN PERFORM authority.fail('ACTION_STALE'); END IF;
        IF ih.status='ADMITTED' THEN PERFORM authority.fail('ALREADY_ADMITTED'); END IF;
        SELECT * INTO ev FROM authority.evidence_revisions WHERE id=body->>'evidence_id' AND tenant_id=person.tenant_id AND action_id=ar.id;
        IF NOT FOUND THEN PERFORM authority.fail('EVIDENCE_STALE'); END IF;
        SELECT * INTO deci FROM authority.policy_decisions WHERE id=body->>'decision_id' AND tenant_id=person.tenant_id
            AND action_id=ar.id AND evidence_id=ev.id;
        IF NOT FOUND THEN PERFORM authority.fail('POLICY_DECISION_MISMATCH'); END IF;
        IF cfg.paused OR ev.config_epoch<>cfg.epoch OR ev.policy_version_id<>cfg.policy_version_id
           OR deci.policy_version_id<>cfg.policy_version_id THEN PERFORM authority.fail('POLICY_CHANGED'); END IF;
        IF ev.grant_version_id<>gh.version_id OR ev.grant_epoch<>gh.epoch THEN PERFORM authority.fail('EVIDENCE_STALE'); END IF;
        IF ev.quote_id IS NOT NULL THEN
            SELECT * INTO q FROM authority.seller_quotes WHERE id=ev.quote_id AND tenant_id=person.tenant_id;
            SELECT * INTO qh FROM authority.quote_heads WHERE quote_id=q.id FOR SHARE;
            IF qh.revision<>ev.quote_revision OR q.expires_at<=clock_timestamp() THEN PERFORM authority.fail('EVIDENCE_STALE'); END IF;
        ELSE
            SELECT * INTO fi FROM authority.refund_fixture_sources WHERE id=ev.fixture_id AND tenant_id=person.tenant_id FOR SHARE;
            IF fi.revision<>ev.fixture_revision THEN PERFORM authority.fail('EVIDENCE_STALE'); END IF;
        END IF;
        now_at:=clock_timestamp();
        IF gh.status<>'ACTIVE' THEN PERFORM authority.fail('AUTHORITY_'||gh.status,403); END IF;
        IF gv.expires_at<=now_at THEN PERFORM authority.fail('AUTHORITY_EXPIRED',403); END IF;
        SELECT * INTO again FROM authority.memberships WHERE principal=ar.requester AND tenant_id=person.tenant_id;
        IF NOT again.active OR again.revision<>ar.requester_revision OR NOT k||'_REQUESTER'=ANY(again.roles) THEN
            PERFORM authority.fail('REQUESTER_CHANGED',403);
        END IF;
        IF NOT deci.allowed THEN PERFORM authority.fail('POLICY_AMOUNT_DENIED',403); END IF;
        IF family='approve' THEN
            IF ar.requester=person.principal THEN PERFORM authority.fail('SELF_APPROVAL_DENIED',403); END IF;
            IF NOT deci.approval_required THEN PERFORM authority.fail('APPROVAL_NOT_REQUIRED',422); END IF;
            expires:=(body->>'expires_at')::timestamptz;
            IF expires<=now_at OR expires>now_at+interval '1 hour' THEN PERFORM authority.fail('APPROVAL_EXPIRY_INVALID',422); END IF;
            obj:='approval_'||gen_random_uuid()::text;
            INSERT INTO authority.approvals(id,tenant_id,action_id,evidence_id,decision_id,evidence_digest,approver,approver_revision,expires_at)
                VALUES(obj,person.tenant_id,ar.id,ev.id,deci.id,ev.digest,person.principal,person.revision,expires);
            INSERT INTO authority.approval_heads VALUES(obj,person.tenant_id,'AVAILABLE',NULL);
            result:=jsonb_build_object('approval_id',obj,'action_id',ar.id,'revision',ih.revision,'status','AVAILABLE','kind',k);
        ELSE
            IF deci.approval_required THEN
                SELECT * INTO ap FROM authority.approvals WHERE id=body->>'approval_id' AND tenant_id=person.tenant_id
                    AND action_id=ar.id AND evidence_id=ev.id AND decision_id=deci.id AND evidence_digest=ev.digest;
                IF NOT FOUND THEN PERFORM authority.fail('APPROVAL_INVALID'); END IF;
                SELECT * INTO aph FROM authority.approval_heads WHERE approval_id=ap.id AND tenant_id=person.tenant_id FOR UPDATE;
                SELECT * INTO approver FROM authority.memberships WHERE principal=ap.approver AND tenant_id=person.tenant_id FOR SHARE;
                IF aph.status<>'AVAILABLE' OR ap.expires_at<=clock_timestamp() OR NOT approver.active
                   OR approver.revision<>ap.approver_revision OR NOT k||'_APPROVER'=ANY(approver.roles)
                   OR ap.approver=ar.requester THEN PERFORM authority.fail('APPROVAL_INVALID'); END IF;
            ELSIF body->>'approval_id' IS NOT NULL THEN PERFORM authority.fail('APPROVAL_NOT_REQUIRED',422); END IF;
            -- Last lock acquisition may have waited: all temporal predicates use wall time again.
            PERFORM authority.actor(token);
            IF gv.expires_at<=clock_timestamp() THEN PERFORM authority.fail('AUTHORITY_EXPIRED',403); END IF;
            IF q.id IS NOT NULL AND q.expires_at<=clock_timestamp() THEN PERFORM authority.fail('EVIDENCE_STALE'); END IF;
            IF gh.free<ar.amount THEN PERFORM authority.fail('INSUFFICIENT_AUTHORITY',403); END IF;
            rsid:='reservation_'||gen_random_uuid()::text;
            INSERT INTO authority.reservations(id,tenant_id,grant_id,grant_version_id,epoch,action_id,intent_revision_id,request_id,amount,currency,source_terms_digest)
                VALUES(rsid,person.tenant_id,g.id,gv.id,gh.epoch,ar.id,ar.intent_revision_id,rid,ar.amount,ar.currency,ar.request_digest);
            INSERT INTO authority.reservation_heads VALUES(rsid,person.tenant_id,'RESERVED');
            UPDATE authority.grant_heads SET free=free-ar.amount,reserved=reserved+ar.amount WHERE grant_id=g.id;
            INSERT INTO authority.authority_movements(id,tenant_id,grant_id,grant_version_id,epoch,reservation_id,request_id,kind,amount,delta_consumed,delta_reserved,delta_free,reason)
                VALUES('movement_'||gen_random_uuid()::text,person.tenant_id,g.id,gv.id,gh.epoch,rsid,rid,'RESERVE',ar.amount,0,ar.amount,-ar.amount,'EXACT_LOCAL_ADMISSION');
            UPDATE authority.intent_heads SET status='ADMITTED',revision=revision+1 WHERE intent_id=ih.intent_id;
            IF deci.approval_required THEN UPDATE authority.approval_heads SET status='CONSUMED',consumed_by=rsid WHERE approval_id=ap.id; END IF;
            INSERT INTO authority.admitted_work VALUES('work_'||gen_random_uuid()::text,person.tenant_id,rsid,ar.id,'LOCAL_ADMISSION_ONLY',false);
            result:=jsonb_build_object('reservation_id',rsid,'action_id',ar.id,'intent_id',ar.intent_id,'revision',ih.revision+1,
                'status','ADMITTED_LOCAL_ONLY','kind',k,'amount',ar.amount,'dispatch_allowed',false);
        END IF;
    END IF;
    -- Restore substantive command hash: proposal used h for immutable term digest.
    h:=encode(public.digest(jsonb_build_object('tenant',person.tenant_id,'principal',person.principal,
        'family',family,'body',body,'expected',expected)::text,'sha256'),'hex');
    result:=result||jsonb_build_object('command_id',rid);
    INSERT INTO authority.request_identities VALUES(rid,person.tenant_id,person.principal,family,idem,h,result,clock_timestamp());
    INSERT INTO authority.audit_events VALUES('audit_'||gen_random_uuid()::text,person.tenant_id,person.principal,rid,family,result,clock_timestamp());
    RETURN result;
END $$;

CREATE FUNCTION authority.snapshot(token text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,authority,pg_temp AS $$
DECLARE person authority.memberships; result jsonb;
BEGIN
    person:=authority.actor(token);
    SELECT jsonb_build_object(
        'principal',person.principal,'tenant_id',person.tenant_id,'roles',person.roles,'membership_revision',person.revision,
        'configuration',coalesce((SELECT jsonb_agg(to_jsonb(c) ORDER BY c.kind) FROM authority.tenant_control c
            WHERE c.tenant_id=person.tenant_id AND (c.kind||'_ISSUER'=ANY(person.roles)
                OR c.kind||'_REQUESTER'=ANY(person.roles) OR c.kind||'_APPROVER'=ANY(person.roles))),'[]'::jsonb),
        'grants',coalesce((SELECT jsonb_agg(to_jsonb(g)||jsonb_build_object('head',to_jsonb(h),'version',to_jsonb(v),
            'effective_status',CASE WHEN h.status IN ('DRAFT','ACTIVE') AND v.expires_at<=clock_timestamp() THEN 'EXPIRED' ELSE h.status END,
            'revoked_unused',CASE WHEN h.status='REVOKED' THEN h.free ELSE 0 END) ORDER BY g.created_at,g.id)
            FROM authority.grants g JOIN authority.grant_heads h ON h.grant_id=g.id
            JOIN authority.grant_versions v ON v.id=h.version_id WHERE g.tenant_id=person.tenant_id
                AND (g.issuer=person.principal OR g.subject=person.principal OR g.kind||'_APPROVER'=ANY(person.roles))),'[]'::jsonb),
        'policies',coalesce((SELECT jsonb_agg(to_jsonb(v)||jsonb_build_object('kind',p.kind,'current',c.policy_version_id=v.id) ORDER BY p.kind,v.version)
            FROM authority.policy_versions v JOIN authority.policies p ON p.id=v.policy_id
            JOIN authority.tenant_control c ON c.tenant_id=p.tenant_id AND c.kind=p.kind
            WHERE v.tenant_id=person.tenant_id AND (p.kind||'_ISSUER'=ANY(person.roles)
                OR p.kind||'_REQUESTER'=ANY(person.roles) OR p.kind||'_APPROVER'=ANY(person.roles))),'[]'::jsonb),
        'quotes',coalesce((SELECT jsonb_agg(to_jsonb(q) ORDER BY q.id) FROM authority.seller_quotes q
            WHERE q.tenant_id=person.tenant_id AND ('PURCHASE_REQUESTER'=ANY(person.roles) OR 'PURCHASE_APPROVER'=ANY(person.roles))),'[]'::jsonb),
        'actions',coalesce((SELECT jsonb_agg(to_jsonb(a)||jsonb_build_object('head',to_jsonb(h),'evidence',to_jsonb(e),'decision',to_jsonb(d),
                'current',h.action_id=a.id,'fresh',h.action_id=a.id AND e.grant_epoch=gh.epoch AND e.grant_version_id=gh.version_id
                    AND e.config_epoch=c.epoch AND e.policy_version_id=c.policy_version_id
                    AND gh.status='ACTIVE' AND gv.expires_at>clock_timestamp()
                    AND EXISTS(SELECT 1 FROM authority.memberships m WHERE m.principal=a.requester AND m.active AND m.revision=a.requester_revision)
                    AND (e.quote_id IS NULL OR EXISTS(SELECT 1 FROM authority.quote_heads qh JOIN authority.seller_quotes q ON q.id=qh.quote_id
                        WHERE qh.quote_id=e.quote_id AND qh.revision=e.quote_revision AND q.expires_at>clock_timestamp()))
                    AND (e.fixture_id IS NULL OR EXISTS(SELECT 1 FROM authority.refund_fixture_sources f WHERE f.id=e.fixture_id AND f.revision=e.fixture_revision)))
                ORDER BY a.created_at,a.id)
            FROM authority.action_revisions a JOIN authority.intent_heads h ON h.intent_id=a.intent_id
            JOIN authority.evidence_revisions e ON e.action_id=a.id JOIN authority.policy_decisions d ON d.action_id=a.id
            JOIN authority.grant_heads gh ON gh.grant_id=a.grant_id JOIN authority.grant_versions gv ON gv.id=gh.version_id
            JOIN authority.tenant_control c ON c.tenant_id=a.tenant_id AND c.kind=a.kind
            WHERE a.tenant_id=person.tenant_id AND (a.requester=person.principal OR a.kind||'_APPROVER'=ANY(person.roles)
                OR EXISTS(SELECT 1 FROM authority.grants g WHERE g.id=a.grant_id AND g.issuer=person.principal))),'[]'::jsonb),
        'approvals',coalesce((SELECT jsonb_agg(to_jsonb(a)||jsonb_build_object('head',to_jsonb(h),'effective_status',
            CASE WHEN h.status='AVAILABLE' AND a.expires_at<=clock_timestamp() THEN 'EXPIRED' ELSE h.status END) ORDER BY a.created_at)
            FROM authority.approvals a JOIN authority.approval_heads h ON h.approval_id=a.id
            JOIN authority.action_revisions r ON r.id=a.action_id WHERE a.tenant_id=person.tenant_id
                AND (a.approver=person.principal OR r.requester=person.principal OR r.kind||'_APPROVER'=ANY(person.roles))),'[]'::jsonb),
        'reservations',coalesce((SELECT jsonb_agg(to_jsonb(r)||jsonb_build_object('status',h.status) ORDER BY r.created_at)
            FROM authority.reservations r JOIN authority.reservation_heads h ON h.reservation_id=r.id
            JOIN authority.grants g ON g.id=r.grant_id WHERE r.tenant_id=person.tenant_id
                AND (g.issuer=person.principal OR g.subject=person.principal OR g.kind||'_APPROVER'=ANY(person.roles))),'[]'::jsonb),
        'movements',coalesce((SELECT jsonb_agg(to_jsonb(m) ORDER BY m.occurred_at,m.id) FROM authority.authority_movements m
            JOIN authority.grants g ON g.id=m.grant_id WHERE m.tenant_id=person.tenant_id
                AND (g.issuer=person.principal OR g.subject=person.principal OR g.kind||'_APPROVER'=ANY(person.roles))),'[]'::jsonb)
    ) INTO result;
    RETURN result;
END $$;

CREATE FUNCTION authority.recover(token text, command_family text, idem text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,authority,pg_temp AS $$
DECLARE person authority.memberships; r authority.request_identities; k text;
BEGIN
    person:=authority.actor(token);
    SELECT * INTO r FROM authority.request_identities WHERE tenant_id=person.tenant_id AND principal=person.principal
        AND family=command_family AND idempotency_key=idem;
    IF NOT FOUND THEN PERFORM authority.fail('COMMAND_NOT_VISIBLE',403); END IF;
    k:=r.result->>'kind';
    IF NOT (k||'_ISSUER'=ANY(person.roles) OR k||'_REQUESTER'=ANY(person.roles) OR k||'_APPROVER'=ANY(person.roles)) THEN
        PERFORM authority.fail('SCOPE_DENIED',403);
    END IF;
    RETURN r.result;
END $$;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA authority FROM PUBLIC;

-- Explicit least-privilege definer ownership, separate from migration administration.
DO $$ DECLARE item record; BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='interlock_authority_owner') THEN
        CREATE ROLE interlock_authority_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    ELSIF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='interlock_authority_owner'
        AND (rolsuper OR rolcanlogin OR rolcreatedb OR rolcreaterole)) THEN
        RAISE EXCEPTION 'Unsafe authority definer role';
    END IF;
    GRANT USAGE ON SCHEMA public TO interlock_authority_owner;
    GRANT SELECT ON public.tenants,public.identities TO interlock_authority_owner;
    ALTER SCHEMA authority OWNER TO interlock_authority_owner;
    FOR item IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='authority' AND c.relkind='r' LOOP
        EXECUTE format('ALTER TABLE authority.%I OWNER TO interlock_authority_owner',item.relname);
    END LOOP;
    FOR item IN SELECT p.oid::regprocedure AS name FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='authority' LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO interlock_authority_owner',item.name);
    END LOOP;
END $$;
