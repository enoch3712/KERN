import "./globals.css";

export const metadata = {
  title: {
    default: "KERN — Compile repositories for machine attention",
    template: "%s — KERN",
  },
  description:
    "A lazy, content-addressed intermediate language and context runtime for coding agents, with exact-source page faults before every write.",
  metadataBase: new URL("https://enoch3712.github.io/KERN/"),
  alternates: {
    canonical: "./",
  },
  icons: {
    icon: [{ url: "https://enoch3712.github.io/KERN/kern-mark.svg", type: "image/svg+xml" }],
  },
  robots: {
    index: true,
    follow: true,
  },
  openGraph: {
    title: "KERN — Compile repositories for machine attention",
    description:
      "Lazy semantic context for coding agents, with an exact-source path before every write.",
    type: "website",
    url: "./",
    siteName: "KERN",
    images: [{ url: "https://enoch3712.github.io/KERN/kern-social.svg", width: 1200, height: 630, alt: "KERN — 12.75× smaller selected representation in the pilot" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "KERN — Compile repositories for machine attention",
    description: "Lazy semantic context with an exact-source path before every write.",
    images: ["https://enoch3712.github.io/KERN/kern-social.svg"],
  },
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
