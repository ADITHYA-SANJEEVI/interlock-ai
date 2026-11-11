"use client";
// The static public builder replaces this module with a dependency-free notice.
// No licence or privileged runtime is present in that separate export.
import dynamic from "next/dynamic";
import type { PublicCase, PublicEvidence } from "./public-evidence";
const RecordedStudio = dynamic(() => import("./RecordedStudio"), {
  ssr: false,
});
export default function StudioSlot(props: {
  record: PublicCase;
  evidence: PublicEvidence;
  licenseKey?: string;
}) {
  return props.licenseKey ? (
    <RecordedStudio {...props} />
  ) : (
    <section>
      <h2>Official AG Studio</h2>
      <p>
        Public Studio hosting awaits the judging-period licence arrangement. The
        working local evaluation supports report pages, filtering, layout
        editing, the custom Four Realities widget and Agent Framework
        integration. The case explorer and evidence exports remain available
        here.
      </p>
      <a href="/documentation/#studio">Integration and licence details</a>
    </section>
  );
}
