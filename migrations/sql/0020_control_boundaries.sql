CREATE OR REPLACE FUNCTION authority.snapshot(token text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'authority', 'pg_temp'
AS $function$
DECLARE person authority.memberships; result jsonb;
BEGIN
    person:=authority.actor(token);
 IF (SELECT count(*) FROM (SELECT 1 FROM authority.grants WHERE tenant_id=person.tenant_id LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM authority.policy_versions WHERE tenant_id=person.tenant_id LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM authority.seller_quotes WHERE tenant_id=person.tenant_id LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM authority.action_revisions WHERE tenant_id=person.tenant_id LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM authority.approvals WHERE tenant_id=person.tenant_id LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM authority.reservations WHERE tenant_id=person.tenant_id LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM authority.authority_movements WHERE tenant_id=person.tenant_id LIMIT 101) bounded) > 100 THEN RAISE EXCEPTION 'RESOURCE_LIMIT_EXCEEDED' USING ERRCODE='P0001', DETAIL='429'; END IF;

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
            CASE WHEN h.status='AVAILABLE' AND a.expires_at<=clock_timestamp() THEN 'EXPIRED' ELSE h.status END) ORDER BY a.created_at,a.id)
            FROM authority.approvals a JOIN authority.approval_heads h ON h.approval_id=a.id
            JOIN authority.action_revisions r ON r.id=a.action_id WHERE a.tenant_id=person.tenant_id
                AND (a.approver=person.principal OR r.requester=person.principal OR r.kind||'_APPROVER'=ANY(person.roles))),'[]'::jsonb),
        'reservations',coalesce((SELECT jsonb_agg(to_jsonb(r)||jsonb_build_object('status',h.status) ORDER BY r.created_at,r.id)
            FROM authority.reservations r JOIN authority.reservation_heads h ON h.reservation_id=r.id
            JOIN authority.grants g ON g.id=r.grant_id WHERE r.tenant_id=person.tenant_id
                AND (g.issuer=person.principal OR g.subject=person.principal OR g.kind||'_APPROVER'=ANY(person.roles))),'[]'::jsonb),
        'movements',coalesce((SELECT jsonb_agg(to_jsonb(m) ORDER BY m.occurred_at,m.id) FROM authority.authority_movements m
            JOIN authority.grants g ON g.id=m.grant_id WHERE m.tenant_id=person.tenant_id
                AND (g.issuer=person.principal OR g.subject=person.principal OR g.kind||'_APPROVER'=ANY(person.roles))),'[]'::jsonb)
    ) INTO result;
    RETURN result;
END $function$
;

