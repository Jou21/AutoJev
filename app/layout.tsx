import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { headers } from "next/headers";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host =
    requestHeaders.get("x-forwarded-host") ??
    requestHeaders.get("host") ??
    "localhost:3000";
  const protocol =
    requestHeaders.get("x-forwarded-proto") ??
    (host.startsWith("localhost") || host.startsWith("127.0.0.1")
      ? "http"
      : "https");
  const origin = new URL(`${protocol}://${host}`);
  const socialImage = new URL("/og.png", origin).toString();

  return {
    metadataBase: origin,
    title: {
      default: "Test Lab — Exploração inteligente de aplicações",
      template: "%s · Test Lab",
    },
    description:
      "Agente autônomo de QA para explorar aplicações web, registrar evidências e transformar descobertas em cenários de teste.",
    applicationName: "Test Lab",
    keywords: ["QA", "testes", "Playwright", "automação", "exploratório"],
    openGraph: {
      title: "Test Lab — QA autônomo, evidência real",
      description:
        "O Test Lab decide. Playwright executa. Sua LLM auxiliar interpreta as evidências.",
      type: "website",
      locale: "pt_BR",
      images: [
        { url: socialImage, width: 1774, height: 887, alt: "Test Lab" },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title: "Test Lab — QA autônomo, evidência real",
      description:
        "O Test Lab decide. Playwright executa. Sua LLM auxiliar interpreta as evidências.",
      images: [socialImage],
    },
    icons: {
      icon: "/favicon.svg",
      shortcut: "/favicon.svg",
    },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="pt-BR">
      <head><link rel="icon" href="/favicon.svg" type="image/svg+xml" /></head>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
