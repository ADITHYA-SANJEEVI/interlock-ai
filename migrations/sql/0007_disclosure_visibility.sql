CREATE OR REPLACE FUNCTION dispatch.inspect(token text,opid text DEFAULT NULL) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,dispatch,pg_temp AS $$
DECLARE person authority.memberships; o dispatch.business_operations; result jsonb:='[]'::jsonb; details jsonb;
BEGIN
 person:=authority.actor(token);
 IF opid IS NOT NULL THEN PERFORM dispatch.user_operation(token,opid); END IF;
 FOR o IN SELECT b.* FROM dispatch.business_operations b JOIN authority.grants g ON g.id=b.grant_id WHERE b.tenant_id=person.tenant_id
 AND (opid IS NULL OR b.id=opid) AND (b.requester=person.principal OR g.issuer=person.principal OR 'PURCHASE_APPROVER'=ANY(person.roles)) ORDER BY b.created_at,b.id LOOP
 SELECT jsonb_build_object('operation',to_jsonb(o),'runtime',(to_jsonb(rt)-'provider_order')||jsonb_build_object('provider_order',CASE WHEN EXISTS(SELECT 1 FROM dispatch.checkout_exposures WHERE operation_id=o.id) THEN rt.provider_order ELSE NULL END),'grant_head',to_jsonb(gh),'reservation',to_jsonb(r),'exposure_state',to_jsonb(rs),
 'replay_contract',to_jsonb(c),'observation_permitted',EXISTS(SELECT 1 FROM dispatch.observation_memberships WHERE tenant_id=o.tenant_id AND principal=person.principal AND active),
 'disclosure_permitted',EXISTS(SELECT 1 FROM authority.grants WHERE id=o.grant_id AND issuer=person.principal),
 'age_seconds',extract(epoch FROM clock_timestamp()-o.created_at)::bigint,
 'attempts',coalesce((SELECT jsonb_agg(to_jsonb(a)||jsonb_build_object('runtime',to_jsonb(ar),'dispatch',to_jsonb(d)) ORDER BY a.number)
 FROM dispatch.transport_attempts a JOIN dispatch.attempt_runtime ar ON ar.attempt_id=a.id JOIN dispatch.dispatch_records d ON d.attempt_id=a.id WHERE a.operation_id=o.id),'[]'::jsonb),
 'exposures',coalesce((SELECT jsonb_agg(to_jsonb(e)-'instructions') FROM dispatch.checkout_exposures e WHERE e.operation_id=o.id),'[]'::jsonb),
 'duties',coalesce((SELECT jsonb_agg(to_jsonb(d)||jsonb_build_object('runtime',to_jsonb(h),'age_seconds',extract(epoch FROM clock_timestamp()-d.created_at)::bigint)) FROM dispatch.recovery_obligations d JOIN dispatch.duty_runtime h ON h.obligation_id=d.id WHERE d.operation_id=o.id),'[]'::jsonb),
 'observations',coalesce((SELECT jsonb_agg((to_jsonb(v)-'body')||jsonb_build_object('body',CASE WHEN EXISTS(SELECT 1 FROM dispatch.checkout_exposures WHERE operation_id=o.id) THEN v.body ELSE v.body-'id' END) ORDER BY v.observed_at,v.id) FROM dispatch.operation_observations v WHERE v.operation_id=o.id),'[]'::jsonb)) INTO details
 FROM dispatch.operation_runtime rt JOIN authority.grant_heads gh ON gh.grant_id=o.grant_id JOIN authority.reservations r ON r.id=o.reservation_id
 JOIN dispatch.reservation_exposure rs ON rs.reservation_id=r.id JOIN dispatch.replay_contracts c ON c.id=o.replay_contract_id WHERE rt.operation_id=o.id;
 result:=result||jsonb_build_array(details);
 END LOOP;
 RETURN jsonb_build_object('principal',person.principal,'tenant_id',person.tenant_id,'operations',result);
END $$;
