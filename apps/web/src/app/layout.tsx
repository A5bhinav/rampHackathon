import type { Metadata } from "next";
import { IBM_Plex_Mono, Inter } from "next/font/google";
import { Suspense } from "react";
import { TourPanel } from "@/components/tour";
import "./globals.css";

// Spec 0041: Inter for everything, headings included (its optical-size axis gives large titles the
// tighter display cut), and IBM Plex Mono for account numbers, phone numbers, ids, hashes and the
// section markers. Self-hosted at build time.
const sans = Inter({ subsets: ["latin"], axes: ["opsz"], variable: "--font-sans", display: "swap" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  title: "Dialback",
  description: "Payments to a vendor whose bank details changed stay held until the vendor confirms on a provable number.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>{children}<Suspense fallback={null}><TourPanel /></Suspense></body>
    </html>
  );
}
