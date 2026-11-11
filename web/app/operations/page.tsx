import Operations from "../commerce/Operations";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ operation?: string }>;
}) {
  const { operation } = await searchParams;
  return (
    <Operations
      initialOperation={
        typeof operation === "string" ? operation.slice(0, 100) : ""
      }
    />
  );
}
