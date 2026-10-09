"use client";

import { useI18n } from "@/lib/i18n";
import React, { useState } from "react";
import { useInfiniteQuery, useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
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
import { flattenPageItems, nextPageParam } from "@/lib/api/pagination";
import { useAuth } from "@/lib/auth/auth-context";
import { getErrorMessage } from "@/lib/api/errors";
import { PageHeader, EmptyState, QueryErrorNotice } from "@/components/page-kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Dialog } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert } from "@/components/ui/alert";
import { Tabs } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";

const userSchema = z.object({
  name: z.string().min(1, "required_name"),
  email: z.string().email("invalid_email"),
  password: z.string().min(12, "admin_password_too_short"),
  role: z.enum(["ADMIN", "EDITOR"]),
});

type UserFormValues = z.infer<typeof userSchema>;

function numericDetail(details: Record<string, unknown> | undefined, key: string): number | null {
  const value = details?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export default function AdminPage() {
  const { t } = useI18n();
  const validationMessage = (message?: string) => {
    const messages: Record<string, string> = {
      required_name: t("Tên không được để trống", "Name is required"),
      invalid_email: t("Email không đúng định dạng", "Enter a valid email address"),
      admin_password_too_short: t("Mật khẩu phải từ 12 ký tự trở lên", "Password must be at least 12 characters"),
    };
    return message ? messages[message] ?? t("Thông tin không hợp lệ", "Invalid value") : undefined;
  };
  const { isAdmin } = useAuth();
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState("system");
  const [isCreateUserOpen, setIsCreateUserOpen] = useState(false);
  const [serverMessage, setServerMessage] = useState<string | null>(null);

  // Queries
  const { data: systemStatus, isLoading: statusLoading, isError: statusError, isFetching: statusFetching, refetch: refetchStatus } = useQuery({
    queryKey: queryKeys.admin.systemStatus,
    queryFn: getAdminSystemStatus,
    enabled: Boolean(isAdmin),
  });

  const { data: storageSummary, isLoading: storageLoading, isError: storageError, isFetching: storageFetching, refetch: refetchStorage } = useQuery({
    queryKey: queryKeys.admin.storageSummary,
    queryFn: getStorageSummary,
    enabled: Boolean(isAdmin),
  });
  const localFreeBytes = numericDetail(systemStatus?.local_storage.details, "free_bytes");

  const usersQuery = useInfiniteQuery({
    queryKey: queryKeys.admin.users({ size: 50 }),
    queryFn: ({ pageParam }) => listUsers({ size: 50, page: pageParam }),
    initialPageParam: 1,
    getNextPageParam: nextPageParam,
    enabled: Boolean(isAdmin),
  });

  const workflowsQuery = useInfiniteQuery({
    queryKey: queryKeys.admin.workflows({ size: 50 }),
    queryFn: ({ pageParam }) => listWorkflows({ size: 50, page: pageParam }),
    initialPageParam: 1,
    getNextPageParam: nextPageParam,
    enabled: Boolean(isAdmin),
  });
  const users = flattenPageItems(usersQuery.data?.pages);
  const workflows = flattenPageItems(workflowsQuery.data?.pages);

  // Mutations
  const cleanupMutation = useMutation({
    mutationFn: (dryRun: boolean) => cleanupStorage({ dry_run: dryRun }),
    onSuccess: (res) => {
      setServerMessage(
        res.dry_run
          ? t(`[Xem trước] Có ${res.candidates.length} đối tượng sẵn sàng dọn dẹp.`, `[Preview] Found ${res.candidates.length} cleanup candidates.`)
          : t(`[Thành công] Đã xóa ${res.deleted_objects} đối tượng không còn sử dụng.`, `[Success] Deleted ${res.deleted_objects} unused objects.`)
      );
      queryClient.invalidateQueries({ queryKey: queryKeys.admin.storageSummary });
    },
    onError: (err) => setServerMessage(getErrorMessage(err, t("Dọn dẹp thất bại", "Cleanup failed"))),
  });

  const reconcileMutation = useMutation({
    mutationFn: reconcileStorage,
    onSuccess: (res) => {
      setServerMessage(
        t(`[Đối soát hoàn tất] Thiếu: ${res.missing_objects.length} · Hỏng: ${res.corrupt_objects.length} · Đã sửa: ${res.repaired_assets.length}`, `[Reconciliation complete] Missing: ${res.missing_objects.length} · Corrupt: ${res.corrupt_objects.length} · Repaired: ${res.repaired_assets.length}`)
      );
      queryClient.invalidateQueries({ queryKey: queryKeys.admin.storageSummary });
    },
    onError: (err) => setServerMessage(getErrorMessage(err, t("Đối soát thất bại", "Reconciliation failed"))),
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
      queryClient.invalidateQueries({ queryKey: queryKeys.admin.users({ size: 50 }) });
      setIsCreateUserOpen(false);
      resetUser();
    },
    onError: (err) => setServerMessage(getErrorMessage(err)),
  });

  const toggleUserActiveMutation = useMutation({
    mutationFn: ({ userId, isActive }: { userId: string; isActive: boolean }) =>
      patchUser(userId, { is_active: isActive }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.admin.users({ size: 50 }) }),
    onError: (err) => setServerMessage(getErrorMessage(err)),
  });

  const approveWorkflowMutation = useMutation({
    mutationFn: ({ workflowId, enabled }: { workflowId: string; enabled: boolean }) =>
      approveWorkflow(workflowId, { enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.admin.workflows({ size: 50 }) }),
    onError: (err) => setServerMessage(getErrorMessage(err)),
  });

  if (!isAdmin) {
    return (
      <div className="space-y-6">
        <PageHeader
          eyebrow={t("Bảng điều khiển hệ thống", "System control panel")}
          title={t("Quản trị không gian làm việc", "Studio administration")}
          description={t("Kiểm soát trạng thái hạ tầng nội bộ, dung lượng MinIO, quản lý tài khoản biên tập viên và phê duyệt workflow H3.", "Monitor on-premise infrastructure and MinIO capacity, manage editor accounts, and approve H3 workflows.")}
        />
        <EmptyState
          title={t("Truy cập bị từ chối", "Access denied")}
          detail={t("Bạn cần quyền quản trị viên (ADMIN) để truy cập hoặc thao tác trên trang này. Vui lòng liên hệ quản trị hệ thống nếu cần cấp quyền.", "You need administrator access (ADMIN) to use this page. Contact your system administrator to request access.")}
          action={{ label: t("Về trang tổng quan", "Go to overview"), href: "/dashboard" }}
        />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow={t("Bảng điều khiển hệ thống", "System control panel")}
        title={t("Quản trị không gian làm việc", "Studio administration")}
        description={t("Kiểm soát trạng thái hạ tầng nội bộ, dung lượng MinIO, quản lý tài khoản biên tập viên và phê duyệt workflow H3.", "Monitor on-premise infrastructure and MinIO capacity, manage editor accounts, and approve H3 workflows.")}
      />

      {serverMessage && (
        <Alert variant="info" title={t("Thông báo hệ thống", "System notice")}>
          {serverMessage}
        </Alert>
      )}

      {/* Tabs */}
      <Tabs
        activeTab={activeTab}
        onChange={setActiveTab}
        tabs={[
          { id: "system", label: t("Hạ tầng & Dịch vụ", "Infrastructure & Services") },
          { id: "storage", label: t("Lưu trữ MinIO & Dọn dẹp", "MinIO Storage & Cleanup") },
          { id: "users", label: t("Tài khoản biên tập viên", "Editor accounts"), count: !usersQuery.isError ? usersQuery.data?.pages[0]?.total : undefined },
          { id: "workflows", label: t("Workflow ComfyUI", "ComfyUI workflows"), count: !workflowsQuery.isError ? workflowsQuery.data?.pages[0]?.total : undefined },
        ]}
      />

      {/* Tab 1: System Health */}
      {activeTab === "system" && (
        <div className="space-y-6">
          <div className="flex min-w-0 flex-wrap items-center justify-between">
            <h2 className="text-base font-semibold text-[#f1f3f5]">{t("Tình trạng dịch vụ đang chạy", "Runtime service health")}</h2>
            <Button size="sm" variant="secondary" disabled={statusFetching} onClick={() => { void refetchStatus(); }}>
              <RefreshCw className="w-3.5 h-3.5" />
              <span>{t("Kiểm tra lại", "Check again")}</span>
            </Button>
          </div>

          {statusLoading ? (
            <div className="grid responsive-summary-grid gap-4">
              <Skeleton className="h-32" />
              <Skeleton className="h-32" />
              <Skeleton className="h-32" />
              <Skeleton className="h-32" />
            </div>
          ) : statusError || !systemStatus ? (
            <QueryErrorNotice
              title={t("Không thể lấy trạng thái hệ thống", "Unable to retrieve system status")}
              detail={t("Không thể xác minh tình trạng PostgreSQL, MinIO hoặc dịch vụ xử lý lúc này.", "Unable to verify PostgreSQL, MinIO, or processing service health at this time.")}
              onRetry={refetchStatus}
              isRetrying={statusFetching}
              retryLabel={t("Thử lại trạng thái", "Retry status check")}
            />
          ) : (
            <div className="grid responsive-summary-grid gap-4">
              <div className="p-5 rounded-md border border-[#2c3038] bg-[#181a1e] space-y-3">
                <div className="flex min-w-0 flex-wrap items-center justify-between">
                  <Database className="w-5 h-5 text-blue-400" />
                  <Badge status={systemStatus.postgres.healthy ? "OK" : "DEGRADED"} />
                </div>
                <div>
                  <h3 className="font-semibold text-sm text-[#f1f3f5]">PostgreSQL</h3>
                  <p className="text-xs text-[#9ea5b0]">{t("Cơ sở dữ liệu trạng thái & Chuyển đổi dữ liệu", "State database & migrations")}</p>
                </div>
              </div>

              <div className="p-5 rounded-md border border-[#2c3038] bg-[#181a1e] space-y-3">
                <div className="flex min-w-0 flex-wrap items-center justify-between">
                  <HardDrive className="w-5 h-5 text-emerald-400" />
                  <Badge status={systemStatus.minio.healthy ? "OK" : "DEGRADED"} />
                </div>
                <div>
                  <h3 className="font-semibold text-sm text-[#f1f3f5]">{t("Kho MinIO S3", "MinIO S3 store")}</h3>
                  <p className="text-xs text-[#9ea5b0]">{t("Kho lưu tài nguyên & Kết quả", "Object store for media & output")}</p>
                </div>
              </div>

              <div className="p-5 rounded-md border border-[#2c3038] bg-[#181a1e] space-y-3">
                <div className="flex min-w-0 flex-wrap items-center justify-between">
                  <Cpu className="w-5 h-5 text-amber-400" />
                  <Badge status={systemStatus.comfyui.healthy ? "OK" : "DEGRADED"} />
                </div>
                <div>
                  <h3 className="font-semibold text-sm text-[#f1f3f5]">ComfyUI / H3</h3>
                  <p className="text-xs text-[#9ea5b0]">{t("Bộ máy xử lý AI (chỉ dùng nội bộ)", "Inference engine (internal only)")}</p>
                </div>
              </div>

              <div className="p-5 rounded-md border border-[#2c3038] bg-[#181a1e] space-y-3">
                <div className="flex min-w-0 flex-wrap items-center justify-between">
                  <Server className="w-5 h-5 text-purple-400" />
                  <Badge status={systemStatus.local_storage.healthy ? "OK" : "DEGRADED"} />
                </div>
                <div>
                  <h3 className="font-semibold text-sm text-[#f1f3f5]">{t("Ổ đĩa cục bộ", "Local disk")}</h3>
                  <p className="text-xs text-[#9ea5b0]">
                    {t("Trống:", "Free:")}{" "}
                    {localFreeBytes !== null
                      ? `${(localFreeBytes / (1024 ** 3)).toFixed(1)} GB`
                      : t("Không có dữ liệu", "Unavailable")}
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
          ) : !storageSummary ? (
            <QueryErrorNotice
              title={t("Không thể tải tổng quan lưu trữ", "Unable to load storage overview")}
              detail={t("Không có số liệu xác thực để hiển thị.", "No verified data is available to display.")}
              onRetry={refetchStorage}
              isRetrying={storageFetching}
            />
          ) : (
            <>
            {storageError && <QueryErrorNotice title={t("Tổng quan lưu trữ chưa được cập nhật", "Storage overview could not be refreshed")} detail={t("Đang hiển thị số liệu đã tải trước đó.", "Showing previously loaded metrics.")} onRetry={refetchStorage} isRetrying={storageFetching} />}
            <div className="grid responsive-summary-grid gap-4">
              <div className="p-4 rounded-md border border-[#2c3038] bg-[#181a1e]">
                <span className="text-xs text-[#9ea5b0] uppercase font-semibold">{t("Tài nguyên trong cơ sở dữ liệu", "Assets in database")}</span>
                <strong className="text-2xl font-semibold block text-[#f1f3f5] mt-1">
                  {storageSummary.database_assets}
                </strong>
                <span className="text-[11px] text-[#9ea5b0]">
                  {(storageSummary.database_bytes / (1024 ** 2)).toFixed(1)} MB
                </span>
              </div>

              <div className="p-4 rounded-md border border-[#2c3038] bg-[#181a1e]">
                <span className="text-xs text-[#9ea5b0] uppercase font-semibold">{t("Đối tượng MinIO", "MinIO objects")}</span>
                <strong className="text-2xl font-semibold block text-[#f1f3f5] mt-1">
                  {storageSummary.object_count}
                </strong>
                <span className="text-[11px] text-[#9ea5b0]">
                  {(storageSummary.object_bytes / (1024 ** 2)).toFixed(1)} MB
                </span>
              </div>

              <div className="p-4 rounded-md border border-[#2c3038] bg-[#181a1e]">
                <span className="text-xs text-[#9ea5b0] uppercase font-semibold">{t("Tài nguyên đang chờ", "Pending assets")}</span>
                <strong className="text-2xl font-semibold block text-amber-400 mt-1">
                  {storageSummary.pending_assets}
                </strong>
                <span className="text-[11px] text-[#9ea5b0]">{t("Đang chờ tải lên hoặc xác thực", "Awaiting upload or validation")}</span>
              </div>

              <div className="p-4 rounded-md border border-[#2c3038] bg-[#181a1e]">
                <span className="text-xs text-[#9ea5b0] uppercase font-semibold">{t("Đã xóa / Hỏng", "Deleted / Failed")}</span>
                <strong className="text-2xl font-semibold block text-red-400 mt-1">
                  {storageSummary.deleted_assets + storageSummary.failed_assets}
                </strong>
                <span className="text-[11px] text-[#9ea5b0]">{t("Sẵn sàng dọn dẹp", "Ready for cleanup")}</span>
              </div>
            </div>
            </>
          )}

          <div className="p-6 rounded-md border border-[#2c3038] bg-[#181a1e] space-y-4">
            <h3 className="text-base font-semibold text-[#f1f3f5]">{t("Quản trị dọn dẹp & Đối soát", "Cleanup & reconciliation management")}</h3>
            <p className="text-xs text-[#9ea5b0]">
              {t("Kiểm tra tính toàn vẹn giữa PostgreSQL và MinIO để tìm tệp không còn liên kết hoặc dọn dẹp tài nguyên đã xóa theo chính sách lưu giữ.", "Check PostgreSQL and MinIO integrity to find orphaned files or clean up deleted assets according to retention policy.")}
            </p>

            <div className="flex flex-wrap items-center gap-3 pt-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={!storageSummary || storageError || storageLoading || storageFetching}
                onClick={() => cleanupMutation.mutate(true)}
                isLoading={cleanupMutation.isPending}
              >
                <span>{t("Xem trước dọn dẹp", "Preview cleanup")}</span>
              </Button>

              <Button
                variant="danger"
                size="sm"
                disabled={!storageSummary || storageError || storageLoading || storageFetching}
                onClick={() => {
                  if (confirm(t("Xác nhận xóa vĩnh viễn các đối tượng hết hạn trên MinIO?", "Permanently delete expired objects from MinIO?"))) {
                    cleanupMutation.mutate(false);
                  }
                }}
                isLoading={cleanupMutation.isPending}
              >
                <Trash2 className="w-3.5 h-3.5" />
                <span>{t("Thực thi dọn dẹp", "Run cleanup")}</span>
              </Button>

              <Button
                variant="outline"
                size="sm"
                disabled={!storageSummary || storageError || storageLoading || storageFetching}
                onClick={() => reconcileMutation.mutate()}
                isLoading={reconcileMutation.isPending}
              >
                <RefreshCw className="w-3.5 h-3.5" />
                <span>{t("Đối soát lưu trữ", "Reconcile storage")}</span>
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Tab 3: Users Management */}
      {activeTab === "users" && (
        <div className="space-y-4">
          <div className="flex min-w-0 flex-wrap items-center justify-between">
            <h2 className="text-base font-semibold text-[#f1f3f5]">{t("Biên tập viên video & Quản trị viên", "Video editors & administrators")}</h2>
            <Button size="sm" variant="primary" onClick={() => setIsCreateUserOpen(true)}>
              <Plus className="w-4 h-4" />
              <span>{t("Thêm người dùng", "Add user")}</span>
            </Button>
          </div>

          {usersQuery.isLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-14" />
              <Skeleton className="h-14" />
            </div>
          ) : !usersQuery.data ? (
            <QueryErrorNotice
              title={t("Không thể tải danh sách người dùng", "Unable to load users")}
              detail={t("Không thể xác nhận danh sách tài khoản lúc này.", "Unable to verify the account list at this time.")}
              onRetry={usersQuery.refetch}
              isRetrying={usersQuery.isFetching}
            />
          ) : (
            <>
            {usersQuery.isFetchNextPageError ? (
              <QueryErrorNotice title={t("Không thể tải thêm trang người dùng", "Unable to load more users")} detail={t("Dữ liệu trang trước vẫn được giữ lại.", "Previously loaded pages are preserved.")} onRetry={usersQuery.fetchNextPage} isRetrying={usersQuery.isFetchingNextPage} retryLabel={t("Thử lại trang tiếp theo", "Retry next page")} />
            ) : usersQuery.isError ? (
              <QueryErrorNotice title={t("Danh sách người dùng chưa được cập nhật", "User list could not be refreshed")} detail={t("Đang hiển thị dữ liệu đã tải trước đó.", "Showing previously loaded data.")} onRetry={usersQuery.refetch} isRetrying={usersQuery.isFetching} />
            ) : null}
            {users.length === 0 ? (
              <EmptyState title={t("Chưa có người dùng", "No users yet")} detail={t("Chưa có tài khoản nào được đăng ký.", "No accounts have been registered yet.")} />
            ) : (
            <div className="table-panel">
              <div className="table-scroll" role="region" aria-label={t("Người dùng", "Users")} tabIndex={0}>
                <table>
                  <thead>
                    <tr>
                      <th>{t("Họ và tên", "Full name")}</th>
                      <th>{t("Email", "Email")}</th>
                      <th>{t("Vai trò", "Role")}</th>
                      <th>{t("Trạng thái", "Status")}</th>
                      <th>{t("Thao tác", "Actions")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((u) => (
                      <tr key={u.id} className="hover:bg-white/5 transition-colors">
                        <td className="font-semibold text-sm text-[#f1f3f5]">{u.name}</td>
                        <td className="text-xs text-[#9ea5b0]">{u.email}</td>
                        <td>
                          <Badge status={u.role} />
                        </td>
                        <td>
                          <Badge status={u.is_active ? "OK" : "DEGRADED"}>
                            {u.is_active ? t("Hoạt động", "Active") : t("Vô hiệu hóa", "Disabled")}
                          </Badge>
                        </td>
                        <td>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={usersQuery.isError}
                            onClick={() =>
                              toggleUserActiveMutation.mutate({
                                userId: u.id,
                                isActive: !u.is_active,
                              })
                            }
                          >
                            {u.is_active ? t("Khóa", "Disable") : t("Mở khóa", "Enable")}
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            )}
            </>
          )}
          {usersQuery.hasNextPage && !usersQuery.isFetchNextPageError && <div className="flex justify-center">
            <Button type="button" variant="secondary" disabled={usersQuery.isFetchingNextPage}
              onClick={() => { void usersQuery.fetchNextPage(); }}>
              {usersQuery.isFetchingNextPage ? t("Đang tải người dùng…", "Loading users…") : t("Tải thêm người dùng", "Load more users")}
            </Button>
          </div>}
        </div>
      )}

      {/* Tab 4: Workflows */}
      {activeTab === "workflows" && (
        <div className="space-y-4">
          <h2 className="text-base font-semibold text-[#f1f3f5]">{t("Đăng ký & Phê duyệt workflow ComfyUI", "ComfyUI workflow registration & approval")}</h2>

          {workflowsQuery.isLoading ? (
            <Skeleton className="h-44" />
          ) : !workflowsQuery.data ? (
            <QueryErrorNotice
              title={t("Không thể tải danh sách workflow", "Unable to load workflows")}
              detail={t("Không thể xác nhận trạng thái phê duyệt lúc này.", "Unable to verify approval status at this time.")}
              onRetry={workflowsQuery.refetch}
              isRetrying={workflowsQuery.isFetching}
            />
          ) : (
            <>
            {workflowsQuery.isFetchNextPageError ? (
              <QueryErrorNotice title={t("Không thể tải thêm trang workflow", "Unable to load more workflows")} detail={t("Dữ liệu trang trước vẫn được giữ lại.", "Previously loaded pages are preserved.")} onRetry={workflowsQuery.fetchNextPage} isRetrying={workflowsQuery.isFetchingNextPage} retryLabel={t("Thử lại trang tiếp theo", "Retry next page")} />
            ) : workflowsQuery.isError ? (
              <QueryErrorNotice title={t("Danh sách workflow chưa được cập nhật", "Workflow list could not be refreshed")} detail={t("Đang hiển thị dữ liệu đã tải trước đó.", "Showing previously loaded data.")} onRetry={workflowsQuery.refetch} isRetrying={workflowsQuery.isFetching} />
            ) : null}
            {workflows.length === 0 ? (
              <EmptyState title={t("Chưa có workflow", "No workflows yet")} detail={t("Chưa có workflow nào được đăng ký.", "No workflows have been registered yet.")} />
            ) : (
            <div className="table-panel">
              <div className="table-scroll" role="region" aria-label="Workflow" tabIndex={0}>
                <table>
                  <thead>
                    <tr>
                      <th>{t("Mã workflow", "Workflow code")}</th>
                      <th>{t("Chế độ", "Mode")}</th>
                      <th>{t("Phiên bản", "Version")}</th>
                      <th>{t("Trạng thái", "Status")}</th>
                      <th>{t("Phê duyệt", "Approval")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {workflows.map((wf) => (
                      <tr key={wf.id}>
                        <td className="font-semibold text-sm text-[#f1f3f5]">{wf.code}</td>
                        <td className="text-xs uppercase text-blue-400 font-semibold">{wf.mode}</td>
                        <td className="text-xs text-[#9ea5b0]">v{wf.version}</td>
                        <td>
                          <Badge status={wf.enabled ? "OK" : "DEGRADED"}>
                            {wf.enabled ? t("Đang kích hoạt", "Enabled") : t("Chưa bật", "Disabled")}
                          </Badge>
                        </td>
                        <td>
                          <Button
                            size="sm"
                            variant={wf.enabled ? "outline" : "primary"}
                            disabled={workflowsQuery.isError}
                            onClick={() =>
                              approveWorkflowMutation.mutate({
                                workflowId: wf.id,
                                enabled: !wf.enabled,
                              })
                            }
                            isLoading={approveWorkflowMutation.isPending}
                          >
                            {wf.enabled ? t("Tắt workflow", "Disable workflow") : t("Phê duyệt & Bật", "Approve & enable")}
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            )}
            </>
          )}
          {workflowsQuery.hasNextPage && !workflowsQuery.isFetchNextPageError && <div className="flex justify-center">
            <Button type="button" variant="secondary" disabled={workflowsQuery.isFetchingNextPage}
              onClick={() => { void workflowsQuery.fetchNextPage(); }}>
              {workflowsQuery.isFetchingNextPage ? t("Đang tải workflow…", "Loading workflows…") : t("Tải thêm workflow", "Load more workflows")}
            </Button>
          </div>}
        </div>
      )}

      {/* Create User Dialog */}
      <Dialog
        isOpen={isCreateUserOpen}
        onClose={() => setIsCreateUserOpen(false)}
        title={t("Thêm tài khoản người dùng", "Add user account")}
        description={t("Tạo tài khoản cho biên tập viên video hoặc quản trị viên hệ thống.", "Create an account for a video editor or system administrator.")}
      >
        <form onSubmit={handleUserSubmit((data) => createUserMutation.mutateAsync(data))} className="space-y-4">
          <Input id="u_name" label={t("Họ và tên", "Full name")} error={validationMessage(userErrors.name?.message)} {...registerUser("name")} />
          <Input id="u_email" label={t("Email", "Email")} type="email" error={validationMessage(userErrors.email?.message)} {...registerUser("email")} />
          <Input
            id="u_pass"
            label={t("Mật khẩu (Tối thiểu 12 ký tự)", "Password (at least 12 characters)")}
            type="password"
            error={validationMessage(userErrors.password?.message)}
            {...registerUser("password")}
          />
          <Select id="u_role" label={t("Vai trò", "Role")} {...registerUser("role")}>
            <option value="EDITOR">{t("Biên tập viên sản xuất", "Production editor")}</option>
            <option value="ADMIN">{t("Quản trị viên hạ tầng", "Infrastructure administrator")}</option>
          </Select>

          <div className="flex flex-wrap justify-end gap-3 pt-4 border-t border-[#2c3038]">
            <Button type="button" variant="outline" onClick={() => setIsCreateUserOpen(false)}>
              {t("Hủy", "Cancel")}
            </Button>
            <Button type="submit" variant="primary" isLoading={isUserSubmitting}>
              {t("Tạo tài khoản", "Create account")}
            </Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
