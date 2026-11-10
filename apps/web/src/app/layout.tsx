import type { Metadata } from "next";
import { IBM_Plex_Mono, Instrument_Serif, Schibsted_Grotesk } from "next/font/google";
import { Suspense } from "react";
import { TourPanel } from "@/components/tour";
import "./globals.css";

// Spec 0041: Schibsted Grotesk for everything, IBM Plex Mono for account numbers, phone numbers,
// ids and hashes, and Instrument Serif only for page titles. Self-hosted at build time.
const sans = Schibsted_Grotesk({ subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--font-sans", display: "swap" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-mono", display: "swap" });
const display = Instrument_Serif({ subsets: ["latin"], weight: ["400"], style: ["normal"], variable: "--font-display", display: "swap" });

export const metadata: Metadata = {
  title: "Dialback",
  description: "Payments to a vendor whose bank details changed stay held until the vendor confirms on a provable number.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable} ${display.variable}`}>
      <body>{children}<Suspense fallback={null}><TourPanel /></Suspense></body>
    </html>
  );
}
