import Workspace from "../paypal/Workspace";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ operation?: string }>;
}) {
  const { operation } = await searchParams;
  return (
    <Workspace
      initialOperation={
        typeof operation === "string" &&
        /^[A-Za-z0-9_-]{1,100}$/.test(operation)
          ? operation
          : ""
      }
    />
  );
}
