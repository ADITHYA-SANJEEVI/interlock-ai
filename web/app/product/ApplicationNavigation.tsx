"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type Snapshot } from "../paypal/contracts";
import styles from "./application.module.css";
import { operationContextEvent } from "./OperationContext";
export default function ApplicationNavigation() {
  const path = usePathname();
  const enabled = [
    "/overview",
    "/workspace",
    "/operations",
    "/intelligence",
  ].includes(path);
  const [scope, setScope] = useState<Snapshot>();
  const [loaded, setLoaded] = useState(false);
  const [context, setContext] = useState("");
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    const follow = (event: Event) => {
      const id = (event as CustomEvent).detail;
      if (typeof id === "string" && /^[A-Za-z0-9_-]{1,100}$/.test(id))
        setContext(id);
    };
    window.addEventListener(operationContextEvent, follow);
    const current = new URLSearchParams(window.location.search).get(
      "operation",
    );
    queueMicrotask(() => {
      if (active)
        setContext(
          current && /^[A-Za-z0-9_-]{1,100}$/.test(current) ? current : "",
        );
    });
    api<Snapshot>("paypal/authority")
      .then((value) => {
        if (active) {
          setScope(value);
          setLoaded(true);
        }
      })
      .catch(() => {
        if (active) {
          setScope(undefined);
          setLoaded(true);
        }
      });
    return () => {
      active = false;
      window.removeEventListener(operationContextEvent, follow);
    };
  }, [enabled, path]);
  if (!enabled) return null;
  const requester = scope?.roles.includes("PURCHASE_REQUESTER");
  const approver = scope?.roles.includes("PURCHASE_APPROVER");
  const binding = context ? "?operation=" + encodeURIComponent(context) : "";
  const links = [
    ["/overview", "Overview", true],
    [
      "/workspace" + binding + "#purchase-agent",
      "Purchase Agent",
      Boolean(requester),
    ],
    [
      "/workspace" + binding + "#requests-approvals",
      "Requests & Approvals",
      Boolean(requester || approver),
    ],
    ["/workspace" + binding + "#payments", "Payments", Boolean(scope)],
    ["/operations" + binding, "Operations", Boolean(scope)],
    [
      "/intelligence" + binding + "#investigation-agent",
      "Investigations",
      Boolean(scope),
    ],
    [
      "/intelligence" + binding + "#studio-report",
      "Analytics & Reports",
      Boolean(scope),
    ],
  ] as const;
  return (
    <div className={styles.bar}>
      <nav aria-label="Application areas">
        {links.map(([href, label, allowed]) =>
          allowed ? (
            <Link key={label} href={href}>
              {label}
            </Link>
          ) : (
            <span
              key={label}
              title="Requires the corresponding authenticated purchase role"
            >
              {label}
            </span>
          ),
        )}
      </nav>
      <small>
        {scope
          ? scope.roles
              .map(
                (r) =>
                  ({
                    PURCHASE_REQUESTER: "Requester",
                    PURCHASE_APPROVER: "Independent approver",
                    PURCHASE_ISSUER: "Issuer",
                  })[r] ?? r,
              )
              .join(" · ")
          : loaded
            ? "Sign in to access authorized areas"
            : "Checking scoped access…"}
      </small>
    </div>
  );
}
