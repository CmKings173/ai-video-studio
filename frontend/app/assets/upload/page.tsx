"use client";

import { useI18n } from "@/lib/i18n";
import React, { useState, useRef, useEffect } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  UploadCloud,
  FileImage,
  FileVideo,
  FileAudio,
  Download,
} from "lucide-react";
import { listAssets, createUploadUrl, uploadBytesToStorage, completeAsset, downloadAsset, getAsset, getUploadPolicy, validateUploadFiles, assetValidationQueryOptions, ASSET_VALIDATION_TIMEOUT_MS, defaultAssetRole, isAssetRoleCompatible } from "@/lib/api/assets";
import { listProjects } from "@/lib/api/projects";
import { listProducts } from "@/lib/api/products";
import { flattenPageItems, nextPageParam } from "@/lib/api/pagination";
import { queryKeys } from "@/lib/query/query-keys";
import { getErrorMessage } from "@/lib/api/errors";
import { useIdempotentAction } from "@/lib/hooks/use-idempotent-action";
import { closePresignedDownloadTab, navigatePresignedDownload, reservePresignedDownloadTab } from "@/lib/utils/presigned-download";
import { PageHeader, StatusPill, EmptyState, QueryErrorNotice } from "@/components/page-kit";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Alert } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import type { AssetRole, AssetDTO } from "@/lib/api/types";

