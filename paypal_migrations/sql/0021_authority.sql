-- Opt-in extension. Existing migration files and INR functions stay unchanged.
CREATE SCHEMA paypal_v1;
REVOKE ALL ON SCHEMA paypal_v1 FROM PUBLIC;
CREATE TABLE paypal_v1.deployment (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 enabled boolean NOT NULL DEFAULT false,
 disposable_acceptance_sha256 text,
 CHECK(NOT enabled OR disposable_acceptance_sha256 ~ '^[a-f0-9]{64}$')
);
INSERT INTO paypal_v1.deployment(singleton) VALUES(true);
-- Only the human-verified M0 US sandbox merchant is in scope for this version.
CREATE TABLE paypal_v1.tenants (
 tenant_id text PRIMARY KEY REFERENCES public.tenants(id),
 merchant_id text NOT NULL CHECK(merchant_id='JB7TR6VHB4GTQ'),
 currency text NOT NULL DEFAULT 'USD' CHECK(currency='USD'),
 seller_country text NOT NULL CHECK(seller_country='US'),
 buyer_country text NOT NULL CHECK(buyer_country='US'),
 profile_id text NOT NULL CHECK(profile_id='us'),
 mapping_version text NOT NULL DEFAULT 'paypal-canonical-v1' CHECK(mapping_version='paypal-canonical-v1')
);
CREATE FUNCTION paypal_v1.new_tenant_only() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM authority.grants WHERE tenant_id=NEW.tenant_id)
 OR EXISTS(SELECT 1 FROM authority.policies WHERE tenant_id=NEW.tenant_id)
 OR EXISTS(SELECT 1 FROM authority.seller_quotes WHERE tenant_id=NEW.tenant_id)
 OR EXISTS(SELECT 1 FROM dispatch.integrations WHERE tenant_id=NEW.tenant_id) THEN
  PERFORM authority.fail('PAYPAL_REQUIRES_NEW_USD_TENANT'); END IF;
 IF EXISTS(SELECT 1 FROM authority.memberships WHERE tenant_id=NEW.tenant_id AND
  roles && ARRAY['REFUND_ISSUER','REFUND_REQUESTER','REFUND_APPROVER']::text[]) THEN
  PERFORM authority.fail('PAYPAL_V1_PURCHASE_ROLES_ONLY'); END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER new_usd_tenant BEFORE INSERT ON paypal_v1.tenants
 FOR EACH ROW EXECUTE FUNCTION paypal_v1.new_tenant_only();
CREATE TRIGGER fixed_usd_tenant BEFORE UPDATE OR DELETE ON paypal_v1.tenants
 FOR EACH ROW EXECUTE FUNCTION authority.immutable();
CREATE FUNCTION paypal_v1.purchase_membership_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM paypal_v1.tenants WHERE tenant_id=NEW.tenant_id) AND
 NEW.roles && ARRAY['REFUND_ISSUER','REFUND_REQUESTER','REFUND_APPROVER']::text[] THEN
  PERFORM authority.fail('PAYPAL_V1_PURCHASE_ROLES_ONLY'); END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER paypal_purchase_roles BEFORE INSERT OR UPDATE ON authority.memberships
 FOR EACH ROW EXECUTE FUNCTION paypal_v1.purchase_membership_guard();

-- Exact denomination is enforced at persistence even if a legacy route is used.
ALTER TABLE authority.grants DROP CONSTRAINT grants_currency_check;
ALTER TABLE authority.grants ADD CHECK(currency IN ('INR','USD'));
ALTER TABLE authority.seller_quotes DROP CONSTRAINT seller_quotes_currency_check;
ALTER TABLE authority.seller_quotes ADD CHECK(currency IN ('INR','USD'));
ALTER TABLE authority.action_revisions DROP CONSTRAINT action_revisions_currency_check;
ALTER TABLE authority.action_revisions ADD CHECK(currency IN ('INR','USD'));
ALTER TABLE authority.reservations DROP CONSTRAINT reservations_currency_check;
ALTER TABLE authority.reservations ADD CHECK(currency IN ('INR','USD'));
ALTER TABLE dispatch.business_operations DROP CONSTRAINT business_operations_currency_check;
ALTER TABLE dispatch.business_operations ADD CHECK(currency IN ('INR','USD'));
ALTER TABLE dispatch.business_operations DROP CONSTRAINT business_operations_provider_mode_check;
ALTER TABLE dispatch.business_operations ADD CHECK(provider_mode IN ('REFERENCE_SYNTHETIC','RAZORPAY_TEST','PAYPAL_SANDBOX'));
ALTER TABLE dispatch.integrations DROP CONSTRAINT integrations_adapter_check;
ALTER TABLE dispatch.integrations ADD CHECK(adapter IN ('REFERENCE_SYNTHETIC','RAZORPAY_TEST','PAYPAL_SANDBOX'));
INSERT INTO dispatch.replay_contracts VALUES
 ('paypal-orders-v2-v1','PAYPAL_SANDBOX','paypal-orders-v2-v1','NEVER_AUTOMATIC_REPLAY',false,0);

