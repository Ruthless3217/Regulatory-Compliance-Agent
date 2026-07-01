import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { cookies } from "next/headers";
import { Toaster } from "sonner";
import "./globals.css";

const sans = Inter({
 subsets: ["latin"],
 variable: "--font-sans",
 display: "swap",
});

const mono = JetBrains_Mono({
 subsets: ["latin"],
 variable: "--font-mono",
 display: "swap",
 weight: ["400", "500"],
});

export const metadata: Metadata = {
 title: "Bajaj Compliance",
 description: "Regulatory compliance review tool for Bajaj Life Insurance marketing content",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
 const density = (await cookies()).get("density")?.value === "compact" ? "compact" : "comfortable";
 return (
 <html
 lang="en"
 data-density={density}
 className={`${sans.variable} ${mono.variable}`}
 >
 <body className="min-h-screen bg-surface text-foreground">
 {children}
 <Toaster position="top-right" toastOptions={{ className: "border border-border bg-background" }} />
 </body>
 </html>
 );
}