CREATE OR REPLACE FUNCTION dispatch.inspect(token text, opid text DEFAULT NULL::text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'dispatch', 'pg_temp'
AS $function$
DECLARE person authority.memberships; o dispatch.business_operations; result jsonb:='[]'::jsonb; details jsonb;
BEGIN
 person:=authority.actor(token);
 IF NOT (ARRAY['PURCHASE_ISSUER','PURCHASE_REQUESTER','PURCHASE_APPROVER']::text[] && person.roles)
 THEN PERFORM authority.fail('SCOPE_DENIED',403); END IF;
 IF (SELECT count(*) FROM (SELECT 1 FROM dispatch.business_operations WHERE tenant_id=person.tenant_id AND (opid IS NULL OR id=opid) LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM dispatch.transport_attempts WHERE tenant_id=person.tenant_id AND (opid IS NULL OR operation_id=opid) LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM dispatch.checkout_exposures WHERE tenant_id=person.tenant_id AND (opid IS NULL OR operation_id=opid) LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM dispatch.recovery_obligations WHERE tenant_id=person.tenant_id AND (opid IS NULL OR operation_id=opid) LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM dispatch.operation_observations WHERE tenant_id=person.tenant_id AND (opid IS NULL OR operation_id=opid) LIMIT 101) bounded) > 100 THEN RAISE EXCEPTION 'RESOURCE_LIMIT_EXCEEDED' USING ERRCODE='P0001', DETAIL='429'; END IF;

 IF opid IS NOT NULL THEN PERFORM dispatch.user_operation(token,opid); END IF;
 FOR o IN SELECT b.* FROM dispatch.business_operations b JOIN authority.grants g ON g.id=b.grant_id WHERE b.tenant_id=person.tenant_id
 AND (opid IS NULL OR b.id=opid) AND (b.requester=person.principal OR g.issuer=person.principal OR 'PURCHASE_APPROVER'=ANY(person.roles)) ORDER BY b.created_at,b.id LOOP
 SELECT jsonb_build_object('operation',to_jsonb(o),'runtime',(to_jsonb(rt)-'provider_order')||jsonb_build_object('provider_order',CASE WHEN EXISTS(SELECT 1 FROM dispatch.checkout_exposures WHERE operation_id=o.id) THEN rt.provider_order ELSE NULL END),'grant_head',to_jsonb(gh),'reservation',to_jsonb(r),'exposure_state',to_jsonb(rs),
 'replay_contract',to_jsonb(c),'observation_permitted',EXISTS(SELECT 1 FROM dispatch.observation_memberships WHERE tenant_id=o.tenant_id AND principal=person.principal AND active),
 'disclosure_permitted',EXISTS(SELECT 1 FROM authority.grants WHERE id=o.grant_id AND issuer=person.principal),
 'age_seconds',extract(epoch FROM clock_timestamp()-o.created_at)::bigint,
 'attempts',coalesce((SELECT jsonb_agg(to_jsonb(a)||jsonb_build_object('runtime',to_jsonb(ar),'dispatch',to_jsonb(d)) ORDER BY a.number)
 FROM dispatch.transport_attempts a JOIN dispatch.attempt_runtime ar ON ar.attempt_id=a.id JOIN dispatch.dispatch_records d ON d.attempt_id=a.id WHERE a.operation_id=o.id),'[]'::jsonb),
 'exposures',coalesce((SELECT jsonb_agg(to_jsonb(e)-'instructions' ORDER BY e.first_disclosed_at,e.id) FROM dispatch.checkout_exposures e WHERE e.operation_id=o.id),'[]'::jsonb),
 'duties',coalesce((SELECT jsonb_agg(to_jsonb(d)||jsonb_build_object('runtime',to_jsonb(h),'age_seconds',extract(epoch FROM clock_timestamp()-d.created_at)::bigint) ORDER BY d.created_at,d.id) FROM dispatch.recovery_obligations d JOIN dispatch.duty_runtime h ON h.obligation_id=d.id WHERE d.operation_id=o.id),'[]'::jsonb),
 'observations',coalesce((SELECT jsonb_agg((to_jsonb(v)-'body')||jsonb_build_object('body',CASE WHEN EXISTS(SELECT 1 FROM dispatch.checkout_exposures WHERE operation_id=o.id) THEN v.body ELSE v.body-'id' END) ORDER BY v.observed_at,v.id) FROM dispatch.operation_observations v WHERE v.operation_id=o.id),'[]'::jsonb)) INTO details
 FROM dispatch.operation_runtime rt JOIN authority.grant_heads gh ON gh.grant_id=o.grant_id JOIN authority.reservations r ON r.id=o.reservation_id
 JOIN dispatch.reservation_exposure rs ON rs.reservation_id=r.id JOIN dispatch.replay_contracts c ON c.id=o.replay_contract_id WHERE rt.operation_id=o.id;
 result:=result||jsonb_build_array(details);
 END LOOP;
 RETURN jsonb_build_object('principal',person.principal,'tenant_id',person.tenant_id,'operations',result);
END $function$
;

CREATE OR REPLACE FUNCTION economic.inspect(token text, opid text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'economic', 'pg_temp'
AS $function$
DECLARE o dispatch.business_operations; BEGIN
 o:=dispatch.user_operation(token,opid,'VIEW');
 IF (SELECT count(*) FROM (SELECT 1 FROM economic.observations WHERE operation_id=o.id LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM economic.facts WHERE operation_id=o.id LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM economic.postings p JOIN economic.facts f ON f.id=p.fact_id WHERE f.operation_id=o.id LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM economic.conflicts c JOIN economic.observations ob ON ob.id=c.observation_id WHERE ob.operation_id=o.id LIMIT 101) bounded) > 100 THEN RAISE EXCEPTION 'RESOURCE_LIMIT_EXCEEDED' USING ERRCODE='P0001', DETAIL='429'; END IF;

 RETURN jsonb_build_object('operation_id',o.id,'purchase_id',o.domain_root,'tenant_id',o.tenant_id,'provider',o.adapter,
 'amount',o.amount,'currency',o.currency,'intended',jsonb_build_object('action_id',o.action_id,'evidence_id',o.evidence_id,'quote_id',
 (SELECT quote_id FROM authority.evidence_revisions WHERE id=o.evidence_id)),
 'observations',coalesce((SELECT jsonb_agg(to_jsonb(x)-'payload' ORDER BY observed_at,id) FROM economic.observations x WHERE operation_id=o.id),'[]'::jsonb),
 'facts',coalesce((SELECT jsonb_agg(to_jsonb(x) ORDER BY created_at,id) FROM economic.facts x WHERE operation_id=o.id),'[]'::jsonb),
 'postings',coalesce((SELECT jsonb_agg(to_jsonb(x)||jsonb_build_object('lines',(SELECT jsonb_agg(to_jsonb(l) ORDER BY ordinal) FROM economic.lines l WHERE posting_id=x.id))
 ORDER BY x.created_at,x.id) FROM economic.postings x JOIN economic.facts f ON f.id=x.fact_id WHERE f.operation_id=o.id),'[]'::jsonb),
 'unresolved',coalesce((SELECT jsonb_agg(jsonb_build_object('reason',c.reason,'receipt_id',c.receipt_id) ORDER BY c.id) FROM economic.conflicts c
 JOIN economic.observations ob ON ob.id=c.observation_id WHERE ob.operation_id=o.id),'[]'::jsonb),
 'seller_entitlement',(SELECT jsonb_build_object('order_id',ob.payload->>'order_id','workspace_id',ob.payload->>'workspace_id',
 'state','FINALIZED_DELIVERY','seats',ob.payload->'seats','capture_fact_id',ob.payload->>'capture_fact_id')
 FROM economic.facts sf JOIN economic.observations ob ON ob.id=sf.observation_id WHERE sf.operation_id=o.id AND sf.kind='SELLER_FULFILLED'));
END $function$
;

CREATE OR REPLACE FUNCTION returns.inspect(token text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'returns', 'pg_temp'
AS $function$
DECLARE m authority.memberships; result jsonb; BEGIN
 m:=authority.actor(token);
 IF NOT (ARRAY['REFUND_REQUESTER','REFUND_ISSUER','REFUND_APPROVER']::text[] && m.roles) THEN PERFORM authority.fail('REFUND_VISIBILITY_REQUIRED',403); END IF;
 IF (SELECT count(*) FROM (SELECT 1 FROM returns.locks WHERE tenant_id=m.tenant_id LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM returns.grant_bindings WHERE tenant_id=m.tenant_id LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM returns.obligations WHERE tenant_id=m.tenant_id LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM returns.observations WHERE tenant_id=m.tenant_id LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM returns.operations WHERE obligation_id IN (SELECT id FROM returns.obligations WHERE tenant_id=m.tenant_id) LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM returns.facts WHERE obligation_id IN (SELECT id FROM returns.obligations WHERE tenant_id=m.tenant_id) LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM returns.postings WHERE fact_id IN (SELECT f.id FROM returns.facts f JOIN returns.obligations b ON b.id=f.obligation_id WHERE b.tenant_id=m.tenant_id) LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM economic.facts WHERE tenant_id=m.tenant_id LIMIT 101) bounded) > 100
 OR (SELECT count(*) FROM (SELECT 1 FROM economic.observations WHERE tenant_id=m.tenant_id LIMIT 101) bounded) > 100 THEN RAISE EXCEPTION 'RESOURCE_LIMIT_EXCEEDED' USING ERRCODE='P0001', DETAIL='429'; END IF;

 SELECT jsonb_build_object('tenant_id',m.tenant_id,'principal_id',m.principal,
 'mismatches',jsonb_build_object(
 'CAPTURED_BUT_UNFULFILLED',(SELECT count(*) FROM economic.facts cap WHERE cap.tenant_id=m.tenant_id AND cap.kind='CAPTURE' AND NOT EXISTS(SELECT 1 FROM economic.facts sf WHERE sf.operation_id=cap.operation_id AND sf.kind='SELLER_FULFILLED')),
 'FULFILLED_WITHOUT_MATCHED_CAPTURE',(SELECT count(*) FROM economic.observations ob WHERE ob.tenant_id=m.tenant_id AND ob.kind='FULFILLED' AND NOT EXISTS(SELECT 1 FROM economic.facts cap WHERE cap.operation_id=ob.operation_id AND cap.kind='CAPTURE')),
 'REFUND_PROCESSED_ENTITLEMENT_ACTIVE',(SELECT count(DISTINCT b.lock_id) FROM returns.obligations b JOIN returns.facts f ON f.obligation_id=b.id AND f.kind='REFUND_PROCESSED' WHERE b.tenant_id=m.tenant_id AND NOT EXISTS(SELECT 1 FROM returns.closures c WHERE c.lock_id=b.lock_id)),
 'PROVIDER_REFUND_MISSING_LOCALLY',(SELECT count(*) FROM returns.observations ob JOIN economic.facts cap ON cap.provider=ob.provider
 AND cap.account_id=ob.account_id AND cap.integration=ob.integration AND cap.effect_id=ob.payment_id AND cap.kind='CAPTURE'
 WHERE cap.tenant_id=m.tenant_id AND ob.operation_id IS NULL),
 'ECONOMIC_FACT_UNPOSTED',(SELECT count(*) FROM returns.facts f JOIN returns.obligations b ON b.id=f.obligation_id WHERE b.tenant_id=m.tenant_id AND NOT EXISTS(SELECT 1 FROM returns.postings p WHERE p.fact_id=f.id)),
 'MUTATION_OUTCOME_UNKNOWN',(SELECT count(*) FROM returns.runtime r JOIN returns.operations o ON o.id=r.operation_id JOIN returns.obligations b ON b.id=o.obligation_id WHERE b.tenant_id=m.tenant_id AND r.state='UNKNOWN')),
 -- Legacy browser consumes raw.seats. Reconstruct only that safe projection;
 -- never return the retained signed capability bytes or signature.
 'locks',coalesce((SELECT jsonb_agg((to_jsonb(l)-'signature'-'raw')||jsonb_build_object(
 'seats',l.raw::jsonb->'seats','raw',jsonb_build_object('seats',l.raw::jsonb->'seats')::text) ORDER BY l.confirmed_at,l.id)
 FROM returns.locks l WHERE tenant_id=m.tenant_id),'[]'::jsonb),
 'principal',coalesce((SELECT jsonb_agg(to_jsonb(p)||jsonb_build_object('refundable',p.captured-p.processed-p.unresolved) ORDER BY p.capture_fact_id) FROM returns.principal p JOIN returns.locks l ON l.capture_fact_id=p.capture_fact_id WHERE l.tenant_id=m.tenant_id),'[]'::jsonb),
 'grants',coalesce((SELECT jsonb_agg(to_jsonb(h)||jsonb_build_object('binding',to_jsonb(b),'expires_at',v.expires_at) ORDER BY h.grant_id) FROM authority.grant_heads h JOIN returns.grant_bindings b ON b.grant_id=h.grant_id JOIN authority.grant_versions v ON v.id=h.version_id WHERE b.tenant_id=m.tenant_id),'[]'::jsonb),
 'obligations',coalesce((SELECT jsonb_agg(to_jsonb(b) ORDER BY b.accepted_at,b.id) FROM returns.obligations b WHERE tenant_id=m.tenant_id),'[]'::jsonb),
 'operations',coalesce((SELECT jsonb_agg(to_jsonb(o)||jsonb_build_object('runtime',to_jsonb(r),'recovery_duty',EXISTS(SELECT 1 FROM returns.recovery_duties d WHERE d.operation_id=o.id),'dispatch_records',coalesce((SELECT jsonb_agg(to_jsonb(dr)) FROM returns.dispatch_records dr WHERE dr.operation_id=o.id),'[]'::jsonb)) ORDER BY o.created_at,o.id) FROM returns.operations o JOIN returns.obligations b ON b.id=o.obligation_id JOIN returns.runtime r ON r.operation_id=o.id WHERE b.tenant_id=m.tenant_id),'[]'::jsonb),
 'observations',coalesce((SELECT jsonb_agg(to_jsonb(ob) ORDER BY ob.observed_at,ob.id) FROM returns.observations ob WHERE tenant_id=m.tenant_id),'[]'::jsonb),
 'facts',coalesce((SELECT jsonb_agg(to_jsonb(f) ORDER BY f.created_at,f.id) FROM returns.facts f JOIN returns.obligations b ON b.id=f.obligation_id WHERE b.tenant_id=m.tenant_id),'[]'::jsonb),
 'postings',coalesce((SELECT jsonb_agg(to_jsonb(p)||jsonb_build_object('lines',(SELECT jsonb_agg(to_jsonb(x) ORDER BY x.ordinal) FROM returns.lines x WHERE x.posting_id=p.id)) ORDER BY p.created_at,p.id) FROM returns.postings p JOIN returns.facts f ON f.id=p.fact_id JOIN returns.obligations b ON b.id=f.obligation_id WHERE b.tenant_id=m.tenant_id),'[]'::jsonb),
 'closures',coalesce((SELECT jsonb_agg(to_jsonb(c)-'signature'-'raw' ORDER BY c.confirmed_at,c.lock_id) FROM returns.closures c JOIN returns.locks l ON l.id=c.lock_id WHERE l.tenant_id=m.tenant_id),'[]'::jsonb)) INTO result;
 RETURN result;
END $function$
;

-- Exact existing action route no longer loads the whole tenant snapshot.
CREATE OR REPLACE FUNCTION authority.action_detail(token text,actionid text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,authority,pg_temp AS $$
DECLARE person authority.memberships; result jsonb;
BEGIN
 person:=authority.actor(token);
 SELECT to_jsonb(a)||jsonb_build_object('head',to_jsonb(h),'evidence',to_jsonb(e),'decision',to_jsonb(d),
                'current',h.action_id=a.id,'fresh',h.action_id=a.id AND e.grant_epoch=gh.epoch AND e.grant_version_id=gh.version_id
                    AND e.config_epoch=c.epoch AND e.policy_version_id=c.policy_version_id
                    AND gh.status='ACTIVE' AND gv.expires_at>clock_timestamp()
                    AND EXISTS(SELECT 1 FROM authority.memberships m WHERE m.principal=a.requester AND m.active AND m.revision=a.requester_revision)
                    AND (e.quote_id IS NULL OR EXISTS(SELECT 1 FROM authority.quote_heads qh JOIN authority.seller_quotes q ON q.id=qh.quote_id
                        WHERE qh.quote_id=e.quote_id AND qh.revision=e.quote_revision AND q.expires_at>clock_timestamp()))
                    AND (e.fixture_id IS NULL OR EXISTS(SELECT 1 FROM authority.refund_fixture_sources f WHERE f.id=e.fixture_id AND f.revision=e.fixture_revision))) INTO result FROM authority.action_revisions a JOIN authority.intent_heads h ON h.intent_id=a.intent_id
            JOIN authority.evidence_revisions e ON e.action_id=a.id JOIN authority.policy_decisions d ON d.action_id=a.id
            JOIN authority.grant_heads gh ON gh.grant_id=a.grant_id JOIN authority.grant_versions gv ON gv.id=gh.version_id
            JOIN authority.tenant_control c ON c.tenant_id=a.tenant_id AND c.kind=a.kind
            WHERE a.tenant_id=person.tenant_id AND (a.requester=person.principal OR a.kind||'_APPROVER'=ANY(person.roles)
                OR EXISTS(SELECT 1 FROM authority.grants g WHERE g.id=a.grant_id AND g.issuer=person.principal)) AND a.id=actionid;
 IF result IS NULL THEN PERFORM authority.fail('SCOPE_DENIED',403); END IF;
 RETURN result;
END $$;
ALTER FUNCTION authority.action_detail(text,text) OWNER TO interlock_authority_owner;
REVOKE ALL ON FUNCTION authority.action_detail(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION authority.action_detail(text,text) TO interlock_api;
