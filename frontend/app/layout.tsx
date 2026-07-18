import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "LabMate Pipetting Workspace",
  description: "Automated liquid handling workspace"
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
