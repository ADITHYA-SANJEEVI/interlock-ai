import Link from "next/link";
import Frame from "../paypal/Frame";
import styles from "../judge/judge.module.css";
export default function SignIn() {
  return (
    <Frame>
      <main className={styles.main}>
        <p className={styles.eyebrow}>AUTHENTICATED APPLICATION</p>
        <h1>Separate access. Independent authority.</h1>
        <p>
          The full application requires an account provisioned by its operator.
          Public demo visitors are never issued financial roles or private
          credentials.
        </p>
        {process.env.NEXT_PUBLIC_JUDGE_ONLY === "true" ? (
          <p>
            This standalone build has no login server or financial services. Use
            Explore Demo without an account; contact the entrant for a
            separately arranged authenticated evaluation.
          </p>
        ) : (
          <p>
            <Link href="/workspace">Open the secure workspace sign-in →</Link>
          </p>
        )}
        <p>
          <Link href="/demo/">Explore historical sandbox evidence →</Link>
        </p>
        <p>
          <Link href="/documentation/">
            Read setup and evidence limitations →
          </Link>
        </p>
      </main>
    </Frame>
  );
}
