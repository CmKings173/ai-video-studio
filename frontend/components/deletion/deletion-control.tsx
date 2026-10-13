"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { Loader2, Trash2 } from "lucide-react";
import { deleteResource, getDeletionOperation, getDeletionPreview, type DeletionOperation, type DeletionPreview, type DeletionResourceType } from "@/lib/api/deletions";
import { getErrorMessage } from "@/lib/api/errors";
import { useI18n } from "@/lib/i18n";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import { queryKeys } from "@/lib/query/query-keys";

export interface DeletionControlProps {
  resource: { type: DeletionResourceType; id: string; name: string };
  invalidationKeys: readonly QueryKey[];
  disabled?: boolean;
  onComplete?: () => void;
}

const terminalStatuses = new Set([
  "COMPLETED", "SUCCEEDED", "DONE", "DELETED", "CLEANUP_COMPLETE", "CLEANED_UP", "PARTIAL", "FAILED", "ERROR", "CANCELLED", "BLOCKED", "REJECTED",
]);
const failedStatuses = new Set(["FAILED", "ERROR", "CANCELLED", "BLOCKED", "REJECTED"]);
const cleanupCompleteStatuses = new Set(["COMPLETED", "SUCCEEDED", "DONE", "FINISHED", "CLEANED", "CLEANED_UP", "CLEANUP_COMPLETE"]);
const logicallyDeletedStatuses = new Set([
  "WAITING_FOR_DEPENDENCIES", "LOGICALLY_DELETED", "CLEANUP_PENDING",
  "CLEANUP_COMPLETE", "CLEANUP_FAILED", "CLEANUP_BLOCKED",
]);

function recordOf(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function normalizedStatus(value: unknown): string {
  return typeof value === "string" ? value.trim().toUpperCase().replace(/[ -]+/g, "_") : "";
}

function countValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (Array.isArray(value)) return value.length;
  const record = recordOf(value);
  if (!record) return null;
  for (const key of ["count", "total", "eligible", "blocked"]) {
    const count = record[key];
    if (typeof count === "number" && Number.isFinite(count)) return count;
  }
  return null;
}

function cleanupPercent(operation: DeletionOperation): number | null {
  const direct = operation.cleanup_progress_percent;
  if (typeof direct === "number" && Number.isFinite(direct)) return Math.min(100, Math.max(0, direct));
  const progress = operation.cleanup_progress;
  if (typeof progress === "number" && Number.isFinite(progress)) {
    return Math.min(100, Math.max(0, progress <= 1 ? progress * 100 : progress));
  }
  const record = recordOf(progress);
  if (!record) return null;
  for (const key of ["percent", "progress_percent", "percentage"]) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) return Math.min(100, Math.max(0, value <= 1 ? value * 100 : value));
  }
  const current = record.completed ?? record.current;
  const total = record.total;
  return typeof current === "number" && typeof total === "number" && total > 0
    ? Math.min(100, Math.max(0, current / total * 100)) : null;
}

function cleanupIsComplete(operation: DeletionOperation): boolean {
  // Preview counts, progress and retry reasons cannot certify physical cleanup.
  return cleanupCompleteStatuses.has(normalizedStatus(operation.cleanup_status || operation.status));
}

function operationIsSettled(operation: DeletionOperation): boolean {
  const status = normalizedStatus(operation.status);
  if (failedStatuses.has(status)) return true;
  return terminalStatuses.has(status) && cleanupIsComplete(operation);
}

function previewMatches(preview: DeletionPreview, resource: DeletionControlProps["resource"]): boolean {
  if (preview.resource_id !== resource.id || !Number.isInteger(preview.revision) || (preview.revision ?? 0) < 1) return false;
  const type = normalizedStatus(preview.resource_type).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
  const expected = resource.type.replace(/-/g, "_");
  return type === expected || (resource.type === "final-version" && ["final_video", "final", "finalversion"].includes(type));
}

