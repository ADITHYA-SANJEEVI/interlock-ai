"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import styles from "./theme.module.css";
export default function Navigation() {
  const path = usePathname();
  const application = [
    "/overview",
    "/workspace",
    "/operations",
    "/intelligence",
  ].includes(path);
  return (
    <nav className={styles.navigation} aria-label="Main navigation">
      {(application
        ? [
            ["/overview", "Application"],
            ["/demo", "Explore Demo"],
            ["/documentation", "Documentation"],
          ]
        : [
            ["/", "Product"],
            ["/demo", "Explore Demo"],
            ["/documentation", "Documentation"],
            ["/sign-in", "Sign In"],
          ]
      ).map(([href, label]) => (
        <Link
          key={href}
          href={href}
          aria-current={
            path === href || (application && href === "/overview")
              ? "page"
              : undefined
          }
        >
          {label}
        </Link>
      ))}
    </nav>
  );
}
