import type { Metadata } from "next";
import { StudioShell } from "@/components/studio-shell";
import { QueryProvider } from "@/lib/query/query-provider";
import { AuthProvider } from "@/lib/auth/auth-context";
import { LocaleProvider } from "@/lib/i18n";

import "./globals.css";

export const metadata: Metadata = {
  title: "AI Video Studio",
  description: "Internal on-premise AI advertising video studio",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="vi" className="dark">
      <body className="bg-[#111317] text-[#f1f3f5] min-h-screen antialiased">
        <LocaleProvider><QueryProvider>
          <AuthProvider>
            <StudioShell>{children}</StudioShell>
          </AuthProvider>
        </QueryProvider></LocaleProvider>
      </body>
    </html>
  );
}
