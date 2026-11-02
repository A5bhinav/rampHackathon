import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Dialback",
  description: "Payments to a vendor whose bank details changed stay held until the vendor confirms on a provable number.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