export default function AssetUploadPage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const uploadAction = useIdempotentAction<{
    filename: string;
    size: number;
    contentType: string;
    lastModified: number;
    role: string;
    project_id: string | null;
    product_id: string | null;
  }>();
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Form State
  const [file, setSelectedFile] = useState<File | null>(null);
  const [scopeType, setScopeType] = useState<"project" | "product">("project");
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [selectedProductId, setSelectedProductId] = useState("");
  const [selectedRole, setSelectedRole] = useState<AssetRole>("PROJECT_REFERENCE");

  // Upload progress state
  const [transferPhase, setUploadPhase] = useState<"idle" | "getting_url" | "uploading_minio" | "checking" | "validating" | "done" | "error">("idle");
  const [localError, setUploadError] = useState<string | null>(null);

  const [validation, setValidation] = useState<{ asset: AssetDTO; startedAt: number } | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [roleManuallySelected, setRoleManuallySelected] = useState(false);
  const [roleNotice, setRoleNotice] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const mounted = useRef(true);
  const validationRetry = useRef<{ controller: AbortController; timer: number } | null>(null);
  const downloadInFlight = useRef(false);
  const translationRef = useRef(t);
  useEffect(() => { translationRef.current = t; }, [t]);
  const policyQuery = useQuery({ queryKey: ["asset-upload-policy"], queryFn: getUploadPolicy, retry: false });
  const policy = policyQuery.isPending || policyQuery.isError ? undefined : policyQuery.data;
  const validateFiles = (files: ArrayLike<File>) => policy
    ? (() => {
      const error = validateUploadFiles(files, policy);
      if (!error) return null;
      if (files.length !== 1) return t("Vui lòng chọn đúng một tệp.", "Please select exactly one file.");
      if (!policy.allowed_content_types.includes(files[0].type)) return t("Định dạng tệp không được hỗ trợ.", "This file format is not supported.");
      if (files[0].size <= 0) return t("Tệp rỗng không thể tải lên.", "Empty files cannot be uploaded.");
      if (files[0].size > policy.max_upload_bytes) return t(`Tệp vượt quá giới hạn ${(policy.max_upload_bytes / 1024 ** 2).toFixed(0)} MiB.`, `File exceeds the ${(policy.max_upload_bytes / 1024 ** 2).toFixed(0)} MiB limit.`);
      return error;
    })()
    : t("Chưa tải được cấu hình tải lên. Vui lòng thử lại.", "Upload settings are unavailable. Please retry.");
  const validationQuery = useQuery({
    ...assetValidationQueryOptions(validation?.asset ?? null, validation?.startedAt ?? 0),
    enabled: !!validation && transferPhase === "validating" && validation.asset.status === "VALIDATING",
  });
  const validatedAsset = validationQuery.data;
  const validationFailed = validatedAsset?.status === "FAILED";
  const uploadPhase = transferPhase === "validating"
    ? validatedAsset?.status === "READY" ? "done" : validationFailed || validationQuery.error ? "error" : "validating"
    : transferPhase;
  const uploadBusy = ["getting_url", "uploading_minio", "checking", "validating"].includes(uploadPhase);
  const selectedFile = uploadPhase === "done" ? null : file;
  const uploadError = transferPhase === "validating" && validationQuery.error
    ? getErrorMessage(validationQuery.error, t("Không thể kiểm tra tài nguyên.", "Unable to validate the asset."))
    : transferPhase === "validating" && validationFailed
    ? t("Kiểm tra tài nguyên thất bại. Bạn có thể thử xác thực lại.", "Asset validation failed. You can retry validation.")
    : localError;
  const resetUploadAction = uploadAction.reset;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (validationRetry.current) {
        window.clearTimeout(validationRetry.current.timer);
        validationRetry.current.controller.abort();
        validationRetry.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!validation) return;
    if (uploadPhase === "done") {
      if (fileInputRef.current) fileInputRef.current.value = "";
      resetUploadAction();
      void queryClient.invalidateQueries({ queryKey: queryKeys.assets.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.summary });
      return;
    }
    if (uploadPhase !== "validating") return;
    const timer = window.setTimeout(() => {
      setUploadPhase("error");
      setUploadError(translationRef.current("Hết thời gian chờ kiểm tra tài nguyên. Bạn có thể kiểm tra lại trạng thái.", "Asset validation timed out. You can check the status again."));
    }, Math.max(0, ASSET_VALIDATION_TIMEOUT_MS - (Date.now() - validation.startedAt)));
    return () => window.clearTimeout(timer);
  }, [validation, uploadPhase, resetUploadAction, queryClient]);

  function beginValidation(asset: AssetDTO) {
    if (!mounted.current) return;
    setValidation({ asset, startedAt: Date.now() });
    setUploadPhase("validating");
  }

  async function retryValidation() {
    if (!validation || uploadBusy || validationRetry.current) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      controller.abort();
      validationRetry.current = null;
      if (!mounted.current) return;
      setUploadPhase("error");
      setUploadError(t("Hết thời gian chờ kiểm tra tài nguyên. Bạn có thể kiểm tra lại trạng thái.", "Asset validation timed out. You can check the status again."));
    }, ASSET_VALIDATION_TIMEOUT_MS);
    validationRetry.current = { controller, timer };
    setUploadError(null);
    setUploadPhase("checking");
    try {
      const asset = validatedAsset?.status === "FAILED"
        // The backend replays VALIDATING/READY rather than scheduling a duplicate job.
        ? await completeAsset(validation.asset.id, { retry_validation: true }, controller.signal)
        : await getAsset(validation.asset.id, controller.signal);
      if (controller.signal.aborted) return;
      beginValidation(asset);
    } catch (err) {
      if (!mounted.current || controller.signal.aborted) return;
      setUploadPhase("error");
      setUploadError(getErrorMessage(err, t("Không thể kiểm tra tài nguyên.", "Unable to validate the asset.")));
    } finally {
      if (mounted.current) window.clearTimeout(timer);
      if (validationRetry.current?.controller === controller) validationRetry.current = null;
    }
  }

  // Load projects & products
  const projectsQuery = useInfiniteQuery({
    queryKey: queryKeys.projects.list({ size: 50, archived: false }),
    queryFn: ({ pageParam }) => listProjects({ size: 50, page: pageParam, archived: false }),
    initialPageParam: 1,
    getNextPageParam: nextPageParam,
  });

  const productsQuery = useInfiniteQuery({
    queryKey: queryKeys.products.list({ size: 50, archived: false }),
    queryFn: ({ pageParam }) => listProducts({ size: 50, page: pageParam, archived: false }),
    initialPageParam: 1,
    getNextPageParam: nextPageParam,
  });
  const projectOptions = flattenPageItems(projectsQuery.data?.pages);
  const productOptions = flattenPageItems(productsQuery.data?.pages);

  // Load existing assets
  const { data: assetsData, isLoading: assetsLoading, isError: assetsError, isFetching: assetsFetching, refetch: refetchAssets } = useQuery({
    queryKey: queryKeys.assets.list({ size: 10 }),
    queryFn: () => listAssets({ size: 10 }),
  });

  const selectFiles = (files: ArrayLike<File>) => {
    if (uploadBusy) return;
    const error = validateFiles(files);
    setUploadError(error);
    setValidation(null);
    setUploadPhase("idle");
    if (error) {
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    const file = files[0];
    setSelectedFile(file);
    const nextRole = defaultAssetRole(file.type, scopeType);
    if (roleManuallySelected && isAssetRoleCompatible(selectedRole, file.type)) {
      setRoleNotice(null);
    } else {
      setSelectedRole(nextRole);
      setRoleManuallySelected(false);
      setRoleNotice(roleManuallySelected
        ? t(`Vai trò ${selectedRole} không tương thích với tệp mới; đã đặt lại thành ${nextRole}.`, `Role ${selectedRole} is incompatible with the new file; reset to ${nextRole}.`)
        : null);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) selectFiles(e.target.files);
  };

  const handleStartUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedFile || uploadBusy || validation) return;
    const fileError = validateFiles([selectedFile]);
    if (fileError) { setUploadError(fileError); return; }
    if (!isAssetRoleCompatible(selectedRole, selectedFile.type)) {
      setUploadError(t(`Vai trò ${selectedRole} không tương thích với loại tệp ${selectedFile.type}.`, `Role ${selectedRole} is incompatible with file type ${selectedFile.type}.`));
      return;
    }
    setValidation(null);

    if (scopeType === "project" && !selectedProjectId) {
      setUploadError(t("Vui lòng chọn dự án cho tài nguyên.", "Please select a project for the asset."));
      return;
    }
    if (scopeType === "product" && !selectedProductId) {
      setUploadError(t("Vui lòng chọn sản phẩm cho tài nguyên.", "Please select a product for the asset."));
      return;
    }

    setUploadError(null);
    setUploadPhase("getting_url");

    try {
      const payload = {
        filename: selectedFile.name,
        content_type: selectedFile.type,
        size_bytes: selectedFile.size,
        role: selectedRole,
        project_id: scopeType === "project" ? selectedProjectId : null,
        product_id: scopeType === "product" ? selectedProductId : null,
      };
      const key = uploadAction.getKey({
        filename: payload.filename,
        size: payload.size_bytes,
        contentType: payload.content_type,
        lastModified: selectedFile.lastModified,
        role: payload.role,
        project_id: payload.project_id,
        product_id: payload.product_id,
      });

      // 1. Create upload intent with boundary idempotency key
      const uploadDTO = await createUploadUrl(payload, key);

      if (!mounted.current) return;
      // A replay still verifies READY through GET rather than trusting the upload flag.
      if (uploadDTO.upload?.completed) {
        beginValidation(await getAsset(uploadDTO.asset_id));
        return;
      }
      setUploadPhase("uploading_minio");
      await uploadBytesToStorage(uploadDTO.upload, selectedFile);
      if (!mounted.current) return;
      setUploadPhase("validating");
      beginValidation(await completeAsset(uploadDTO.asset_id, {}));
    } catch (err) {
      if (!mounted.current) return;
      setUploadPhase("error");
      setUploadError(getErrorMessage(err, t("Quá trình tải lên thất bại.", "Upload failed.")));
    }
  };

  const handleDownload = async (assetId: string) => {
    if (downloadInFlight.current) return;
    const tab = reservePresignedDownloadTab();
    if (!tab) {
      setDownloadError(t("Trình duyệt đã chặn tab tải xuống. Cho phép mở tab rồi thử lại.", "The browser blocked the download tab. Allow pop-ups and retry."));
      return;
    }
    downloadInFlight.current = true;
    setDownloadingId(assetId);
    setDownloadError(null);
    try {
      const download = await downloadAsset(assetId);
      navigatePresignedDownload(tab, download.url);
    } catch (err) {
      closePresignedDownloadTab(tab);
      setDownloadError(getErrorMessage(err, t("Không thể tạo liên kết tải tài nguyên", "Unable to create asset download link")));
    } finally {
      downloadInFlight.current = false;
      setDownloadingId(null);
    }
  };

  const changeScope = (nextScope: "project" | "product") => {
    setScopeType(nextScope);
    if (nextScope === "project") setSelectedProductId("");
    else setSelectedProjectId("");
    if (selectedFile && !roleManuallySelected) {
      setSelectedRole(defaultAssetRole(selectedFile.type, nextScope));
      setRoleNotice(null);
    }
  };

  return (
    <div className="asset-upload-page space-y-8">
      <PageHeader
        eyebrow={t("Tài nguyên workflow AI", "AI workflow assets")}
        title={t("Tải lên tài nguyên", "Upload assets")}
        description={t("Tải lên hình ảnh sản phẩm, video tham chiếu và nhạc nền vào MinIO qua URL ký sẵn.", "Upload product images, reference videos, and background audio to MinIO using presigned URLs.")}
      />

      {uploadError && (
        <Alert variant="destructive" title={t("Lỗi tải lên", "Upload error")}>
          {uploadError}
        </Alert>
      )}

      {!policy && <Alert title={t("Cấu hình tải lên chưa sẵn sàng", "Upload settings are not ready")} variant={policyQuery.isError ? "destructive" : "info"}>
        <p>{policyQuery.isError
          ? t(`Không thể tải cấu hình: ${getErrorMessage(policyQuery.error)}. Vui lòng thử lại trước khi chọn tệp hoặc tải lên.`, `Unable to load settings: ${getErrorMessage(policyQuery.error)}. Please retry before selecting a file or uploading.`)
          : t("Đang tải cấu hình tải lên. Vui lòng chờ trước khi chọn tệp hoặc tải lên.", "Loading upload settings. Please wait before selecting a file or uploading.")}</p>
        <Button type="button" variant="secondary" disabled={policyQuery.isFetching} onClick={() => { void policyQuery.refetch(); }}>{t("Tải lại cấu hình", "Reload settings")}</Button>
      </Alert>}
      {(uploadPhase === "validating" || uploadPhase === "checking") && <p role="status" aria-live="polite">{t("Đang kiểm tra tài nguyên…", "Validating asset…")}</p>}
      {uploadPhase === "error" && validation && <Button type="button" onClick={retryValidation}>
        {validatedAsset?.status === "FAILED" ? t("Thử xác thực lại", "Retry validation") : t("Kiểm tra lại trạng thái", "Check status again")}
      </Button>}

      {uploadPhase === "done" && (
        <Alert variant="success" title={t("Hoàn thành", "Complete")}>
          {t("Tài nguyên đã tải lên thành công và sẵn sàng sử dụng.", "The asset was uploaded and is now ready.")}
        </Alert>
      )}

      <div className="upload-layout grid gap-8">
        {/* Upload Form */}
        <div className="min-w-0 space-y-6">
          <form onSubmit={handleStartUpload} className="form-card">
            <fieldset disabled={uploadBusy} className="space-y-6">
            <h2>{t("Thiết lập tải lên", "Upload settings")}</h2>

            {/* Drag and Drop Zone */}
            <div
              role="button"
              tabIndex={uploadBusy ? -1 : 0}
              aria-label={t("Chọn hoặc kéo thả một tệp tài nguyên", "Select or drop one asset file")}
              aria-disabled={uploadBusy}
              onClick={() => { if (!uploadBusy) fileInputRef.current?.click(); }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (!uploadBusy) fileInputRef.current?.click(); }
              }}
              onDragEnter={(e) => { e.preventDefault(); if (!uploadBusy) setDragActive(true); }}
              onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = uploadBusy ? "none" : "copy"; if (!uploadBusy) setDragActive(true); }}
              onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragActive(false); }}
              onDrop={(e) => { e.preventDefault(); setDragActive(false); selectFiles(e.dataTransfer.files); }}
              className={`upload-zone flex flex-col items-center justify-center p-8 text-center cursor-pointer transition-all ${
                (selectedFile || dragActive) ? "border-blue-500 bg-blue-500/10" : ""
              }`}
            >
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                onClick={(e) => e.stopPropagation()}
                aria-label={t("Tệp tài nguyên", "Asset file")}
                onChange={handleFileChange}
                accept={policy?.allowed_content_types.join(",")}
                disabled={uploadBusy}
              />
              <div className="w-12 h-12 rounded-md bg-blue-600/20 border border-blue-500/40 flex items-center justify-center text-blue-400 mb-3">
                <UploadCloud className="w-6 h-6" />
              </div>
              {selectedFile && !dragActive ? (
                <div className="space-y-1">
                  <p className="font-semibold text-sm text-[#f1f3f5]">{selectedFile.name}</p>
                  <p className="text-xs text-[#9ea5b0]">
                    {(selectedFile.size / (1024 * 1024)).toFixed(2)} MB · {selectedFile.type}
                  </p>
                  <span className="text-xs text-blue-400 font-semibold block pt-2">
                    {t("Nhấn để chọn tệp khác", "Click to select another file")}
                  </span>
                </div>
              ) : (
                <div className="space-y-1">
                  <p className="font-semibold text-sm text-[#f1f3f5]">{dragActive ? t("Thả tệp để chọn tài nguyên", "Drop a file to select it") : t("Kéo thả tệp vào đây hoặc nhấn để chọn", "Drop a file here or click to select")}</p>
                  <p className="text-xs text-[#9ea5b0]">
                    {policy ? t(`${policy.allowed_content_types.join(", ")} (Tối đa ${(policy.max_upload_bytes / 1024 ** 2).toFixed(0)} MiB)`, `${policy.allowed_content_types.join(", ")} (Maximum ${(policy.max_upload_bytes / 1024 ** 2).toFixed(0)} MiB)`) : t("Chưa tải được cấu hình tải lên. Vui lòng thử lại.", "Upload settings are unavailable. Please retry.")}
                  </p>
                </div>
              )}
            </div>

            {/* Scope Selection */}
            <div className="responsive-field-grid grid gap-4">
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-[#9ea5b0] uppercase tracking-wider">
                  {t("Phạm vi tài nguyên:", "Asset scope:")}
                </label>
                <div className="upload-scope-buttons flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => changeScope("project")}
                    className={`flex-1 py-2 rounded-md text-xs font-semibold border transition-colors ${
                      scopeType === "project"
                        ? "bg-blue-600 text-white border-blue-500 "
                        : "bg-[#0b101a] text-[#9ea5b0] border-[#2c3038]"
                    }`}
                  >
                    {t("Thuộc dự án", "Project asset")}
                  </button>
                  <button
                    type="button"
                    onClick={() => changeScope("product")}
                    className={`flex-1 py-2 rounded-md text-xs font-semibold border transition-colors ${
                      scopeType === "product"
                        ? "bg-blue-600 text-white border-blue-500 "
                        : "bg-[#0b101a] text-[#9ea5b0] border-[#2c3038]"
                    }`}
                  >
                    {t("Thuộc sản phẩm", "Product asset")}
                  </button>
                </div>
              </div>

              {scopeType === "project" ? (
                <div className="min-w-0 space-y-2">
                  <Select
                    id="project_select"
                    label={t("Chọn dự án", "Select project")}
                    value={selectedProjectId}
                    disabled={projectsQuery.isPending || projectsQuery.isError}
                    onChange={(e) => setSelectedProjectId(e.target.value)}
                  >
                    <option value="">{t("-- Chọn dự án --", "-- Select project --")}</option>
                    {projectOptions.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </Select>
                  {projectsQuery.isPending && <p role="status" className="text-xs text-[#9ea5b0]">{t("Đang tải dự án…", "Loading projects…")}</p>}
                  {projectsQuery.isError && <Alert variant="destructive" title={t("Không thể tải dự án", "Unable to load projects")}>
                    <p>{getErrorMessage(projectsQuery.error)}</p>
                    <Button type="button" variant="secondary" disabled={projectsQuery.isFetching} onClick={() => { void projectsQuery.refetch(); }}>{t("Thử lại", "Retry")}</Button>
                  </Alert>}
                  {projectsQuery.isFetchNextPageError && <Alert variant="destructive" title={t("Không thể tải thêm dự án", "Unable to load more projects")}>
                    <p>{getErrorMessage(projectsQuery.error)}</p>
                  </Alert>}
                  {projectsQuery.hasNextPage && <Button type="button" variant="outline" disabled={projectsQuery.isFetchingNextPage} onClick={() => { void projectsQuery.fetchNextPage(); }}>
                    {projectsQuery.isFetchingNextPage ? t("Đang tải…", "Loading…") : projectsQuery.isFetchNextPageError ? t("Thử tải lại dự án", "Retry loading projects") : t("Tải thêm dự án", "Load more projects")}
                  </Button>}
                </div>
              ) : (
                <div className="min-w-0 space-y-2">
                  <Select
                    id="product_select"
                    label={t("Chọn sản phẩm", "Select product")}
                    value={selectedProductId}
                    disabled={productsQuery.isPending || productsQuery.isError}
                    onChange={(e) => setSelectedProductId(e.target.value)}
                  >
                    <option value="">{t("-- Chọn sản phẩm --", "-- Select product --")}</option>
                    {productOptions.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </Select>
                  {productsQuery.isPending && <p role="status" className="text-xs text-[#9ea5b0]">{t("Đang tải sản phẩm…", "Loading products…")}</p>}
                  {productsQuery.isError && <Alert variant="destructive" title={t("Không thể tải sản phẩm", "Unable to load products")}>
                    <p>{getErrorMessage(productsQuery.error)}</p>
                    <Button type="button" variant="secondary" disabled={productsQuery.isFetching} onClick={() => { void productsQuery.refetch(); }}>{t("Thử lại", "Retry")}</Button>
                  </Alert>}
                  {productsQuery.isFetchNextPageError && <Alert variant="destructive" title={t("Không thể tải thêm sản phẩm", "Unable to load more products")}>
                    <p>{getErrorMessage(productsQuery.error)}</p>
                  </Alert>}
                  {productsQuery.hasNextPage && <Button type="button" variant="outline" disabled={productsQuery.isFetchingNextPage} onClick={() => { void productsQuery.fetchNextPage(); }}>
                    {productsQuery.isFetchingNextPage ? t("Đang tải…", "Loading…") : productsQuery.isFetchNextPageError ? t("Thử tải lại sản phẩm", "Retry loading products") : t("Tải thêm sản phẩm", "Load more products")}
                  </Button>}
                </div>
              )}
            </div>

            {/* Role Selection */}
            <div className="space-y-1.5">
              <label htmlFor="asset_role" className="text-xs font-semibold text-[#9ea5b0] uppercase tracking-wider">
                {t("Vai trò trong workflow AI:", "Role in AI workflow:")}
              </label>
              <select
                id="asset_role"
                value={selectedRole}
                onChange={(e) => {
                  setSelectedRole(e.target.value as AssetRole);
                  setRoleManuallySelected(true);
                  setRoleNotice(null);
                }}
                className="w-full rounded-md border border-[#2c3038] bg-[#0b101a] text-sm text-[#f1f3f5] px-3.5 py-2.5 focus:outline-none focus:border-blue-500"
              >
                <option value="PRODUCT_IMAGE" disabled={!!selectedFile && !isAssetRoleCompatible("PRODUCT_IMAGE", selectedFile.type)}>{t("Ảnh sản phẩm chính (I2V)", "Main product image (I2V)")}</option>
                <option value="PROJECT_REFERENCE" disabled={!!selectedFile && !isAssetRoleCompatible("PROJECT_REFERENCE", selectedFile.type)}>{t("Ảnh tham chiếu phong cách", "Style reference image")}</option>
                <option value="SOURCE_VIDEO" disabled={!!selectedFile && !isAssetRoleCompatible("SOURCE_VIDEO", selectedFile.type)}>{t("Video nguồn", "Source video")}</option>
                <option value="REFERENCE_VIDEO" disabled={!!selectedFile && !isAssetRoleCompatible("REFERENCE_VIDEO", selectedFile.type)}>{t("Video tham chiếu chuyển động (Ref2V)", "Motion reference video (Ref2V)")}</option>
                <option value="REFERENCE_AUDIO" disabled={!!selectedFile && !isAssetRoleCompatible("REFERENCE_AUDIO", selectedFile.type)}>{t("Âm thanh tham chiếu", "Reference audio")}</option>
                <option value="BACKGROUND_AUDIO" disabled={!!selectedFile && !isAssetRoleCompatible("BACKGROUND_AUDIO", selectedFile.type)}>{t("Nhạc nền khi ghép video", "Background audio for assembly")}</option>
              </select>
              {roleNotice && <p className="text-xs text-amber-300" role="status">{roleNotice}</p>}
            </div>

            <Button
              type="submit"
              variant="primary"
              disabled={!policy || !selectedFile || uploadBusy || !!validation}
              isLoading={uploadBusy}
            >
              {uploadPhase === "getting_url"
                ? t("Đang khởi tạo tải lên...", "Preparing upload...")
                : uploadPhase === "uploading_minio"
                ? t("Đang tải dữ liệu lên MinIO...", "Uploading to MinIO...")
                : uploadPhase === "validating" || uploadPhase === "checking"
                ? t("Đang kiểm tra tài nguyên…", "Validating asset…")
                : t("Bắt đầu tải lên", "Start upload")}
            </Button>
            </fieldset>
          </form>
        </div>

        {/* Existing Assets Panel */}
        <div className="min-w-0 space-y-4">
          <div className="table-panel space-y-4">
            <h2>{t("Tài nguyên vừa tải lên gần đây", "Recently uploaded assets")}{assetsData ? ` (${assetsData.total})` : ""}</h2>

            {downloadError && <Alert variant="destructive" title={t("Không thể tải tài nguyên", "Unable to download asset")}>{downloadError}</Alert>}

            {assetsLoading ? (
              <div className="space-y-3">
                <Skeleton className="h-14" />
                <Skeleton className="h-14" />
                <Skeleton className="h-14" />
              </div>
            ) : !assetsData ? (
              <QueryErrorNotice
                title={t("Không thể tải danh sách tài nguyên", "Unable to load assets")}
                detail={t("Không có số liệu xác thực để hiển thị.", "No verified data is available to display.")}
                onRetry={refetchAssets}
                isRetrying={assetsFetching}
              />
            ) : (
              <>
              {assetsError && (
                <QueryErrorNotice
                  title={t("Danh sách tài nguyên chưa được cập nhật", "Asset list could not be refreshed")}
                  detail={t("Đang hiển thị dữ liệu đã tải trước đó.", "Showing previously loaded data.")}
                  onRetry={refetchAssets}
                  isRetrying={assetsFetching}
                />
              )}
              {!assetsData.items.length ? (
                <EmptyState
                  title={t("Chưa có tài nguyên nào", "No assets yet")}
                  detail={t("Tải tệp đầu tiên qua biểu mẫu bên trái để lưu trữ trên MinIO.", "Upload your first file using the form on the left to store it in MinIO.")}
                />
              ) : (
              <div className="space-y-3">
                {assetsData.items.map((asset) => {
                  const isImage = asset.content_type.startsWith("image/");
                  const isVideo = asset.content_type.startsWith("video/");

                  return (
                    <div
                      key={asset.id}
                      className="upload-asset-row p-3.5 rounded-md border border-[#2c3038] bg-[#22252b]/60 flex items-center justify-between gap-3 hover:border-[#2a2e37] transition-colors"
                    >
                      <div className="upload-asset-info flex items-center gap-3 min-w-0">
                        <div className="w-9 h-9 rounded-lg bg-[#0b101a] border border-[#2c3038] flex items-center justify-center text-blue-400 shrink-0">
                          {isImage ? (
                            <FileImage className="w-4 h-4" />
                          ) : isVideo ? (
                            <FileVideo className="w-4 h-4" />
                          ) : (
                            <FileAudio className="w-4 h-4" />
                          )}
                        </div>
                        <div className="min-w-0">
                          <p className="font-semibold text-xs text-[#f1f3f5] truncate">
                            {asset.filename}
                          </p>
                          <p className="text-[11px] text-[#9ea5b0]">
                            {(asset.size_bytes / (1024 * 1024)).toFixed(2)} MB · {asset.role}
                          </p>
                        </div>
                      </div>

                      <div className="flex flex-wrap items-center gap-2 min-w-0">
                        <StatusPill status={asset.status} />
                        {asset.status === "READY" && (
                          <button
                            onClick={() => handleDownload(asset.id)}
                            disabled={downloadingId !== null}
                            title={t("Tải xuống", "Download")}
                            aria-label={downloadingId === asset.id ? t(`Đang tải ${asset.filename}`, `Downloading ${asset.filename}`) : t(`Tải xuống ${asset.filename}`, `Download ${asset.filename}`)}
                            className="p-1.5 rounded-lg text-[#9ea5b0] hover:text-white hover:bg-white/5 transition-colors"
                          >
                            <Download className="w-4 h-4" />
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
              )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
