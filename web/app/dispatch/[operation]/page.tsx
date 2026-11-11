import DispatchView from "../DispatchView";
export default async function Page({ params }: { params: Promise<{ operation: string }> }) {
  const { operation } = await params;
  return <DispatchView selected={operation} />;
}
