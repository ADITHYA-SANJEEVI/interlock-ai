import Workspace from "../Workspace";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token } = await searchParams;
  return (
    <Workspace
      returnToken={
        typeof token === "string" && /^[A-Z0-9]{10,32}$/.test(token)
          ? token
          : "INVALID"
      }
    />
  );
}
