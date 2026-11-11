import type { Metadata } from "next";
import localFont from "next/font/local";
import "./styles.css";
const body = localFont({
  src: "./product/fonts/Manrope-Variable.ttf",
  weight: "200 800",
  display: "swap",
  variable: "--font-body",
});
const mono = localFont({
  src: "./product/fonts/IBMPlexMono-Regular.ttf",
  weight: "400",
  display: "swap",
  variable: "--font-mono",
  preload: false,
});
export const metadata: Metadata = {
  title: "Interlock AI | Payment controls for AI agents",
  description:
    "AI proposals, deterministic authority, independent approval and verifiable PayPal sandbox evidence.",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${body.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
