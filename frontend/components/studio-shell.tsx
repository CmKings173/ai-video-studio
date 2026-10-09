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
import { SidebarNavLink } from "./sidebar-nav-link";
import { useI18n } from "@/lib/i18n";

const navItems = [
  { href: "/dashboard", label: "Tổng quan", description: "Tổng quan công việc và trạng thái hệ thống.", icon: LayoutDashboard },
  { href: "/projects", label: "Dự án", description: "Quản lý dự án và các video trong từng dự án.", icon: FolderKanban },
  { href: "/products", label: "Sản phẩm", description: "Quản lý sản phẩm, thương hiệu và tài nguyên dùng lại.", icon: Package },
  { href: "/videos", label: "Video", description: "Tạo video, quản lý cảnh và các bản xuất.", icon: Film },
  { href: "/assets/upload", label: "Tải tài nguyên", description: "Tải ảnh, video và âm thanh cho dự án.", icon: UploadCloud },
  { href: "/admin", label: "Quản trị", description: "Quản lý người dùng, workflow và hệ thống.", icon: ShieldCheck, adminOnly: true },
];

export function StudioShell({ children }: { children: React.ReactNode }) {
  const { t, locale, setLocale } = useI18n();
  const labels = [t("Tổng quan", "Overview"), t("Dự án", "Projects"), t("Sản phẩm", "Products"), "Video", t("Tải tài nguyên", "Upload assets"), t("Quản trị", "Administration")];
  const descriptions = [t("Tổng quan công việc và trạng thái hệ thống.", "Overview of your work and system health."), t("Quản lý dự án và các video trong từng dự án.", "Manage projects and their videos."), t("Quản lý sản phẩm, thương hiệu và tài nguyên dùng lại.", "Manage products, brands and reusable assets."), t("Tạo video, quản lý cảnh và các bản xuất.", "Create videos, manage scenes and exports."), t("Tải ảnh, video và âm thanh cho dự án.", "Upload images, video and audio for your project."), t("Quản lý người dùng, workflow và hệ thống.", "Manage users, workflows and system settings.")];
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
        <p className="text-sm text-[#9ea5b0]">{t("Đang xác thực phiên làm việc...", "Checking your session...")}</p>
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
          <h1 className="text-base font-semibold text-red-100">{t("Không thể kiểm tra phiên đăng nhập", "Unable to check your session")}</h1>
          <p className="mt-2 text-sm text-red-100/80">{sessionError}</p>
          <button
            type="button"
            onClick={() => { void refetchUser(); }}
            className="mt-4 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500"
          >
            {t("Thử lại kết nối", "Retry connection")}
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
      <aside className="sidebar" aria-label={t("Điều hướng ứng dụng", "Studio navigation")}>
        <div>
          <Link className="brand group" href="/dashboard" aria-label={t("Tổng quan AI Video Studio", "AI Video Studio overview")} title={t("Tổng quan AI Video Studio", "AI Video Studio overview")}>
            <span className="brand-mark group-hover:scale-105 transition-transform">
              <Sparkles className="w-5 h-5 text-white" />
            </span>
            <span className="sidebar-label">
              <strong>AI Video Studio</strong>
            </span>
          </Link>

          <nav>
            {navItems.map((item, index) => {
              if (item.adminOnly && !isAdmin) return null;
              const active = pathname === item.href || (item.href !== "/dashboard" && pathname.startsWith(`${item.href}/`));
              const Icon = item.icon;
              return (
                <SidebarNavLink
                  active={active}
                  label={labels[index]}
                  description={descriptions[index]}
                  href={item.href}
                  key={item.href}
                >
                  <Icon className="w-4 h-4 shrink-0" aria-hidden="true" />
                  <span className="sidebar-label">{labels[index]}</span>
                </SidebarNavLink>
              );
            })}
          </nav>
        </div>

        {/* User profile section */}
        <div className="sidebar-account mt-6 border-t border-[#2c3038] pt-4">
          {isAuthenticated && user ? (
            <>
              <div className="sidebar-profile">
                <span className="sidebar-avatar" role="img" aria-label={`${user.name} (${user.role})`} title={`${user.name} · ${user.email} · ${user.role}`}>
                  {user.name.trim().charAt(0).toLocaleUpperCase() || "?"}
                </span>
                <div className="sidebar-label grid gap-0.5 min-w-0">
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
                      setLogoutError(getErrorMessage(error, t("Không thể đăng xuất. Phiên hiện tại vẫn được giữ.", "Unable to sign out. Your session is still active.")));
                    });
                  }}
                  title={t("Đăng xuất", "Sign out")}
                  className="sidebar-logout rounded-md p-2 text-[#9ea5b0] transition-colors hover:bg-[#22252b] hover:text-red-400"
                  aria-label={t("Đăng xuất", "Sign out")}
                >
                  <LogOut className="w-4 h-4" />
                </button>
              </div>
            </>
          ) : (
            <Link
              href="/login"
              className="flex items-center justify-between rounded-md border border-blue-500/20 bg-blue-500/10 p-2 text-xs font-medium text-blue-400 hover:text-blue-300"
            >
              <span>{t("Đăng nhập hệ thống", "Sign in")}</span>
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
            </div>
          </div>
          <div className="flex items-center gap-3">
            <label className="sr-only" htmlFor="studio_language">{t("Ngôn ngữ", "Language")}</label>
            <select id="studio_language" value={locale} onChange={event => setLocale(event.target.value as "vi" | "en")}
              className="min-h-9 min-w-0 rounded-md border border-[#2c3038] bg-[#181a1e] px-2 text-xs text-[#f1f3f5] focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-500">
              <option value="vi">Tiếng Việt</option><option value="en">English</option>
            </select>
            <Link href="/videos/new" className="primary-action text-xs flex items-center gap-1.5">
              <Plus className="w-4 h-4" />
              <span>{t("Tạo video", "Create video")}</span>
            </Link>
          </div>
        </header>

        <main>
          {logoutError && (
            <div role="alert" className="studio-logout-alert mb-6 rounded-md border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-200">
              <p>{logoutError}</p>
              <button type="button" aria-label={t("Đóng thông báo lỗi đăng xuất", "Dismiss sign-out error")} onClick={() => setLogoutError(null)}
                className="mt-3 rounded-md border border-red-500/30 px-3 py-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-500">
                {t("Đóng thông báo", "Dismiss")}
              </button>
            </div>
          )}
          {children}
        </main>
      </div>
    </div>
  );
}
