"use client";

import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  Server,
  HardDrive,
  RefreshCw,
  Trash2,
  Cpu,
  Database,
  Plus,
} from "lucide-react";
import {
  getAdminSystemStatus,
  getStorageSummary,
  cleanupStorage,
  reconcileStorage,
  listUsers,
  createUser,
  patchUser,
  listWorkflows,
  approveWorkflow,
} from "@/lib/api/admin";
import { queryKeys } from "@/lib/query/query-keys";
import { useAuth } from "@/lib/auth/auth-context";
import { getErrorMessage } from "@/lib/api/errors";
import { PageHeader, EmptyState } from "@/components/page-kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Dialog } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert } from "@/components/ui/alert";
import { Tabs } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";

const userSchema = z.object({
  name: z.string().min(1, "Tên không được để trống"),
  email: z.string().email("Email không đúng định dạng"),
  password: z.string().min(12, "Mật khẩu quản trị phải từ 12 ký tự trở lên"),
  role: z.enum(["ADMIN", "EDITOR"]),
});

type UserFormValues = z.infer<typeof userSchema>;

function numericDetail(details: Record<string, unknown> | undefined, key: string): number | null {
  const value = details?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export default function AdminPage() {
  const { isAdmin } = useAuth();
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState("system");
  const [isCreateUserOpen, setIsCreateUserOpen] = useState(false);
  const [serverMessage, setServerMessage] = useState<string | null>(null);

  // Queries
  const { data: systemStatus, isLoading: statusLoading, refetch: refetchStatus } = useQuery({
    queryKey: queryKeys.admin.systemStatus,
    queryFn: getAdminSystemStatus,
    enabled: Boolean(isAdmin),
  });

  const { data: storageSummary, isLoading: storageLoading } = useQuery({
    queryKey: queryKeys.admin.storageSummary,
    queryFn: getStorageSummary,
    enabled: Boolean(isAdmin),
  });
  const localFreeBytes = numericDetail(systemStatus?.local_storage.details, "free_bytes");

  const { data: usersData, isLoading: usersLoading } = useQuery({
    queryKey: queryKeys.admin.users(),
    queryFn: () => listUsers({ size: 50 }),
    enabled: Boolean(isAdmin),
  });

  const { data: workflowsData, isLoading: workflowsLoading } = useQuery({
    queryKey: queryKeys.admin.workflows(),
    queryFn: () => listWorkflows({ size: 50 }),
    enabled: Boolean(isAdmin),
  });

  // Mutations
  const cleanupMutation = useMutation({
    mutationFn: (dryRun: boolean) => cleanupStorage({ dry_run: dryRun }),
    onSuccess: (res) => {
      setServerMessage(
        res.dry_run
          ? `[Dry Run] Tìm thấy ${res.candidates.length} candidates sẵn sàng dọn dẹp.`
          : `[Thành công] Đã xóa ${res.deleted_objects} objects không còn sử dụng.`
      );
      queryClient.invalidateQueries({ queryKey: queryKeys.admin.storageSummary });
    },
    onError: (err) => setServerMessage(getErrorMessage(err, "Cleanup thất bại")),
  });

  const reconcileMutation = useMutation({
    mutationFn: reconcileStorage,
    onSuccess: (res) => {
      setServerMessage(
        `[Đối soát hoàn tất] Missing: ${res.missing_objects.length} · Corrupt: ${res.corrupt_objects.length} · Repaired: ${res.repaired_assets.length}`
      );
      queryClient.invalidateQueries({ queryKey: queryKeys.admin.storageSummary });
    },
    onError: (err) => setServerMessage(getErrorMessage(err, "Reconciliation thất bại")),
  });

  const {
    register: registerUser,
    handleSubmit: handleUserSubmit,
    reset: resetUser,
    formState: { errors: userErrors, isSubmitting: isUserSubmitting },
  } = useForm<UserFormValues>({
    resolver: zodResolver(userSchema),
    defaultValues: { role: "EDITOR" },
  });

  const createUserMutation = useMutation({
    mutationFn: createUser,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.admin.users() });
      setIsCreateUserOpen(false);
      resetUser();
    },
    onError: (err) => setServerMessage(getErrorMessage(err)),
  });

  const toggleUserActiveMutation = useMutation({
    mutationFn: ({ userId, isActive }: { userId: string; isActive: boolean }) =>
      patchUser(userId, { is_active: isActive }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.admin.users() }),
    onError: (err) => setServerMessage(getErrorMessage(err)),
  });

  const approveWorkflowMutation = useMutation({
    mutationFn: ({ workflowId, enabled }: { workflowId: string; enabled: boolean }) =>
      approveWorkflow(workflowId, { enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.admin.workflows() }),
    onError: (err) => setServerMessage(getErrorMessage(err)),
  });

  if (!isAdmin) {
    return (
      <div className="space-y-6">
        <PageHeader
          eyebrow="Bảng điều khiển hệ thống"
          title="Quản trị Studio (Admin Control)"
          description="Kiểm soát trạng thái hạ tầng on-premise, dung lượng MinIO, quản lý tài khoản editor và phê duyệt workflow H3."
        />
        <EmptyState
          title="Truy cập bị từ chối"
          detail="Bạn không có quyền quản trị viên (ADMIN) để truy cập hoặc thao tác trên trang này. Vui lòng liên hệ quản trị hệ thống nếu bạn cần cấp quyền."
          action={{ label: "Về trang chủ Dashboard", href: "/dashboard" }}
        />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Bảng điều khiển hệ thống"
        title="Quản trị Studio (Admin Control)"
        description="Kiểm soát trạng thái hạ tầng on-premise, dung lượng MinIO, quản lý tài khoản editor và phê duyệt workflow H3."
      />

      {serverMessage && (
        <Alert variant="info" title="Thông báo hệ thống">
          {serverMessage}
        </Alert>
      )}

      {/* Tabs */}
      <Tabs
        activeTab={activeTab}
        onChange={setActiveTab}
        tabs={[
          { id: "system", label: "Hạ tầng & Dịch vụ" },
          { id: "storage", label: "Lưu trữ MinIO & Dọn dẹp" },
          { id: "users", label: "Tài khoản Editor", count: usersData?.total ?? 0 },
          { id: "workflows", label: "ComfyUI Workflows", count: workflowsData?.total ?? 0 },
        ]}
      />

      {/* Tab 1: System Health */}
      {activeTab === "system" && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-[#f1f3f5]">Runtime Components Health</h2>
            <Button size="sm" variant="secondary" onClick={() => refetchStatus()}>
              <RefreshCw className="w-3.5 h-3.5" />
              <span>Kiểm tra lại</span>
            </Button>
          </div>

          {statusLoading ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              <Skeleton className="h-32" />
              <Skeleton className="h-32" />
              <Skeleton className="h-32" />
              <Skeleton className="h-32" />
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              <div className="p-5 rounded-md border border-[#2c3038] bg-[#181a1e] space-y-3">
                <div className="flex items-center justify-between">
                  <Database className="w-5 h-5 text-blue-400" />
                  <Badge status={systemStatus?.postgres.healthy ? "OK" : "DEGRADED"} />
                </div>
                <div>
                  <h3 className="font-semibold text-sm text-[#f1f3f5]">PostgreSQL</h3>
                  <p className="text-xs text-[#9ea5b0]">Database trạng thái &amp; migrations</p>
                </div>
              </div>

              <div className="p-5 rounded-md border border-[#2c3038] bg-[#181a1e] space-y-3">
                <div className="flex items-center justify-between">
                  <HardDrive className="w-5 h-5 text-emerald-400" />
                  <Badge status={systemStatus?.minio.healthy ? "OK" : "DEGRADED"} />
                </div>
                <div>
                  <h3 className="font-semibold text-sm text-[#f1f3f5]">MinIO S3 Store</h3>
                  <p className="text-xs text-[#9ea5b0]">Object store lưu media &amp; output</p>
                </div>
              </div>

              <div className="p-5 rounded-md border border-[#2c3038] bg-[#181a1e] space-y-3">
                <div className="flex items-center justify-between">
                  <Cpu className="w-5 h-5 text-amber-400" />
                  <Badge status={systemStatus?.comfyui.healthy ? "OK" : "DEGRADED"} />
                </div>
                <div>
                  <h3 className="font-semibold text-sm text-[#f1f3f5]">ComfyUI / H3</h3>
                  <p className="text-xs text-[#9ea5b0]">Inference engine (Internal only)</p>
                </div>
              </div>

              <div className="p-5 rounded-md border border-[#2c3038] bg-[#181a1e] space-y-3">
                <div className="flex items-center justify-between">
                  <Server className="w-5 h-5 text-purple-400" />
                  <Badge status={systemStatus?.local_storage.healthy ? "OK" : "DEGRADED"} />
                </div>
                <div>
                  <h3 className="font-semibold text-sm text-[#f1f3f5]">Local Disk</h3>
                  <p className="text-xs text-[#9ea5b0]">
                    Trống:{" "}
                    {localFreeBytes !== null
                      ? `${(localFreeBytes / (1024 ** 3)).toFixed(1)} GB`
                      : "N/A"}
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Tab 2: Storage Summary & Retention */}
      {activeTab === "storage" && (
        <div className="space-y-6">
          {storageLoading ? (
            <Skeleton className="h-44" />
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="p-4 rounded-md border border-[#2c3038] bg-[#181a1e]">
                <span className="text-xs text-[#9ea5b0] uppercase font-semibold">Assets Database</span>
                <strong className="text-2xl font-semibold block text-[#f1f3f5] mt-1">
                  {storageSummary?.database_assets ?? 0}
                </strong>
                <span className="text-[11px] text-[#9ea5b0]">
                  {((storageSummary?.database_bytes ?? 0) / (1024 ** 2)).toFixed(1)} MB
                </span>
              </div>

              <div className="p-4 rounded-md border border-[#2c3038] bg-[#181a1e]">
                <span className="text-xs text-[#9ea5b0] uppercase font-semibold">MinIO Objects</span>
                <strong className="text-2xl font-semibold block text-[#f1f3f5] mt-1">
                  {storageSummary?.object_count ?? 0}
                </strong>
                <span className="text-[11px] text-[#9ea5b0]">
                  {((storageSummary?.object_bytes ?? 0) / (1024 ** 2)).toFixed(1)} MB
                </span>
              </div>

              <div className="p-4 rounded-md border border-[#2c3038] bg-[#181a1e]">
                <span className="text-xs text-[#9ea5b0] uppercase font-semibold">Tài nguyên Pending</span>
                <strong className="text-2xl font-semibold block text-amber-400 mt-1">
                  {storageSummary?.pending_assets ?? 0}
                </strong>
                <span className="text-[11px] text-[#9ea5b0]">Đang chờ upload hoặc validate</span>
              </div>

              <div className="p-4 rounded-md border border-[#2c3038] bg-[#181a1e]">
                <span className="text-xs text-[#9ea5b0] uppercase font-semibold">Đã xóa / Hỏng</span>
                <strong className="text-2xl font-semibold block text-red-400 mt-1">
                  {(storageSummary?.deleted_assets ?? 0) + (storageSummary?.failed_assets ?? 0)}
                </strong>
                <span className="text-[11px] text-[#9ea5b0]">Sẵn sàng dọn dẹp</span>
              </div>
            </div>
          )}

          <div className="p-6 rounded-md border border-[#2c3038] bg-[#181a1e] space-y-4">
            <h3 className="text-base font-semibold text-[#f1f3f5]">Quản trị dọn dẹp &amp; đối soát</h3>
            <p className="text-xs text-[#9ea5b0]">
              Chạy kiểm tra tính toàn vẹn giữa PostgreSQL và MinIO để tìm các file mồ côi hoặc tự động dọn dẹp các asset đã xóa theo retention policy.
            </p>

            <div className="flex items-center gap-3 pt-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => cleanupMutation.mutate(true)}
                isLoading={cleanupMutation.isPending}
              >
                <span>Chạy Dry-run Dọn dẹp</span>
              </Button>

              <Button
                variant="danger"
                size="sm"
                onClick={() => {
                  if (confirm("Xác nhận xóa vĩnh viễn các object hết hạn trên MinIO?")) {
                    cleanupMutation.mutate(false);
                  }
                }}
                isLoading={cleanupMutation.isPending}
              >
                <Trash2 className="w-3.5 h-3.5" />
                <span>Thực thi Dọn dẹp Thật</span>
              </Button>

              <Button
                variant="outline"
                size="sm"
                onClick={() => reconcileMutation.mutate()}
                isLoading={reconcileMutation.isPending}
              >
                <RefreshCw className="w-3.5 h-3.5" />
                <span>Đối soát Lưu trữ</span>
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Tab 3: Users Management */}
      {activeTab === "users" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-[#f1f3f5]">Danh sách Video Editors &amp; Quản trị viên</h2>
            <Button size="sm" variant="primary" onClick={() => setIsCreateUserOpen(true)}>
              <Plus className="w-4 h-4" />
              <span>Thêm người dùng</span>
            </Button>
          </div>

          {usersLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-14" />
              <Skeleton className="h-14" />
            </div>
          ) : (
            <div className="table-panel">
              <table>
                <thead>
                  <tr>
                    <th>Họ và tên</th>
                    <th>Email</th>
                    <th>Vai trò</th>
                    <th>Trạng thái</th>
                    <th>Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {usersData?.items.map((u) => (
                    <tr key={u.id} className="hover:bg-white/5 transition-colors">
                      <td className="font-semibold text-sm text-[#f1f3f5]">{u.name}</td>
                      <td className="text-xs text-[#9ea5b0]">{u.email}</td>
                      <td>
                        <Badge status={u.role} />
                      </td>
                      <td>
                        <Badge status={u.is_active ? "OK" : "DEGRADED"}>
                          {u.is_active ? "Hoạt động" : "Vô hiệu hóa"}
                        </Badge>
                      </td>
                      <td>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() =>
                            toggleUserActiveMutation.mutate({
                              userId: u.id,
                              isActive: !u.is_active,
                            })
                          }
                        >
                          {u.is_active ? "Khóa" : "Mở khóa"}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Tab 4: Workflows */}
      {activeTab === "workflows" && (
        <div className="space-y-4">
          <h2 className="text-base font-semibold text-[#f1f3f5]">Đăng ký &amp; Phê duyệt ComfyUI Workflows</h2>

          {workflowsLoading ? (
            <Skeleton className="h-44" />
          ) : (
            <div className="table-panel">
              <table>
                <thead>
                  <tr>
                    <th>Workflow Code</th>
                    <th>Mode</th>
                    <th>Version</th>
                    <th>Trạng thái</th>
                    <th>Phê duyệt</th>
                  </tr>
                </thead>
                <tbody>
                  {workflowsData?.items.map((wf) => (
                    <tr key={wf.id}>
                      <td className="font-semibold text-sm text-[#f1f3f5]">{wf.code}</td>
                      <td className="text-xs uppercase text-blue-400 font-semibold">{wf.mode}</td>
                      <td className="text-xs text-[#9ea5b0]">v{wf.version}</td>
                      <td>
                        <Badge status={wf.enabled ? "OK" : "DEGRADED"}>
                          {wf.enabled ? "Đang kích hoạt" : "Chưa bật"}
                        </Badge>
                      </td>
                      <td>
                        <Button
                          size="sm"
                          variant={wf.enabled ? "outline" : "primary"}
                          onClick={() =>
                            approveWorkflowMutation.mutate({
                              workflowId: wf.id,
                              enabled: !wf.enabled,
                            })
                          }
                          isLoading={approveWorkflowMutation.isPending}
                        >
                          {wf.enabled ? "Tắt workflow" : "Phê duyệt & Bật"}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Create User Dialog */}
      <Dialog
        isOpen={isCreateUserOpen}
        onClose={() => setIsCreateUserOpen(false)}
        title="Thêm tài khoản người dùng"
        description="Tạo tài khoản cho video editor hoặc quản trị viên hệ thống."
      >
        <form onSubmit={handleUserSubmit((data) => createUserMutation.mutateAsync(data))} className="space-y-4">
          <Input id="u_name" label="Họ và tên" error={userErrors.name?.message} {...registerUser("name")} />
          <Input id="u_email" label="Email" type="email" error={userErrors.email?.message} {...registerUser("email")} />
          <Input
            id="u_pass"
            label="Mật khẩu (Tối thiểu 12 ký tự)"
            type="password"
            error={userErrors.password?.message}
            {...registerUser("password")}
          />
          <Select id="u_role" label="Vai trò" {...registerUser("role")}>
            <option value="EDITOR">EDITOR (Biên tập viên sản xuất)</option>
            <option value="ADMIN">ADMIN (Quản trị viên hạ tầng)</option>
          </Select>

          <div className="flex justify-end gap-3 pt-4 border-t border-[#2c3038]">
            <Button type="button" variant="outline" onClick={() => setIsCreateUserOpen(false)}>
              Hủy
            </Button>
            <Button type="submit" variant="primary" isLoading={isUserSubmitting}>
              Tạo tài khoản
            </Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
