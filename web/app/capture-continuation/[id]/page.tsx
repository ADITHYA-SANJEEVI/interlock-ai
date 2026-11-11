import { notFound } from "next/navigation";
import CaptureContinuation from "../../paypal/CaptureContinuation";
import { continuationId } from "../../paypal/continuation-contract";

export const dynamic = "force-dynamic";
export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  if (
    process.env.PAYPAL_CAPTURE_CONTINUATION_ENABLED !== "true" ||
    !continuationId(id)
  )
    notFound();
  return <CaptureContinuation id={id} />;
}
