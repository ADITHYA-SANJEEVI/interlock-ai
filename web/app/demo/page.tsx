import DemoExplorer from "../judge/DemoExplorer";
export const dynamic = "force-dynamic";
export default function DemoPage() {
  return <DemoExplorer licenseKey={process.env.AG_STUDIO_LICENSE_KEY} />;
}
