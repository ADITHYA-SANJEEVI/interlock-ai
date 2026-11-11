import FinancialIntelligence from "../paypal/FinancialIntelligence";

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ operation?: string }>;
}) {
  const { operation } = await searchParams;
  return (
    <FinancialIntelligence
      licenseKey={process.env.AG_STUDIO_LICENSE_KEY}
      initialOperation={
        typeof operation === "string" ? operation.slice(0, 100) : ""
      }
    />
  );
}
