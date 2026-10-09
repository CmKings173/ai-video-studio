"use client";

import { useI18n } from "@/lib/i18n";
import React, { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Sparkles, Shield } from "lucide-react";
import { useAuth } from "@/lib/auth/auth-context";
import { getErrorMessage } from "@/lib/api/errors";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert } from "@/components/ui/alert";

const loginSchema = z.object({
  email: z
    .string()
    .trim()
    .min(3, "account_too_short")
    .max(254, "account_too_long"),
  password: z.string().min(1, "required_password").max(1024, "password_too_long"),
});

type LoginFormValues = z.infer<typeof loginSchema>;

export default function LoginPage() {
  const { t, locale, setLocale } = useI18n();
  const validationMessage = (message?: string) => {
    const messages: Record<string, string> = {
      account_too_short: t("Tài khoản tối thiểu 3 ký tự", "Account must be at least 3 characters"),
      account_too_long: t("Tài khoản tối đa 254 ký tự", "Account must be at most 254 characters"),
      required_password: t("Vui lòng nhập mật khẩu", "Please enter your password"),
      password_too_long: t("Mật khẩu tối đa 1.024 ký tự", "Password must be at most 1,024 characters"),
    };
    return message ? messages[message] ?? t("Thông tin không hợp lệ", "Invalid value") : undefined;
  };
  const { login } = useAuth();
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: {
      email: "",
      password: "",
    },
  });

  const onSubmit = async (data: LoginFormValues) => {
    setServerError(null);
    try {
      await login(data);
    } catch (err) {
      setServerError(getErrorMessage(err, t("Đăng nhập thất bại. Vui lòng kiểm tra lại thông tin.", "Login failed. Please check your credentials.")));
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#111317] p-4">
      <div className="w-full max-w-md bg-[#181a1e] border border-[#2c3038] rounded-md p-8  relative z-10 space-y-6">
        <div className="flex justify-end gap-2" role="group" aria-label={t("Ngôn ngữ", "Language")}>
          <button type="button" lang="vi" aria-pressed={locale === "vi"} aria-label={t("Tiếng Việt", "Vietnamese")} onClick={() => setLocale("vi")} className="secondary-action text-xs">{t("VI", "VI")}</button>
          <button type="button" lang="en" aria-pressed={locale === "en"} aria-label={t("Tiếng Anh", "English")} onClick={() => setLocale("en")} className="secondary-action text-xs">{t("English", "English")}</button>
        </div>
        <div className="text-center space-y-2">
          <div className="mb-2 inline-flex h-12 w-12 items-center justify-center rounded-md bg-blue-600 text-white">
            <Sparkles className="w-6 h-6" />
          </div>
          <h1 className="text-2xl font-semibold text-[#f1f3f5] tracking-tight">AI Video Studio</h1>
          <p className="text-xs text-[#9ea5b0]">
            {t("Hệ thống sản xuất video quảng cáo AI nội bộ (H3)", "Internal AI advertising video production (on-premise H3)")}
          </p>
        </div>

        {serverError && (
          <Alert variant="destructive" title={t("Lỗi đăng nhập", "Login error")}>
            {serverError}
          </Alert>
        )}

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <Input
            id="email"
            label={t("Tài khoản", "Account")}
            type="text"
            inputMode="email"
            autoComplete="username"
            placeholder={t("editor@studio.local", "editor@studio.local")}
            error={validationMessage(errors.email?.message)}
            {...register("email")}
          />

          <Input
            id="password"
            label={t("Mật khẩu", "Password")}
            type="password"
            autoComplete="current-password"
            placeholder={t("••••••••••••", "••••••••••••")}
            error={validationMessage(errors.password?.message)}
            {...register("password")}
          />

          <Button
            type="submit"
            className="w-full mt-2"
            isLoading={isSubmitting}
            variant="primary"
          >
            {t("Đăng nhập", "Log in")}
          </Button>
        </form>

        <div className="pt-4 border-t border-[#2c3038]/60 text-center">
          <div className="inline-flex items-center gap-1.5 text-[11px] text-[#9ea5b0]">
            <Shield className="w-3.5 h-3.5 text-blue-400" />
            <span>{t("Bảo mật phiên làm việc bằng cookie HttpOnly & CSRF", "Session secured with HttpOnly cookies & CSRF")}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
