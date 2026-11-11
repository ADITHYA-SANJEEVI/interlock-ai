import Link from "next/link";
import Navigation from "../product/Navigation";
import ApplicationNavigation from "../product/ApplicationNavigation";
import styles from "../product/theme.module.css";

export function Mark() {
  return (
    <span className={styles.mark} aria-hidden="true">
      <svg viewBox="0 0 36 36" fill="none">
        <path
          d="M13 4H5v19a8 8 0 0 0 8 8h10V14H13V4Z"
          stroke="currentColor"
          strokeWidth="2.4"
        />
        <path
          d="M23 32h8V13a8 8 0 0 0-8-8H13v17h10v10Z"
          stroke="currentColor"
          strokeWidth="2.4"
        />
      </svg>
    </span>
  );
}
export default function Frame({
  children,
}: {
  children: React.ReactNode;
  dark?: boolean;
}) {
  return (
    <div className={styles.shell}>
      <a className={styles.skip} href="#main-content">
        Skip to content
      </a>
      <header className={styles.header}>
        <Link
          href="/"
          className={styles.brand}
          aria-label="Interlock AI product"
        >
          <Mark />
          interlock<small>AI</small>
        </Link>
        <Navigation />
        <span className={styles.environment}>
          <i />
          PayPal Sandbox
        </span>
      </header>
      <ApplicationNavigation />
      <div id="main-content" className={styles.main} tabIndex={-1}>
        {children}
      </div>
      <footer className={styles.footer}>
        <span>
          <strong>Interlock AI</strong> Payment controls &amp; investigation
        </span>
        <span>Sandbox demonstration · Local evaluation</span>
      </footer>
    </div>
  );
}
