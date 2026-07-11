import "./globals.css";

export const metadata = {
  title: "KERN — Compile code for machine attention",
  description:
    "A vendor-neutral intermediate language and context runtime for coding agents: language compilation, visual cold pages, and exact-source page faults.",
  metadataBase: new URL("https://enoch3712.github.io/KERN/"),
  openGraph: {
    title: "KERN — Less syntax. More software.",
    description:
      "Compile supported programming languages into compact semantic context without giving up exact-source edits.",
    type: "website",
  },
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
