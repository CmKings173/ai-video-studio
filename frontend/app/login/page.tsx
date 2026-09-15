"use client";

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
    .min(3, "Tai khoan toi thieu 3 ky tu")
    .max(254, "Tai khoan toi da 254 ky tu"),
  password: z.string().min(1, "Vui long nhap mat khau").max(1024, "Mat khau qua dai"),
});

type LoginFormValues = z.infer<typeof loginSchema>;

export default function LoginPage() {
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
      setServerError(getErrorMessage(err, "Đăng nhập thất bại. Vui lòng kiểm tra lại thông tin."));
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#111317] p-4">
      <div className="w-full max-w-md bg-[#181a1e] border border-[#2c3038] rounded-md p-8  relative z-10 space-y-6">
        <div className="text-center space-y-2">
          <div className="mb-2 inline-flex h-12 w-12 items-center justify-center rounded-md bg-blue-600 text-white">
            <Sparkles className="w-6 h-6" />
          </div>
          <h1 className="text-2xl font-semibold text-[#f1f3f5] tracking-tight">AI Video Studio</h1>
          <p className="text-xs text-[#9ea5b0]">
            Hệ thống sản xuất video quảng cáo AI nội bộ (On-Premise H3)
          </p>
        </div>

        {serverError && (
          <Alert variant="destructive" title="Lỗi đăng nhập">
            {serverError}
          </Alert>
        )}

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <Input
            id="email"
            label="Tai khoan"
            type="text"
            inputMode="email"
            autoComplete="username"
            placeholder="editor@studio.local"
            error={errors.email?.message}
            {...register("email")}
          />

          <Input
            id="password"
            label="Mật khẩu"
            type="password"
            autoComplete="current-password"
            placeholder="••••••••••••"
            error={errors.password?.message}
            {...register("password")}
          />

          <Button
            type="submit"
            className="w-full mt-2"
            isLoading={isSubmitting}
            variant="primary"
          >
            Đăng nhập
          </Button>
        </form>

        <div className="pt-4 border-t border-[#2c3038]/60 text-center">
          <div className="inline-flex items-center gap-1.5 text-[11px] text-[#9ea5b0]">
            <Shield className="w-3.5 h-3.5 text-blue-400" />
            <span>Bảo mật phiên làm việc với HttpOnly Cookie &amp; CSRF</span>
          </div>
        </div>
      </div>
    </div>
  );
}
