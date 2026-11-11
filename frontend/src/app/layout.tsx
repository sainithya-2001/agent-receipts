import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans_Condensed, Newsreader } from "next/font/google";
import Link from "next/link";
import { ChainPill } from "@/components/ChainPill";
import "./globals.css";

const condensed = IBM_Plex_Sans_Condensed({
  subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--font-plex-condensed",
});
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-plex-mono" });
const serif = Newsreader({ subsets: ["latin"], style: ["normal", "italic"], variable: "--font-newsreader" });

export const metadata: Metadata = {
  title: "Proof of Intent",
  description: "The dispute layer for AI purchases on PayPal: tamper-evident receipts of intent, replayed when a purchase is disputed.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${condensed.variable} ${mono.variable} ${serif.variable}`}>
      <body>
        <header className="topbar">
          <div className="topbar-inner">
            <Link href="/" className="brand">
              <span className="brand-mark" aria-hidden />
              <span className="brand-name">Proof of Intent</span>
              <span className="brand-tag">The dispute layer for AI purchases on PayPal</span>
            </Link>
            <nav className="nav">
              <Link href="/">Dispute queue</Link>
              <Link href="/task">Give your agent a task</Link>
              <ChainPill />
            </nav>
          </div>
        </header>
        {children}
      </body>
    </html>
  );
}
