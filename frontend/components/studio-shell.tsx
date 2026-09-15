"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  LayoutDashboard,
  FolderKanban,
  Package,
  Film,
  UploadCloud,
  ShieldCheck,
  LogOut,
  Plus,
  Sparkles,
} from "lucide-react";
import { useAuth } from "@/lib/auth/auth-context";
import { getErrorMessage } from "@/lib/api/errors";
import { Badge } from "./ui/badge";

const navItems = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/projects", label: "Dự án", icon: FolderKanban },
  { href: "/products", label: "Sản phẩm", icon: Package },
  { href: "/videos", label: "Video", icon: Film },
  { href: "/assets/upload", label: "Tải tài nguyên", icon: UploadCloud },
  { href: "/admin", label: "Quản trị", icon: ShieldCheck, adminOnly: true },
];

export function StudioShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, isAuthenticated, isLoading, isAdmin, logout, sessionError, refetchUser } = useAuth();
  const [logoutError, setLogoutError] = useState<string | null>(null);

  useEffect(() => {
    if (isLoading || sessionError) return;

    if (!isAuthenticated && pathname !== "/login") {
      router.replace("/login");
    } else if (isAuthenticated && pathname === "/login") {
      router.replace("/dashboard");
    }
  }, [isLoading, sessionError, isAuthenticated, pathname, router]);

  // AuthProvider initializing -> show loading state
  if (isLoading) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-[#111317]">
        <div className="w-10 h-10 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        <p className="text-sm text-[#9ea5b0]">Đang xác thực phiên làm việc...</p>
      </div>
    );
  }

  // If on login page, render full screen without studio sidebar/topbar
  if (pathname === "/login") {
    return <>{children}</>;
  }

  if (sessionError && !isAuthenticated) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-[#111317] px-6 text-center">
        <div className="max-w-md rounded-md border border-red-500/30 bg-red-500/10 p-5 text-left">
          <h1 className="text-base font-semibold text-red-100">Khong the kiem tra phien dang nhap</h1>
          <p className="mt-2 text-sm text-red-100/80">{sessionError}</p>
          <button
            type="button"
            onClick={() => { void refetchUser(); }}
            className="mt-4 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500"
          >
            Thu lai ket noi
          </button>
        </div>
      </div>
    );
  }

  // If unauthenticated on protected route, do not render protected application while redirecting
  if (!isAuthenticated) {
    return null;
  }

  return (
    <div className="studio-shell">
      <aside className="sidebar" aria-label="Studio navigation">
        <div>
          <Link className="brand group" href="/dashboard" aria-label="AI Video Studio dashboard">
            <span className="brand-mark group-hover:scale-105 transition-transform">
              <Sparkles className="w-5 h-5 text-white" />
            </span>
            <span>
              <strong>AI Video Studio</strong>
              <small>On-prem H3 workspace</small>
            </span>
          </Link>

          <nav>
            {navItems.map((item) => {
              if (item.adminOnly && !isAdmin) return null;
              const active = pathname === item.href || (item.href !== "/dashboard" && pathname.startsWith(`${item.href}/`));
              const Icon = item.icon;
              return (
                <Link
                  aria-current={active ? "page" : undefined}
                  className={active ? "active" : ""}
                  href={item.href}
                  key={item.href}
                >
                  <Icon className="w-4 h-4 shrink-0" />
                  <span>{item.label}</span>
                </Link>
              );
            })}
          </nav>
        </div>

        {/* User profile section */}
        <div className="mt-6 border-t border-[#2c3038] pt-4">
          {isAuthenticated && user ? (
            <>
              <div className="flex items-center justify-between gap-3">
                <div className="grid gap-0.5 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-semibold text-[#f1f3f5]">{user.name}</span>
                    <Badge status={user.role} className="text-[10px] px-1.5 py-0" />
                  </div>
                  <span className="truncate text-xs text-[#9ea5b0]">{user.email}</span>
                </div>
                <button
                  onClick={() => {
                    setLogoutError(null);
                    void logout().catch((error) => {
                      setLogoutError(getErrorMessage(error, "Không thể đăng xuất. Phiên hiện tại vẫn được giữ."));
                    });
                  }}
                  title="Đăng xuất"
                  className="rounded-md p-2 text-[#9ea5b0] transition-colors hover:bg-[#22252b] hover:text-red-400"
                  aria-label="Đăng xuất"
                >
                  <LogOut className="w-4 h-4" />
                </button>
              </div>
              {logoutError && (
                <p className="mt-3 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
                  {logoutError}
                </p>
              )}
            </>
          ) : (
            <Link
              href="/login"
              className="flex items-center justify-between rounded-md border border-blue-500/20 bg-blue-500/10 p-2 text-xs font-medium text-blue-400 hover:text-blue-300"
            >
              <span>Đăng nhập hệ thống</span>
              <span>→</span>
            </Link>
          )}
        </div>
      </aside>

      <div className="main-column">
        <header className="topbar">
          <div className="flex items-center gap-3">
            <div>
              <span>AI Video Studio</span>
              <strong className="text-sm font-semibold text-[#f1f3f5]">On-Premise Production</strong>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Link href="/videos/new" className="primary-action text-xs flex items-center gap-1.5">
              <Plus className="w-4 h-4" />
              <span>Tạo video</span>
            </Link>
          </div>
        </header>

        <main>{children}</main>
      </div>
    </div>
  );
}