CREATE FUNCTION paypal_v1.denomination_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE denomination text; BEGIN
 SELECT currency INTO denomination FROM paypal_v1.tenants WHERE tenant_id=NEW.tenant_id;
 IF NEW.currency IS DISTINCT FROM coalesce(denomination,'INR') THEN
  PERFORM authority.fail('TENANT_DENOMINATION_MISMATCH'); END IF;
 RETURN NEW;
END $$;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['authority.grants','authority.seller_quotes','authority.action_revisions',
 'authority.reservations','dispatch.business_operations'] LOOP
  EXECUTE format('CREATE TRIGGER paypal_denomination BEFORE INSERT ON %s FOR EACH ROW EXECUTE FUNCTION paypal_v1.denomination_guard()',t);
 END LOOP;
END $$;
CREATE FUNCTION paypal_v1.exact_policy_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM paypal_v1.tenants WHERE tenant_id=NEW.tenant_id) AND
 (NEW.approval_above<>0 OR NOT EXISTS(SELECT 1 FROM authority.policies WHERE id=NEW.policy_id AND kind='PURCHASE')) THEN
  PERFORM authority.fail('PAYPAL_ALWAYS_REQUIRES_INDEPENDENT_APPROVAL'); END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER paypal_exact_policy BEFORE INSERT ON authority.policy_versions
 FOR EACH ROW EXECUTE FUNCTION paypal_v1.exact_policy_guard();

-- The versioned facade uses the existing approval, admission and lifecycle code.
-- Only preparation has a mechanically checked USD specialization (next file).
CREATE FUNCTION paypal_v1.command(token text,family text,body jsonb,expected bigint,idem text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,paypal_v1,pg_temp AS $$
DECLARE person authority.memberships; denomination text; result jsonb; BEGIN
 person:=authority.actor(token);
 SELECT currency INTO denomination FROM paypal_v1.tenants WHERE tenant_id=person.tenant_id;
 IF denomination IS DISTINCT FROM 'USD' THEN PERFORM authority.fail('USD_TENANT_REQUIRED',403); END IF;
 IF family IN ('create-grant','publish-policy') AND body->>'kind' IS DISTINCT FROM 'PURCHASE' THEN
  PERFORM authority.fail('PAYPAL_PURCHASE_ONLY',422); END IF;
 IF family='publish-policy' AND body->>'approval_above' IS DISTINCT FROM '0' THEN
  PERFORM authority.fail('PAYPAL_ALWAYS_REQUIRES_INDEPENDENT_APPROVAL',422); END IF;
 IF family IN ('create-grant','propose') THEN
  IF family='propose' AND NOT EXISTS(
   SELECT 1 FROM paypal_v1.proposal_quotes pq JOIN paypal_v1.proposals pr ON pr.id=pq.proposal_id
   WHERE pq.quote_id=body->>'quote_id' AND pr.grant_id=body->>'grant_id'
   AND pr.tenant_id=person.tenant_id AND pr.principal=person.principal) THEN
   PERFORM authority.fail('MODEL_PROPOSAL_SIGNED_QUOTE_LINK_REQUIRED',403); END IF;
  result:=authority.paypal_prepare_v1(token,family,body,expected,idem);
 ELSE
  result:=authority.command(token,family,body,expected,idem);
 END IF;
 RETURN result||jsonb_build_object('currency','USD','contract_version','paypal-v1','policy_result',
  (SELECT jsonb_build_object('decision_id',id,'allowed',allowed,'approval_required',approval_required,'reasons',reasons)
   FROM authority.policy_decisions WHERE id=result->>'decision_id'), 'operation_id',
  (SELECT id FROM dispatch.business_operations WHERE action_id=result->>'action_id' AND adapter='PAYPAL_SANDBOX'));
END $$;
