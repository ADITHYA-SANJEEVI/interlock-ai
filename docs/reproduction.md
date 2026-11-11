# Reproduction and operational boundaries

## Public explorer

Use Node.js 22+ (locally tested with 22.19.0), npm and the committed lockfile:

~~~sh
cd web
npm ci
npm run demo:build
npm run demo:serve
~~~

Open http://127.0.0.1:3020/demo/. Windows needs its standard ComSpec environment
variable for npm scripts. No private profile, PostgreSQL, PayPal credentials,
model subscription, saved session or Studio key is required.

The builder creates dist/judge from an explicit source/route allowlist. Private,
financial and live-model API routes are absent. The static server refuses
writes. Private environment files and local evaluation builds are never copied.

## Safe deterministic checks

~~~sh
node --test tools/release/evidence.test.cjs tools/release/investigation.test.cjs
cd web
npm run typecheck
npm run lint
npx playwright install chromium
npx playwright test --config playwright.refinement.config.ts
~~~

Linux runners may need npx playwright install --with-deps chromium. Python
offline tests require Python 3.12+, requirements.lock and PYTHONPATH=backend.
GitHub Actions is not configured. Run these checks locally; no financial
credentials are required.

## Separate protected runtime

The source includes reference/additive migrations, service factories and guarded
SQL acceptance cases. It does not distribute private databases or privileged
accounts. A fresh protected evaluation requires these separately reviewed steps:

1. Establish disposable PostgreSQL 18.3. Independently verify its actual control
   system identifier, version, directory, listener/process, port and inventory.
   Never substitute an existing project database.
2. Review tools/hackathon/disposable_binding.py and provisioning helpers. The
   original Windows bindings intentionally fail on a different machine. They
   are provenance/safety guards, not portable defaults.
3. Initialize dedicated control/seller/reference databases, non-login owners
   and distinct API/broker/worker/economic/seller/reference credentials. Review
   the original migration history; execute only against the new isolated target.
4. Establish legitimate tenant/requester/issuer/independent-approver identities
   and separately authorized seller terms. Historical quotes/approvals are not
   current authority.
5. Run synthetic financial/role/race acceptance before any separately authorized
   sandbox transaction. Never run these suites against recorded demo data.
6. Start the session, API, broker, seller and economic processes under their
   runtime permissions. Only the broker receives merchant credentials. Planning
   and investigation use scoped READ ONLY access, never broker secrets.
7. Supply your own permitted model configuration and sandbox credentials in
   ignored profiles. Humans perform independent approval and PayPal consent.

This is not a one-command protected-stack bootstrap. The public explorer is the
independently exercised portable reproduction path. Original Compose defaults
are local fixtures, never hosted credentials. No migration/payment is invoked
by the public build or documented local checks.

Local official Studio additionally requires applicable evaluation/use rights.
Register a local key through its documented provider API; never upload the
licence-bearing evaluation build. Public Studio awaits the judging arrangement.
