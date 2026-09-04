import type { Metadata } from "next";
import "./globals.css";

// Using next/font/google for Space Grotesk + JetBrains Mono is the
// intended setup (self-hosts at build time, zero layout shift) - it
// just needs network access to fonts.googleapis.com at build time,
// which this sandbox doesn't have. Uncomment on your machine:
//
//   import { Space_Grotesk, JetBrains_Mono } from "next/font/google";
//   const grotesk = Space_Grotesk({ subsets: ["latin"], variable: "--font-grotesk", weight: ["500", "700"] });
//   const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", weight: ["400", "500"] });
//   then add `${grotesk.variable} ${mono.variable}` to the <html> className below.

export const metadata: Metadata = {
  title: "Waypost — the job board that reads job boards for you",
  description:
    "Waypost scans job boards quietly, checks each posting against your resume, and only tells you what's actually worth applying to.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-display">{children}</body>
    </html>
  );
}
