"use client";
import { useEffect } from "react";
export const operationContextEvent = "interlock-operation-context";
// Presentation context only, published after existing authenticated source reads.
// Changing this URL never loads, approves or sends a financial command.
export default function OperationContext({
  operation,
}: {
  operation?: string;
}) {
  useEffect(() => {
    if (!operation || !/^[A-Za-z0-9_-]{1,100}$/.test(operation)) return;
    const url = new URL(window.location.href);
    if (url.searchParams.get("operation") !== operation) {
      url.searchParams.set("operation", operation);
      window.history.replaceState(window.history.state, "", url);
    }
    window.dispatchEvent(
      new CustomEvent(operationContextEvent, { detail: operation }),
    );
  }, [operation]);
  return null;
}
