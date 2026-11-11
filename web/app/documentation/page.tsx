import Link from "next/link";
import Frame from "../paypal/Frame";
import styles from "../judge/judge.module.css";
export default function Documentation() {
  return (
    <Frame>
      <main className={styles.main}>
        <p className={styles.eyebrow}>
          DOCUMENTATION / COMPETITION RELEASE CANDIDATE
        </p>
        <h1>What this demo proves</h1>
        <div className={styles.summaryGrid}>
          <section>
            <h2>A protected purchase</h2>
            <p>
              The original authenticated application combines real
              model-selected planning, signed seller terms, deterministic
              policy, independent exact-action approval, atomic USD reservation,
              credential-isolated PayPal Orders v2 sandbox execution, separately
              authorized capture, canonical verification, accounting and
              controlled fulfillment.
            </p>
            <p>
              The public explorer reads a sanitized historical projection of two
              genuine operations. Its controls change report selection and local
              presentation only. They cannot spend, approve, reserve, capture or
              release money.
            </p>
            <Link href="/demo/">Explore both recorded cases →</Link>
          </section>
          <section>
            <h2>Honest evidence boundaries</h2>
            <p>
              The completed purchase records order 4FM556283T043144X and capture
              6C4956371B715513Y for USD 1.00. The incomplete purchase records
              order 5NV48695TN707743S as historically APPROVED, with no verified
              capture.
            </p>
            <p>
              Both retain separate legacy dispatch exposure. November 10
              provider observations are not current PayPal reads,
              bank-settlement proof or externally certified signatures. Missing
              timestamps and permission-limited approval details stay
              unavailable.
            </p>
          </section>
        </div>
        <section id="verification">
          <h2>Versioned and reproducible</h2>
          <p>
            Evidence version 2026-11-11.v1 is generated from authenticated
            source exports at checkpoint e6e545c. Closed nested field allowlists
            remove buyer and owner identities, credentials, sessions, raw
            transport and hidden model reasoning. The browser verifies the
            bundle against its SHA-256 manifest; this checks content
            consistency, not an external trust anchor.
          </p>
          <p>
            <a href="/evidence/v1/manifest.json" download>
              Manifest
            </a>{" "}
            ·{" "}
            <a href="/evidence/v1/bundle.json" download>
              Sanitized evidence and recorded tool traces
            </a>
          </p>
          <pre>
            {[
              "# From a clean checkout, Node.js 22+",
              "cd web",
              "npm ci",
              "npm run demo:build",
              "npm run demo:serve",
              "# Open http://127.0.0.1:3020",
              "# No .env, PostgreSQL, PayPal or model account is needed.",
            ].join("\n")}
          </pre>
        </section>
        <section id="studio">
          <h2>AG Studio and genuine AI</h2>
          <p>
            The authenticated local product uses official AG Studio 3.1.0,
            normalized source relationships, a custom Four Realities widget and
            the Studio Agent Framework. Its genuine seven-tool investigation
            changed an actual Studio report.
          </p>
          <p>
            The public demo provides authentic recorded planning and
            investigation calls, explicitly labelled as past executions. It
            invokes no model. Official Studio public hosting awaits the
            judging-period licence arrangement; the unapproved local evaluation
            key is excluded from the public export. Working local evaluation
            remains separate.
          </p>
          <p>
            AG Grid states that it will handle judging-period keys. The owner
            must confirm the applicable public evaluation configuration before
            deploying a Studio-enabled build. This is not a production licence.
          </p>
        </section>
        <section>
          <h2>Private application and release status</h2>
          <p>
            Full financial workflows require legitimate requester, independent
            approver, issuer, broker and seller roles, an isolated PostgreSQL
            deployment, separately configured sandbox credentials and permitted
            model access. No private account is supplied to a public visitor.
          </p>
          <p>
            The public explorer is ready for local review; hosting, repository
            publication and the final YouTube submission require owner
            authorization. It is sandbox-only. Render deployment is prepared
            separately; no hosted service or optional sponsor integration is
            claimed here.
          </p>
          <p>
            Original project code is MIT-licensed. Dependencies and fonts retain
            their own rights. The standalone public build includes{" "}
            <a href="/notices/project-license.txt">the project licence</a> and{" "}
            <a href="/notices/third-party.txt">full public runtime notices</a>.
          </p>
          <Link href="/sign-in/">Authenticated application access →</Link>
        </section>
      </main>
    </Frame>
  );
}
