"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import Frame from "../paypal/Frame";
import styles from "./landing.module.css";

const steps = [
  [
    "AI request",
    "A proposal, not permission",
    "The model extracts a product, beneficiary and limit. It cannot approve spending.",
  ],
  [
    "Signed terms",
    "A price with a defined scope",
    "The controlled seller signs the exact product, destination, currency and expiry.",
  ],
  [
    "Policy",
    "Deterministic spending rules",
    "The financial kernel checks the grant, beneficiary, budget and current authority.",
  ],
  [
    "Independent approval",
    "A separate human decision",
    "A different principal reviews the exact action before budget can be reserved.",
  ],
  [
    "PayPal",
    "Buyer consent and protected capture",
    "Checkout is followed by independent capture authorization and canonical verification.",
  ],
  [
    "Verified records",
    "Separate sources, explicit agreement",
    "Provider observations, journals and seller fulfillment remain independently inspectable.",
  ],
];

function TrustOrbit({
  active,
  select,
  compact = false,
}: {
  active: number;
  select(value: number): void;
  compact?: boolean;
}) {
  const scene = useRef<HTMLDivElement>(null);
  const id = useId().replaceAll(":", "");
  return (
    <div
      className={`${styles.orbit} ${compact ? styles.compactOrbit : ""}`}
      aria-label="Illustrative purchase control flow"
      data-orbit-step={active}
    >
      <div className={styles.orbitTop}>
        <span>INTERLOCK / AUTHORIZATION SYSTEM</span>
        <span>ILLUSTRATION</span>
      </div>
      <div
        ref={scene}
        className={styles.orbitWorld}
        onPointerMove={(event) => {
          if (
            event.pointerType !== "mouse" ||
            matchMedia("(prefers-reduced-motion: reduce)").matches
          )
            return;
          const box = event.currentTarget.getBoundingClientRect();
          scene.current?.style.setProperty(
            "--tilt-x",
            `${((event.clientY - box.top) / box.height - 0.5) * -5}deg`,
          );
          scene.current?.style.setProperty(
            "--tilt-y",
            `${((event.clientX - box.left) / box.width - 0.5) * 7}deg`,
          );
        }}
        onPointerLeave={() => {
          scene.current?.style.setProperty("--tilt-x", "0deg");
          scene.current?.style.setProperty("--tilt-y", "0deg");
        }}
      >
        <div className={styles.orbitGlow} />
        <svg
          className={styles.orbitPaths}
          viewBox="0 0 720 580"
          fill="none"
          aria-hidden="true"
        >
          <defs>
            <linearGradient
              id={id + "-stroke"}
              x1="70"
              y1="100"
              x2="660"
              y2="490"
              gradientUnits="userSpaceOnUse"
            >
              <stop stopColor="#b7a0ff" stopOpacity=".05" />
              <stop offset=".46" stopColor="#c3b5ff" />
              <stop offset="1" stopColor="#a4e8d5" stopOpacity=".12" />
            </linearGradient>
            <radialGradient id={id + "-ground"}>
              <stop stopColor="#7b57db" stopOpacity=".18" />
              <stop offset="1" stopColor="#7b57db" stopOpacity="0" />
            </radialGradient>
          </defs>
          <ellipse
            cx="365"
            cy="365"
            rx="295"
            ry="190"
            fill={`url(#${id}-ground)`}
          />
          <g stroke="#aa98f5" strokeOpacity=".12">
            <ellipse
              cx="360"
              cy="290"
              rx="305"
              ry="156"
              transform="rotate(-24 360 290)"
            />
            <ellipse
              cx="360"
              cy="290"
              rx="280"
              ry="140"
              transform="rotate(27 360 290)"
            />
            <ellipse
              cx="360"
              cy="290"
              rx="215"
              ry="260"
              transform="rotate(64 360 290)"
            />
          </g>
          <ellipse
            className={styles.signalPath}
            cx="360"
            cy="290"
            rx="305"
            ry="156"
            transform="rotate(-24 360 290)"
            stroke={`url(#${id}-stroke)`}
            strokeWidth="1.6"
          />
          <ellipse
            className={styles.signalEcho}
            cx="360"
            cy="290"
            rx="280"
            ry="140"
            transform="rotate(27 360 290)"
            stroke="#b8a3ff"
            strokeWidth="2"
            strokeDasharray="2 480"
          />
          <path
            d="M360 90V180M185 225 270 267M450 277 550 205M365 405 365 485"
            stroke="#b5a5ec"
            strokeOpacity=".14"
            strokeDasharray="3 6"
          />
        </svg>
        <div className={styles.coreShadow} />
        <div className={styles.coreStack} aria-hidden="true">
          <div className={styles.coreBase} />
          <div className={styles.corePlate} />
          <div className={styles.coreFace}>
            <svg viewBox="0 0 100 100" fill="none">
              <path
                d="M37 16H18v48a18 18 0 0 0 18 18h28V43H37V16Z"
                stroke="currentColor"
                strokeWidth="5"
              />
              <path
                d="M64 84h18V36a18 18 0 0 0-18-18H37v39h27v27Z"
                stroke="currentColor"
                strokeWidth="5"
              />
            </svg>
            <span>INTERLOCK</span>
            <i />
          </div>
        </div>
        <div className={styles.axisLabel}>
          AUTHORITY
          <br />
          <b>SEPARATE FROM INTENT</b>
        </div>
        {steps.map(([name, subtitle], index) => (
          <button
            key={name}
            type="button"
            className={`${styles.orbitNode} ${styles["node" + index]} ${active === index ? styles.nodeActive : ""}`}
            aria-label={`Explore ${name}`}
            aria-pressed={active === index}
            onClick={() => select(index)}
            onFocus={() => select(index)}
          >
            <span className={styles.nodeIndex}>
              {String(index + 1).padStart(2, "0")}
            </span>
            <span>
              <strong>{name}</strong>
              <small>{subtitle}</small>
            </span>
            <i />
          </button>
        ))}
        <span className={styles.coordinate}>USD / EXACT TERMS</span>
      </div>
      <div className={styles.orbitLegend}>
        <span>{String(active + 1).padStart(2, "0")} / 06</span>
        <p>{steps[active][2]}</p>
      </div>
      <p className={styles.illustrationNote}>
        Illustrative process. Not a current payment or live status.
      </p>
    </div>
  );
}

