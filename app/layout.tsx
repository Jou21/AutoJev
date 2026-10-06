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
      default: "AutoJev — Exploração inteligente de aplicações",
      template: "%s · AutoJev",
    },
    description:
      "Agente autônomo de QA para explorar aplicações web, registrar evidências e transformar descobertas em cenários de teste.",
    applicationName: "AutoJev",
    keywords: ["QA", "testes", "Playwright", "automação", "JEV"],
    openGraph: {
      title: "AutoJev — QA autônomo, evidência real",
      description:
        "JEV decide. Playwright executa. Sua LLM auxiliar interpreta as evidências.",
      type: "website",
      locale: "pt_BR",
      images: [
        { url: socialImage, width: 1774, height: 887, alt: "AutoJev" },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title: "AutoJev — QA autônomo, evidência real",
      description:
        "JEV decide. Playwright executa. Sua LLM auxiliar interpreta as evidências.",
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
