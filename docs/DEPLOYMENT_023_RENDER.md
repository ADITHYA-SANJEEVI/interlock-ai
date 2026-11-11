# Free static Render deployment: operator handoff

Directive 023 authorizes only the free static historical explorer. No database,
worker, dynamic web service, payment/model endpoint, trial key or billing change.
Status at preflight: **BLOCKED HOSTING â€” Render authentication unavailable**.
The operator selected manual deployment handoff in this session. No service or
HTTPS URL is claimed; no further deployment authentication attempts are planned.

The Render integration was discovered but was not connected; no Render CLI,
environment credential or Render key in ignored profiles was found. Installation
or an account grant is a human checkpoint. Do not extract browser sessions or
use desktop automation to work around it.

## Exact resource boundary

| Setting | Required value |
| --- | --- |
| Resource | New **Static Site**, not a dynamic Web Service |
| Repository | https://github.com/ADITHYA-SANJEEVI/interlock-ai |
| Branch | main, initially verified at 99a707bac5263ffd090b57e72a7843495397eebc |
| Root directory | Blank / repository root |
| Build command | cd web && npm ci && npm run demo:build |
| Publish directory | ./dist/judge, relative to repository root |
| Node | NODE_VERSION=22.19.0 |
| Other environment | NEXT_TELEMETRY_DISABLED=1; no secrets |
| Auto deploy | Off; initial reviewed deployment only |
| Start command | None; never npm start or demo:serve on the hosted service |
| Rewrite | No catch-all rewrite to index.html |
| Billing | Free static hosting only; stop on upgrade/card/payment/overage commitment |

The audited render.yaml declares type web plus runtime static, the documented
Blueprint representation of a Static Site. It is not authorization for a paid
Node runtime. A Node process serves the local preview only; the hosted artifact
contains static HTML/JS/JSON/fonts, not a financial server.

Official references checked November 12:
[static sites](https://render.com/docs/static-sites),
[Blueprint fields](https://render.com/docs/blueprint-spec),
[Next static export](https://render.com/docs/deploy-nextjs-app),
[redirect rules](https://render.com/docs/redirects-rewrites).
The schema documents staticPublishPath relative to repo root and off as a valid
autoDeployTrigger. Free sites consume included workspace bandwidth/build minutes;
the owner must stop rather than accept an unapproved billing commitment.

## Manual steps if integration remains unavailable

1. Personally sign into https://dashboard.render.com/ and select the intended
   workspace. Check its included limits; do not change billing or accept upgrades.
2. Choose New > Static Site and the public Git repository above. Stop if an
   additional account grant is required and not acceptable to the operator.
3. Enter exactly the settings above. Keep Root Directory blank. Do not select the
   root Dockerfile, full Next app, private runtime, or a database blueprint.
4. Copy the security/cache headers from render.yaml. Do not add an SPA fallback.
5. Review the resource summary as free/static and deploy once. Record actual
   service/deploy ID, source commit and HTTPS URL. Do not repeatedly retry a
   credential or account error.
6. Return the actual HTTPS URL and deployment status to Codex. No key/password
   should be pasted into chat. A URL from this procedure is not verified until
   the independent checks below run.

## Required post-deployment acceptance

Check a fresh anonymous desktop/tablet/mobile context: landing, /demo/, direct
case/question links, refresh, Back, guided investigation, four source transitions,
recorded tools, comparison, JSON/HTML downloads and all source/media links. Verify
evidence bytes against the public manifest. Check CSP, MIME, cache headers, missing
assets, console errors and unexpected outbound requests. Financial/private/model
paths must return actual 404, never a rewritten public index. No auth, provider
or model secrets may occur in public output. Record each actual result.

Local route/headers testing is a preflight, not proof of CDN behavior, TLS or a
Render Linux build. If no authenticated deployment occurs, keep the gate blocked
and use the verified public-clone installation/artifact as the explicit fallback.
