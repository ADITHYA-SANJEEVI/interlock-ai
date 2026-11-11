"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import Frame from "../paypal/Frame";
import { commerce, readCase, type CaseFile } from "./contracts";
import { usd } from "../paypal/contracts";
import styles from "../judge/judge.module.css";
export default function Overview() {
  const [records, setRecords] = useState<{ id: string; value?: CaseFile }[]>(
      [],
    ),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    commerce<{ transactions: { operation_id: string }[] }>("transactions")
      .then(async (data) => {
        const values = await Promise.all(
          data.transactions.slice(0, 20).map(async (t) => {
            try {
              return {
                id: t.operation_id,
                value: readCase(
                  await commerce<CaseFile>("cases/" + t.operation_id),
                  t.operation_id,
                ),
              };
            } catch {
              return { id: t.operation_id };
            }
          }),
        );
        if (active) {
          setRecords(values);
          setLoading(false);
        }
      })
      .catch(() => {
        if (active) {
          setError(
            "Authorized operations are unavailable. Sign in with an existing purchase role; public historical cases are separate.",
          );
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);
  return (
    <Frame>
      <main className={styles.main}>
        <p className={styles.eyebrow}>
          AUTHENTICATED APPLICATION / SOURCE-BACKED OVERVIEW
        </p>
        <h1>Operations that need attention</h1>
        <p>
          Recorded outcomes and outstanding obligations in your authorized
          scope. Source refreshes do not contact PayPal or create financial
          commands.
        </p>
        {loading && <p role="status">Loading accessible source records…</p>}
        {error && (
          <p role="alert" className={styles.warning}>
            {error} <Link href="/workspace">Open sign-in</Link>
          </p>
        )}
        <div className={styles.tableWrap}>
          <table>
            <caption>Accessible operation summaries ({records.length})</caption>
            <thead>
              <tr>
                <th>Operation</th>
                <th>Amount</th>
                <th>Recorded outcome</th>
                <th>Outstanding review</th>
                <th>Open case</th>
              </tr>
            </thead>
            <tbody>
              {records.map(({ id, value: c }) => (
                <tr key={id}>
                  <td>
                    <code>{id}</code>
                  </td>
                  <td>{c ? usd(c.commercial.amount_minor) : "Unavailable"}</td>
                  <td>
                    {c?.status.replaceAll("_", " ") ?? "Source unavailable"}
                  </td>
                  <td>
                    {c?.next_action.replaceAll("_", " ") ?? "No conclusion"}
                    {c?.reconciliation.findings.map((f) => (
                      <p key={f.id}>
                        {f.class} · {f.state}
                      </p>
                    ))}
                  </td>
                  <td>
                    <Link href={"/operations?operation=" + id}>
                      Case file →
                    </Link>
                    <br />
                    <Link href={"/intelligence?operation=" + id}>
                      Investigate →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>
          Up to twenty accessible operations are inspected. Missing source
          packages remain unavailable; a stored COMPLETE reconciliation run does
          not close a separate exposure or open finding.
        </p>
      </main>
    </Frame>
  );
}
