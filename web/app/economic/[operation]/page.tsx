import EconomicView from "../EconomicView";

export default async function Page({ params }: { params: Promise<{ operation: string }> }) {
  return <EconomicView operation={(await params).operation} />;
}