export default function Landing() {
  const root = useRef<HTMLElement>(null);
  const [active, setActive] = useState(0);
  const manualSelection = useRef(false);
  const selectStep = (value: number) => {
    manualSelection.current = true;
    setActive(value);
  };
  useEffect(() => {
    const host = root.current;
    if (!host) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries)
          if (entry.isIntersecting && !manualSelection.current)
            setActive(
              Number((entry.target as HTMLElement).dataset.narrativeStep),
            );
      },
      { rootMargin: "-30% 0px -35% 0px", threshold: 0 },
    );
    host
      .querySelectorAll("[data-narrative-step]")
      .forEach((el) => observer.observe(el));
    let frame = 0;
    const scroll = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        host.style.setProperty(
          "--page-progress",
          String(
            Math.min(
              1,
              window.scrollY / Math.max(1, host.offsetHeight - innerHeight),
            ),
          ),
        );
        frame = 0;
      });
    };
    const onScroll = () => {
      manualSelection.current = false;
      scroll();
    };
    addEventListener("scroll", onScroll, { passive: true });
    scroll();
    return () => {
      observer.disconnect();
      removeEventListener("scroll", onScroll);
      cancelAnimationFrame(frame);
    };
  }, []);
  return (
    <Frame>
      <main ref={root} className={styles.landing}>
        <div className={styles.progress} aria-hidden="true" />
        <section className={styles.hero} data-narrative-step="0">
          <div className={styles.heroCopy}>
            <p className={styles.eyebrow}>
              <span />
              CONTROLLED AGENTIC PAYMENTS
            </p>
            <h1>
              Payment controls
              <br />
              for <em>AI agents.</em>
            </h1>
            <p className={styles.intro}>
              Review purchase requests, enforce spending rules, and reconcile
              PayPal payments against accounting and fulfillment records.
            </p>
            <div className={styles.actions}>
              <Link className={styles.primary} href="/workspace">
                Open workspace <span aria-hidden="true">↗</span>
              </Link>
              <Link className={styles.quiet} href="#controls">
                Explore the system <span aria-hidden="true">↓</span>
              </Link>
            </div>
            <div className={styles.heroFoot}>
              <span>Scoped proposals</span>
              <i />
              <span>Independent approval</span>
              <i />
              <span>Canonical verification</span>
            </div>
          </div>
          <TrustOrbit active={active} select={selectStep} />
          <div className={styles.heroIndex}>
            <span>01 — PURCHASE CONTROLS</span>
            <span>02 — VERIFIED EXECUTION</span>
            <span>03 — FINANCIAL INTELLIGENCE</span>
          </div>
        </section>
        <section
          id="controls"
          className={styles.narrative}
          aria-label="How purchasing controls work"
        >
          <div className={styles.stickyScene}>
            <p className={styles.eyebrow}>
              A REQUEST BECOMES AN AUTHORIZED ACTION
            </p>
            <TrustOrbit active={active} select={selectStep} compact />
          </div>
          <div className={styles.chapters}>
            <article data-narrative-step="1">
              <span className={styles.chapterNumber}>01 / DEFINE</span>
              <h2>
                Agree on the purchase
                <br />
                before money moves.
              </h2>
              <p>
                An AI proposal becomes a signed seller quote with a specific
                product, beneficiary, amount and expiry. Deterministic policy
                evaluates those exact terms.
              </p>
              <div className={styles.termLine}>
                <span>Product & beneficiary</span>
                <span>USD amount</span>
                <span>Signed expiry</span>
              </div>
            </article>
            <article data-narrative-step="3">
              <span className={styles.chapterNumber}>02 / AUTHORIZE</span>
              <h2>
                Keep the decision
                <br />
                independent.
              </h2>
              <p>
                A separate human approves the exact request. Budget is reserved
                atomically. The agent cannot grant itself permission or access
                payment credentials.
              </p>
              <div className={styles.separation}>
                <span>Requester</span>
                <i />
                <b>Independent approver</b>
                <i />
                <span>Protected broker</span>
              </div>
            </article>
            <article data-narrative-step="4">
              <span className={styles.chapterNumber}>03 / EXECUTE</span>
              <h2>
                PayPal checkout.
                <br />
                Verified capture.
              </h2>
              <p>
                The buyer consents directly on PayPal. Capture requires separate
                protected authority, and the server verifies the result through
                PayPal’s canonical API.
              </p>
              <p className={styles.smallNote}>
                A redirect is navigation. It is never proof of payment.
              </p>
            </article>
          </div>
        </section>
        <section className={styles.records} data-narrative-step="5">
          <div className={styles.recordHeading}>
            <div>
              <p className={styles.eyebrow}>INDEPENDENT FINANCIAL RECORDS</p>
              <h2>
                A payment has more
                <br />
                than one source of truth.
              </h2>
            </div>
            <p>
              Compare what was authorized, what PayPal observed, what accounting
              booked, and what the seller fulfilled. Keep missing evidence
              visible.
            </p>
          </div>
          <div
            className={styles.evidenceWeave}
            aria-label="Illustrative evidence comparison"
          >
            <div className={styles.weaveAxis}>
              <span>EXACT PURCHASE</span>
              <svg viewBox="0 0 100 32" aria-hidden="true">
                <path
                  d="M0 16H80m-8-7 9 7-9 7"
                  fill="none"
                  stroke="currentColor"
                />
              </svg>
              <span>INDEPENDENT RECORDS</span>
            </div>
            {[
              ["I", "Intended", "Signed terms & approval", "Authority record"],
              [
                "II",
                "Observed",
                "Canonical PayPal response",
                "Provider observation",
              ],
              [
                "III",
                "Booked",
                "Balanced journal entries",
                "Accounting record",
              ],
              [
                "IV",
                "Fulfilled",
                "Authenticated seller evidence",
                "Fulfillment attestation",
              ],
            ].map(([n, title, text, label]) => (
              <div className={styles.weaveRow} key={n}>
                <span>{n}</span>
                <h3>{title}</h3>
                <p>{text}</p>
                <div className={styles.waveLine}>
                  <i />
                  <i />
                  <i />
                </div>
                <small>{label}</small>
              </div>
            ))}
            <p className={styles.illustrationNote}>
              Conceptual source map · no transaction results displayed
            </p>
          </div>
        </section>
        <section className={styles.investigation}>
          <div>
            <p className={styles.eyebrow}>FINANCIAL INTELLIGENCE / AG STUDIO</p>
            <h2>
              Ask where the
              <br />
              records disagree.
            </h2>
            <p>
              The investigation agent selects read-only tools, checks
              authenticated evidence and returns cited findings. Open the
              underlying records in AG Studio to inspect the result.
            </p>
            <Link className={styles.primary} href="/intelligence">
              Open Financial Intelligence <span aria-hidden="true">↗</span>
            </Link>
          </div>
          <div className={styles.investigationMap}>
            <div className={styles.exampleQuestion}>
              <span>INVESTIGATION EXAMPLE</span>
              <p>“The buyer approved. Why isn’t this purchase booked?”</p>
            </div>
            <div className={styles.toolScope}>
              <span>READ-ONLY TOOL CAPABILITIES</span>
              {[
                "Inspect recorded PayPal state",
                "Check authenticated facts & journals",
                "Compare seller and reconciliation evidence",
              ].map((text, i) => (
                <div key={text}>
                  <b>0{i + 1}</b>
                  <span>{text}</span>
                  <i>↗</i>
                </div>
              ))}
            </div>
            <p className={styles.smallNote}>
              Illustrative question. Actual tools and findings appear only after
              an authenticated investigation.
            </p>
          </div>
        </section>
        <section className={styles.closing}>
          <div>
            <p className={styles.eyebrow}>WORKING SANDBOX PROTOTYPE</p>
            <h2>Inspect the working product.</h2>
            <p>
              The local demonstration includes a genuine PayPal sandbox
              purchase, verified accounting and seller fulfillment, and a
              separate incomplete case for investigation.
            </p>
          </div>
          <div>
            <Link className={styles.primary} href="/workspace">
              Enter workspace ↗
            </Link>
            <p>
              Authenticated local access required.
              <br />
              No production payments.
            </p>
          </div>
        </section>
      </main>
    </Frame>
  );
}
