-- Narrow helper grant: the authority definer hashes command bodies; API roles
-- retain no direct access to this private dispatch helper.
GRANT EXECUTE ON FUNCTION dispatch.hash(jsonb) TO interlock_authority_owner;