function operationMatches(operation: DeletionOperation, operationId: string, resource: DeletionControlProps["resource"]): boolean {
  const type = normalizedStatus(operation.resource_type).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
  const expected = resource.type.replace(/-/g, "_");
  return operation.id === operationId && operation.resource_id === resource.id
    && (type === expected || (resource.type === "final-version" && ["final_video", "final", "finalversion"].includes(type)));
}

function acceptedOperation(value: unknown): DeletionOperation | null {
  const outer = recordOf(value);
  const candidate = recordOf(outer?.operation) ?? outer;
  return candidate && typeof candidate.id === "string" && typeof candidate.status === "string"
    ? candidate as DeletionOperation : null;
}

function humanized(value: string): string {
  return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

const blockerCopy: Record<string, [string, string]> = {
  ACTIVE_GENERATIONS: ["Còn tác vụ tạo video đang chạy.", "Generation work is still running."],
  ACTIVE_DIRECTOR_RUNS: ["Còn tác vụ Director đang chạy.", "Director work is still running."],
  ACTIVE_FINAL_VIDEOS: ["Còn tác vụ ghép video đang chạy.", "Video assembly is still running."],
  ACTIVE_ASSEMBLIES: ["Còn tác vụ ghép video đang chạy.", "Video assembly is still running."],
  SELECTED_GENERATION: ["Bản tạo này đang được chọn cho cảnh.", "This generation is selected for its scene."],
  CHILD_GENERATION_REFERENCES: ["Có bản tạo sau đang dựa trên bản này.", "A later generation is based on this one."],
  DIRECTOR_RUN_REFERENCES: ["Một tác vụ Director vẫn tham chiếu đến tài nguyên này.", "A Director run still refers to this resource."],
  FINAL_VIDEO_REFERENCES: ["Video đã ghép vẫn tham chiếu đến tài nguyên này.", "An assembled video still refers to this resource."],
  CURRENT_FINAL_VIDEO: ["Đây là phiên bản video hiện tại.", "This is the current final video."],
  OUTPUT_WRITE_CLAIMS: ["Tệp đầu ra vẫn đang được ghi.", "Output files are still being written."],
  DELETION_OPERATION_PENDING: ["Yêu cầu xóa đang được xử lý.", "A deletion request is already pending."],
  ALREADY_LOGICALLY_DELETED: ["Tài nguyên đã được ẩn khỏi hệ thống.", "The resource has already been hidden."],
};

const countLabels: Record<string, [string, string]> = {
  scenes: ["Cảnh", "Scenes"],
  generations: ["Bản tạo", "Generations"],
  child_generations: ["Bản tạo phụ thuộc", "Dependent generations"],
  generation_attempts: ["Lượt chạy tạo video", "Generation attempts"],
  generation_assets: ["Tệp liên kết với bản tạo", "Generation asset links"],
  director_runs: ["Tác vụ Director", "Director runs"],
  director_run_members: ["Cảnh trong tác vụ Director", "Director run members"],
  final_videos: ["Phiên bản video", "Final versions"],
  final_video_scenes: ["Cảnh trong bản ghép", "Assembled scenes"],
  output_assets: ["Tệp đầu ra", "Output files"],
  shared_output_assets: ["Tệp dùng chung được giữ lại", "Shared output files preserved"],
};

function localizedStatus(value: unknown, t: (vi: string, en: string) => string): string {
  const status = normalizedStatus(value);
  const copy: Record<string, [string, string]> = {
    DELETE_REQUESTED: ["Đang chờ xác nhận", "Awaiting confirmation"],
    WAITING_FOR_DEPENDENCIES: ["Đang chờ tác vụ liên quan", "Waiting for related work"],
    LOGICALLY_DELETED: ["Đã ẩn; đang chờ dọn tệp", "Hidden; file cleanup is pending"],
    CLEANUP_PENDING: ["Đang dọn tệp", "File cleanup is pending"],
    CLEANUP_COMPLETE: ["Đã dọn tệp xong", "File cleanup complete"],
    CLEANUP_FAILED: ["Dọn tệp gặp lỗi; hệ thống sẽ thử lại", "File cleanup failed; retry scheduled"],
    CLEANUP_BLOCKED: ["Dọn tệp đang bị chặn; hệ thống sẽ thử lại", "File cleanup is blocked; retry scheduled"],
  };
  const labels = copy[status];
  return labels ? t(labels[0], labels[1]) : humanized(status || "UNKNOWN");
}

function localizedReason(value: unknown, t: (vi: string, en: string) => string): string | null {
  if (typeof value !== "string") return null;
  const copy: Record<string, [string, string]> = {
    "A deletion request is already pending.": ["Yêu cầu xóa đang được xử lý.", "A deletion request is already pending."],
    "Deletion has already been requested.": ["Yêu cầu xóa đã được gửi trước đó.", "Deletion has already been requested."],
    "Finish active work before deleting this video.": ["Hãy chờ các tác vụ đang chạy hoàn tất trước khi xóa video.", "Finish active work before deleting this video."],
    "Resolve active work and references before deleting this scene.": ["Hãy xử lý các tác vụ và liên kết đang hoạt động trước khi xóa cảnh.", "Resolve active work and references before deleting this scene."],
    "Resolve active work and downstream references before deleting this generation.": ["Hãy xử lý tác vụ và các tài nguyên đang phụ thuộc trước khi xóa bản tạo.", "Resolve active work and downstream references before deleting this generation."],
    "Resolve active work and the current-version reference before deleting this final video.": ["Hãy chờ tác vụ hoàn tất và chọn phiên bản khác trước khi xóa phiên bản hiện tại.", "Resolve active work and the current-version reference before deleting this final video."],
  };
  const labels = copy[value];
  return labels ? t(labels[0], labels[1]) : value;
}

function safeText(value: unknown): string | null {
  return typeof value === "string" || typeof value === "number" ? String(value) : null;
}

function safeBlocker(value: unknown, t: (vi: string, en: string) => string): string | null {
  if (typeof value === "string") return value;
  const record = recordOf(value);
  if (!record) return null;
  const code = typeof record.code === "string" ? record.code : "";
  const translated = blockerCopy[code];
  if (translated) return t(translated[0], translated[1]);
  if (typeof record.message === "string") return record.message;
  if (code) return humanized(code);
  if (typeof record.label === "string") return record.label;
  return null;
}

export function DeletionControl({ resource, invalidationKeys, disabled = false, onComplete }: DeletionControlProps) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [isOpen, setIsOpen] = useState(false);
  const [preview, setPreview] = useState<DeletionPreview | null>(null);
  const [operationId, setOperationId] = useState<string | null>(null);
  const [operationFinished, setOperationFinished] = useState(false);
  const completionNotifiedFor = useRef<string | null>(null);
  const [acceptedWithoutTracking, setAcceptedWithoutTracking] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);

  const invalidateAfterDeletion = () => Promise.all(
    invalidationKeys.map((queryKey) => queryClient.invalidateQueries({ queryKey }))
  );

  const notifyLogicalDeletion = useCallback((operation: DeletionOperation) => {
    if (!logicallyDeletedStatuses.has(normalizedStatus(operation.status))
      || completionNotifiedFor.current === operation.id) return;
    completionNotifiedFor.current = operation.id;
    void Promise.all(invalidationKeys.map((queryKey) => queryClient.invalidateQueries({ queryKey })));
    onComplete?.();
  }, [invalidationKeys, queryClient, onComplete]);

  const previewMutation = useMutation({
    mutationFn: () => getDeletionPreview(resource.type, resource.id),
    onSuccess: (result) => setPreview(result),
    onError: (error) => setRequestError(getErrorMessage(error, t("Không tải được thông tin xóa.", "Unable to load deletion details."))),
  });

  const deleteMutation = useMutation({
    mutationFn: (input: { expected_revision: number; confirmation_token?: string }) =>
      deleteResource(resource.type, resource.id, input),
    onSuccess: (result) => {
      setRequestError(null);
      const operation = acceptedOperation(result);
      if (operation) {
        if (!operationMatches(operation, operation.id, resource)) {
          setAcceptedWithoutTracking(true);
          setOperationFinished(true);
          setRequestError(t("Máy chủ đã nhận yêu cầu nhưng trả về tiến trình không khớp tài nguyên.", "The server accepted the request but returned an operation for a different resource."));
          void invalidateAfterDeletion();
          return;
        }
        setOperationId(operation.id);
        setOperationFinished(false);
        notifyLogicalDeletion(operation);
        return;
      }
      setAcceptedWithoutTracking(true);
      setOperationFinished(true);
      void invalidateAfterDeletion();
    },
    onError: (error) => setRequestError(getErrorMessage(error, t("Không thể gửi yêu cầu xóa.", "Unable to submit the deletion request."))),
  });

  const operationQuery = useQuery({
    queryKey: operationId ? queryKeys.deletions.operation(operationId) : queryKeys.deletions.operation(""),
    queryFn: () => {
      if (!operationId) throw new Error("Missing deletion operation id");
      return getDeletionOperation(operationId);
    },
    enabled: !!operationId,
    refetchInterval: (query) => {
      if (!operationId || operationFinished) return false;
      const current = query.state.data;
      if (current && !operationMatches(current, operationId, resource)) return false;
      if (current && operationIsSettled(current)) return false;
      const status = normalizedStatus(current?.status);
      if (status === "CLEANUP_FAILED" || status === "CLEANUP_BLOCKED") {
        const retryAt = typeof current?.next_attempt_at === "string"
          ? Date.parse(current.next_attempt_at) : Number.NaN;
        return Number.isFinite(retryAt)
          ? Math.max(5_000, Math.min(60_000, retryAt - Date.now() + 500))
          : 30_000;
      }
      return 2_000;
    },
    refetchOnWindowFocus: true,
  });

  const operation = operationQuery.data;
  const operationMismatch = Boolean(operationId && operation
    && !operationMatches(operation, operationId, resource));
  const operationDisplayFinished = operationFinished || operationMismatch
    || Boolean(operation && operationId && !operationMismatch && operationIsSettled(operation));
  const visibleRequestError = operationMismatch
    ? t("Máy chủ trả về tiến trình không khớp tài nguyên; chưa thể xác minh kết quả.", "The server returned an operation for a different resource; its result cannot be verified.")
    : requestError;

  useEffect(() => {
    const operation = operationQuery.data;
    if (!operation || !operationId || operationMismatch) return;
    // Logical visibility is independent of the worker's physical cleanup retries.
    notifyLogicalDeletion(operation);
  }, [operationQuery.data, operationId, operationMismatch, notifyLogicalDeletion]);

  const openDialog = () => {
    setPreview(null);
    setOperationId(null);
    setOperationFinished(false);
    setAcceptedWithoutTracking(false);
    setRequestError(null);
    previewMutation.reset();
    deleteMutation.reset();
    setIsOpen(true);
    previewMutation.mutate();
  };

  const canConfirm = !!preview && preview.allowed === true && previewMatches(preview, resource);
  const blockers = (Array.isArray(preview?.blockers) ? preview.blockers : []).map((value) => safeBlocker(value, t)).filter((value): value is string => !!value);
  const operationStatus = normalizedStatus(operation?.status);
  const isCleanupPending = !!operation && preview?.async_cleanup === true && !cleanupIsComplete(operation);
  const cleanupErrorCode = safeText(operation?.cleanup_error_code);
  const operationErrorCode = safeText(operation?.error_code);
  const isRetentionWait = operationStatus === "CLEANUP_PENDING" && cleanupErrorCode === "RETENTION_PENDING";
  const hasCleanupError = operationStatus === "CLEANUP_FAILED" || operationStatus === "CLEANUP_BLOCKED"
    || (!!cleanupErrorCode && !isRetentionWait);
  const cleanupProgress = operation ? cleanupPercent(operation) : null;
  const resourceLabel = resource.type === "final-version" ? t("phiên bản cuối", "final version")
    : resource.type === "generation" ? t("lần tạo clip", "generation")
      : resource.type === "scene" ? t("cảnh", "scene") : t("video", "video");

  return (
    <>
      <Button type="button" size="sm" variant={operationId && !operationDisplayFinished ? "secondary" : "danger"} disabled={disabled}
        onClick={() => operationId && !operationDisplayFinished ? setIsOpen(true) : openDialog()}
        aria-label={operationId && !operationDisplayFinished ? t("Xem tiến độ xóa", "View deletion progress") + " " + resourceLabel : t("Xóa", "Delete") + " " + resourceLabel}>
        {operationId && !operationDisplayFinished ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
        <span>{operationId && !operationDisplayFinished ? t("Tiến độ", "Progress") : t("Xóa", "Delete")}</span>
      </Button>
      {operationId && !operationDisplayFinished && (
        <span role="status" className="inline-flex items-center gap-1.5 text-xs text-amber-300">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          {t("Đang xử lý yêu cầu xóa", "Deletion is in progress")}
        </span>
      )}
      <Dialog isOpen={isOpen} onClose={() => setIsOpen(false)}
        title={t("Xem trước khi xóa", "Review before deleting")}
        description={t("Kiểm tra phạm vi ảnh hưởng và trạng thái dọn tệp trước khi xác nhận.", "Review the impact and file cleanup status before confirming.")}
        maxWidth="md">
        <div className="space-y-4">
          <div className="rounded-md border border-[#2c3038] bg-[#0b101a] p-3">
            <p className="text-sm font-semibold text-[#f1f3f5]">{safeText(preview?.name) || resource.name}</p>
            <p className="mt-1 text-xs text-[#9ea5b0]">{resourceLabel} · {resource.id}</p>
            {preview?.revision !== undefined && <p className="mt-1 text-xs text-[#9ea5b0]">{t("Phiên bản dữ liệu", "Resource revision")}: {preview.revision}</p>}
          </div>

          {previewMutation.isPending && <p role="status" className="text-sm text-[#9ea5b0]">{t("Đang tải bản xem trước…", "Loading deletion preview…")}</p>}
          {previewMutation.isError && (
            <Alert variant="destructive" title={t("Không tải được bản xem trước", "Unable to load preview")}>
              {visibleRequestError || getErrorMessage(previewMutation.error)}
              <Button type="button" size="sm" variant="secondary" className="ml-2" disabled={previewMutation.isPending} onClick={() => { setRequestError(null); previewMutation.mutate(); }}>
                {t("Thử lại", "Retry")}
              </Button>
            </Alert>
          )}

          {preview && (
            <div className="space-y-3">
              {!previewMatches(preview, resource) && <Alert variant="destructive" title={t("Bản xem trước không khớp tài nguyên hiện tại.", "The preview does not match this resource.")}>
                {t("Không thể xác nhận xóa từ bản xem trước này. Hãy đóng hộp thoại và tải lại tài nguyên.", "Deletion is disabled for this preview. Close this dialog and reload the resource.")}
              </Alert>}
              {preview.allowed === true ? (
                <Alert variant="warning" title={t("Hành động này không thể hoàn tác từ giao diện.", "This action cannot be undone from the interface.")}>
                  {safeText(preview.reason) || t("Hệ thống sẽ kiểm tra lại phiên bản trước khi chấp nhận.", "The server will recheck the revision before accepting the request.")}
                </Alert>
              ) : (
                <Alert variant="destructive" title={t("Chưa thể xóa tài nguyên này", "This resource cannot be deleted yet")}>
                  {localizedReason(preview.reason, t) || t("Bản xem trước không cho phép xóa.", "The preview does not allow deletion.")}
                  {blockers.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-5">{blockers.map((item, index) => <li key={index}>{item}</li>)}</ul>}
                </Alert>
              )}

              <dl className="grid grid-cols-1 gap-x-4 gap-y-2 text-xs sm:grid-cols-2">
                {preview.current !== undefined && preview.current !== null && (
                  <>
                    <dt className="text-[#9ea5b0]">{t("Trạng thái hiện tại", "Current state")}</dt>
                    <dd className="break-words text-[#f1f3f5]">{typeof preview.current === "string" || typeof preview.current === "number" ? String(preview.current) : (() => {
                      const current = recordOf(preview.current);
                      const state = current?.status ?? current?.state;
                      return typeof state === "string" ? localizedStatus(state, t) : t("Đã kiểm tra", "Checked");
                    })()}</dd>
                  </>
                )}
                {Object.entries(recordOf(preview.affected_counts) ?? {}).map(([key, value]) => {
                  const count = countValue(value);
                  return count === null ? null : <Fragment key={key}><dt className="text-[#9ea5b0]">{countLabels[key] ? t(...countLabels[key]) : humanized(key)}</dt><dd className="text-[#f1f3f5]">{count}</dd></Fragment>;
                })}
                {preview.shared_assets_preserved !== undefined && <>
                  <dt className="text-[#9ea5b0]">{t("Tài sản dùng chung", "Shared assets")}</dt>
                  <dd className="text-[#f1f3f5]">{countValue(preview.shared_assets_preserved) ?? (preview.shared_assets_preserved ? t("Được giữ lại", "Preserved") : t("Không có tài sản dùng chung được giữ lại", "No shared assets will be preserved"))}</dd>
                </>}
                {preview.cleanup_eligible_outputs !== undefined && <>
                  <dt className="text-[#9ea5b0]">{t("Đầu ra đủ điều kiện dọn", "Outputs eligible for cleanup")}</dt>
                  <dd className="text-[#f1f3f5]">{countValue(preview.cleanup_eligible_outputs) ?? t("Được hệ thống xác định", "Determined by the server")}</dd>
                </>}
                {preview.cleanup_blocked_outputs !== undefined && <>
                  <dt className="text-[#9ea5b0]">{t("Đầu ra bị giữ lại", "Outputs blocked from cleanup")}</dt>
                  <dd className="text-[#f1f3f5]">{countValue(preview.cleanup_blocked_outputs) ?? t("Được hệ thống xác định", "Determined by the server")}</dd>
                </>}
              </dl>

              {preview.async_cleanup === true && (
                <Alert variant="warning" title={t("Dọn tệp có thể tiếp tục sau khi xóa.", "File cleanup may continue after deletion.")}>
                  {t("Giao diện sẽ theo dõi tiến độ; yêu cầu được chấp nhận không có nghĩa là tệp đã bị xóa vĩnh viễn.", "The interface will track progress; an accepted request does not mean files have been permanently removed.")}
                </Alert>
              )}
            </div>
          )}

          {visibleRequestError && !previewMutation.isError && !deleteMutation.isError && <Alert variant="destructive">{visibleRequestError}</Alert>}
          {deleteMutation.isError && <Alert variant="destructive" title={t("Yêu cầu chưa được chấp nhận", "Request was not accepted")}>{visibleRequestError || getErrorMessage(deleteMutation.error)}</Alert>}

          {deleteMutation.isSuccess && (
            <div className="space-y-3" aria-live="polite">
              {operation ? (
                <>
                  <Alert variant={failedStatuses.has(operationStatus) || hasCleanupError ? "destructive" : isCleanupPending ? "warning" : "info"}
                    title={failedStatuses.has(operationStatus) || hasCleanupError ? t("Quy trình xóa gặp vấn đề", "Deletion operation needs attention")
                      : isCleanupPending ? t("Yêu cầu đã được chấp nhận; dọn tệp vẫn đang chờ.", "Request accepted; file cleanup is still pending.")
                        : operationDisplayFinished ? t("Quy trình xóa đã kết thúc.", "Deletion operation has finished.")
                          : t("Yêu cầu đã được chấp nhận.", "Deletion request accepted.")}>
                    {t("Trạng thái", "Status")}: {localizedStatus(operation.status, t)}
                    {(cleanupErrorCode || operationErrorCode) && <span className="ml-2">· {isRetentionWait ? t("Lý do chờ", "Waiting reason") : t("Mã lỗi an toàn", "Safe error code")}: {cleanupErrorCode || operationErrorCode}</span>}
                  </Alert>
                  {!operationDisplayFinished && operationQuery.isError && <Alert variant="destructive">{t("Chưa lấy được tiến độ mới nhất. Hệ thống sẽ thử lại.", "Could not fetch the latest progress. The interface will retry.")}</Alert>}
                  {isCleanupPending && cleanupProgress !== null && <Progress value={cleanupProgress} label={t("Tiến độ dọn tệp", "File cleanup progress")} />}
                  {operationDisplayFinished && !failedStatuses.has(operationStatus) && isCleanupPending && <p role="status" className="text-xs text-amber-300">{t("Tác vụ chính đã kết thúc nhưng trạng thái dọn tệp chưa xác nhận hoàn tất.", "The main operation ended, but file cleanup is not confirmed complete.")}</p>}
                </>
              ) : operationId ? (
                <Alert variant={operationQuery.isError ? "warning" : "info"} title={t("Yêu cầu đã được chấp nhận.", "Deletion request accepted.")}>
                  {operationQuery.isError
                    ? <>{t("Chưa tải được trạng thái tiến trình. Hệ thống sẽ thử lại.", "The operation status could not be loaded. The interface will retry.")} <Button type="button" size="sm" variant="secondary" className="ml-2" onClick={() => { void operationQuery.refetch(); }}>{t("Thử lại", "Retry")}</Button></>
                    : t("Đang tải trạng thái dọn dẹp…", "Loading cleanup status…")}
                </Alert>
              ) : acceptedWithoutTracking ? (
                <Alert variant="warning" title={t("Máy chủ đã nhận yêu cầu.", "The server accepted the request.")}>
                  {t("Không có mã tiến trình để theo dõi. Trạng thái dọn tệp chưa được xác nhận.", "No operation ID was returned, so file cleanup status cannot be confirmed.")}
                </Alert>
              ) : null}
            </div>
          )}

          <div className="flex flex-wrap justify-end gap-2 border-t border-[#2c3038] pt-4">
            <Button type="button" variant="outline" onClick={() => setIsOpen(false)}>
              {deleteMutation.isSuccess ? t("Đóng", "Close") : t("Hủy", "Cancel")}
            </Button>
            {!deleteMutation.isSuccess && (
              <Button type="button" variant="danger" disabled={!canConfirm || previewMutation.isPending || deleteMutation.isPending}
                isLoading={deleteMutation.isPending}
                onClick={() => {
                  if (!preview || !canConfirm || !Number.isInteger(preview.revision)) return;
                  deleteMutation.mutate({ expected_revision: preview.revision as number,
                    ...(preview.confirmation_token ? { confirmation_token: preview.confirmation_token } : {}) });
                }}>
                <Trash2 className="h-3.5 w-3.5" />
                {t("Xác nhận xóa", "Confirm deletion")}
              </Button>
            )}
          </div>
        </div>
      </Dialog>
    </>
  );
}
