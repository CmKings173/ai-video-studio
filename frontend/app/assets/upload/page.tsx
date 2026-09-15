"use client";

import React, { useState, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  UploadCloud,
  FileImage,
  FileVideo,
  FileAudio,
  Download,
} from "lucide-react";
import { listAssets, createUploadUrl, uploadBytesToStorage, completeAsset, downloadAsset } from "@/lib/api/assets";
import { listProjects } from "@/lib/api/projects";
import { listProducts } from "@/lib/api/products";
import { queryKeys } from "@/lib/query/query-keys";
import { getErrorMessage } from "@/lib/api/errors";
import { useIdempotentAction } from "@/lib/hooks/use-idempotent-action";
import { PageHeader, StatusPill, EmptyState } from "@/components/page-kit";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Alert } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import type { AssetRole } from "@/lib/api/types";

export default function AssetUploadPage() {
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
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [scopeType, setScopeType] = useState<"project" | "product">("project");
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [selectedProductId, setSelectedProductId] = useState("");
  const [selectedRole, setSelectedRole] = useState<AssetRole>("PROJECT_REFERENCE");

  // Upload progress state
  const [uploadPhase, setUploadPhase] = useState<"idle" | "getting_url" | "uploading_minio" | "validating" | "done" | "error">("idle");
  const [uploadError, setUploadError] = useState<string | null>(null);

  // Load projects & products
  const { data: projectsData } = useQuery({
    queryKey: queryKeys.projects.all,
    queryFn: () => listProjects({ size: 100, archived: false }),
  });

  const { data: productsData } = useQuery({
    queryKey: queryKeys.products.all,
    queryFn: () => listProducts({ size: 100, archived: false }),
  });

  // Load existing assets
  const { data: assetsData, isLoading: assetsLoading } = useQuery({
    queryKey: queryKeys.assets.list({ size: 10 }),
    queryFn: () => listAssets({ size: 10 }),
  });

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      setSelectedFile(file);

      // Guess role based on mime type
      if (file.type.startsWith("image/")) {
        setSelectedRole(scopeType === "product" ? "PRODUCT_IMAGE" : "PROJECT_REFERENCE");
      } else if (file.type.startsWith("video/")) {
        setSelectedRole("REFERENCE_VIDEO");
      } else if (file.type.startsWith("audio/")) {
        setSelectedRole("BACKGROUND_AUDIO");
      }
    }
  };

  const handleStartUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedFile) return;

    if (scopeType === "project" && !selectedProjectId) {
      setUploadError("Vui lòng chọn Dự án cho asset.");
      return;
    }
    if (scopeType === "product" && !selectedProductId) {
      setUploadError("Vui lòng chọn Sản phẩm cho asset.");
      return;
    }

    setUploadError(null);
    setUploadPhase("getting_url");

    try {
      const payload = {
        filename: selectedFile.name,
        content_type: selectedFile.type || "application/octet-stream",
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

      // Replay check: if asset was already READY on server, skip MinIO upload and completion
      if (uploadDTO.upload?.completed) {
        setUploadPhase("done");
        setSelectedFile(null);
        if (fileInputRef.current) fileInputRef.current.value = "";
        uploadAction.reset();
        queryClient.invalidateQueries({ queryKey: queryKeys.assets.all });
        queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.summary });
        return;
      }

      // 2. Upload bytes to MinIO
      setUploadPhase("uploading_minio");
      await uploadBytesToStorage(uploadDTO.upload, selectedFile);

      // 3. Complete asset for backend validation
      setUploadPhase("validating");
      await completeAsset(uploadDTO.asset_id, {});

      setUploadPhase("done");
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      uploadAction.reset();

      queryClient.invalidateQueries({ queryKey: queryKeys.assets.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.summary });
    } catch (err) {
      setUploadPhase("error");
      setUploadError(getErrorMessage(err, "Quá trình upload thất bại."));
    }
  };

  const handleDownload = async (assetId: string) => {
    try {
      const download = await downloadAsset(assetId);
      window.open(download.url, "_blank");
    } catch (err) {
      alert(getErrorMessage(err, "Không thể tạo link tải asset"));
    }
  };

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Tài nguyên AI Workflow"
        title="Tải lên tài nguyên (Asset Upload)"
        description="Upload hình ảnh sản phẩm, reference video và background audio vào MinIO theo quy trình presigned URL."
      />

      {uploadError && (
        <Alert variant="destructive" title="Lỗi tải lên">
          {uploadError}
        </Alert>
      )}

      {uploadPhase === "done" && (
        <Alert variant="success" title="Hoàn thành">
          Asset đã được upload và chuyển sang trạng thái READY thành công.
        </Alert>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
        {/* Upload Form */}
        <div className="lg:col-span-6 space-y-6">
          <form onSubmit={handleStartUpload} className="form-card">
            <h2>Thiết lập Upload</h2>

            {/* Drag and Drop Zone */}
            <div
              onClick={() => fileInputRef.current?.click()}
              className={`upload-zone flex flex-col items-center justify-center p-8 text-center cursor-pointer transition-all ${
                selectedFile ? "border-blue-500 bg-blue-500/10" : ""
              }`}
            >
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                onChange={handleFileChange}
                accept="image/png,image/jpeg,image/webp,video/mp4,video/webm,audio/wav,audio/mpeg,audio/mp4,audio/ogg"
              />
              <div className="w-12 h-12 rounded-md bg-blue-600/20 border border-blue-500/40 flex items-center justify-center text-blue-400 mb-3">
                <UploadCloud className="w-6 h-6" />
              </div>
              {selectedFile ? (
                <div className="space-y-1">
                  <p className="font-semibold text-sm text-[#f1f3f5]">{selectedFile.name}</p>
                  <p className="text-xs text-[#9ea5b0]">
                    {(selectedFile.size / (1024 * 1024)).toFixed(2)} MB · {selectedFile.type}
                  </p>
                  <span className="text-xs text-blue-400 font-semibold block pt-2">
                    Nhấn để chọn file khác
                  </span>
                </div>
              ) : (
                <div className="space-y-1">
                  <p className="font-semibold text-sm text-[#f1f3f5]">Kéo thả file vào đây hoặc nhấn để chọn</p>
                  <p className="text-xs text-[#9ea5b0]">
                    PNG, JPEG, WEBP, MP4, WEBM, WAV, MP3 (Tối đa 512MB)
                  </p>
                </div>
              )}
            </div>

            {/* Scope Selection */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-[#9ea5b0] uppercase tracking-wider">
                  Phạm vi tài nguyên:
                </label>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setScopeType("project");
                      setSelectedProductId("");
                    }}
                    className={`flex-1 py-2 rounded-md text-xs font-semibold border transition-colors ${
                      scopeType === "project"
                        ? "bg-blue-600 text-white border-blue-500 "
                        : "bg-[#0b101a] text-[#9ea5b0] border-[#2c3038]"
                    }`}
                  >
                    Thuộc Dự án
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setScopeType("product");
                      setSelectedProjectId("");
                    }}
                    className={`flex-1 py-2 rounded-md text-xs font-semibold border transition-colors ${
                      scopeType === "product"
                        ? "bg-blue-600 text-white border-blue-500 "
                        : "bg-[#0b101a] text-[#9ea5b0] border-[#2c3038]"
                    }`}
                  >
                    Thuộc Sản phẩm
                  </button>
                </div>
              </div>

              {scopeType === "project" ? (
                <Select
                  id="project_select"
                  label="Chọn Dự án"
                  value={selectedProjectId}
                  onChange={(e) => setSelectedProjectId(e.target.value)}
                >
                  <option value="">-- Chọn dự án --</option>
                  {projectsData?.items.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </Select>
              ) : (
                <Select
                  id="product_select"
                  label="Chọn Sản phẩm"
                  value={selectedProductId}
                  onChange={(e) => setSelectedProductId(e.target.value)}
                >
                  <option value="">-- Chọn sản phẩm --</option>
                  {productsData?.items.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </Select>
              )}
            </div>

            {/* Role Selection */}
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-[#9ea5b0] uppercase tracking-wider">
                Vai trò (Role trong AI Workflow):
              </label>
              <select
                value={selectedRole}
                onChange={(e) => setSelectedRole(e.target.value as AssetRole)}
                className="w-full rounded-md border border-[#2c3038] bg-[#0b101a] text-sm text-[#f1f3f5] px-3.5 py-2.5 focus:outline-none focus:border-blue-500"
              >
                <option value="PRODUCT_IMAGE">PRODUCT_IMAGE (Ảnh sản phẩm chính, I2V)</option>
                <option value="PROJECT_REFERENCE">PROJECT_REFERENCE (Ảnh tham chiếu phong cách)</option>
                <option value="REFERENCE_VIDEO">REFERENCE_VIDEO (Video mẫu chuyển động, Ref2V)</option>
                <option value="REFERENCE_AUDIO">REFERENCE_AUDIO (Âm thanh tham chiếu)</option>
                <option value="BACKGROUND_AUDIO">BACKGROUND_AUDIO (Nhạc nền xuất bản Assembly)</option>
              </select>
            </div>

            <Button
              type="submit"
              variant="primary"
              disabled={!selectedFile || uploadPhase === "getting_url" || uploadPhase === "uploading_minio" || uploadPhase === "validating"}
              isLoading={uploadPhase === "getting_url" || uploadPhase === "uploading_minio" || uploadPhase === "validating"}
            >
              {uploadPhase === "getting_url"
                ? "Khởi tạo Upload Intent..."
                : uploadPhase === "uploading_minio"
                ? "Đang đẩy dữ liệu lên MinIO..."
                : uploadPhase === "validating"
                ? "Đang xác thực Asset..."
                : "Bắt đầu Upload"}
            </Button>
          </form>
        </div>

        {/* Existing Assets Panel */}
        <div className="lg:col-span-6 space-y-4">
          <div className="table-panel space-y-4">
            <h2>Tài nguyên vừa tải lên gần đây ({assetsData?.total ?? 0})</h2>

            {assetsLoading ? (
              <div className="space-y-3">
                <Skeleton className="h-14" />
                <Skeleton className="h-14" />
                <Skeleton className="h-14" />
              </div>
            ) : !assetsData?.items.length ? (
              <EmptyState
                title="Chưa có asset nào"
                detail="Tải file đầu tiên qua khung bên trái để lưu trữ trên MinIO."
              />
            ) : (
              <div className="space-y-3">
                {assetsData.items.map((asset) => {
                  const isImage = asset.content_type.startsWith("image/");
                  const isVideo = asset.content_type.startsWith("video/");

                  return (
                    <div
                      key={asset.id}
                      className="p-3.5 rounded-md border border-[#2c3038] bg-[#22252b]/60 flex items-center justify-between gap-3 hover:border-[#2a2e37] transition-colors"
                    >
                      <div className="flex items-center gap-3 min-w-0">
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

                      <div className="flex items-center gap-2 shrink-0">
                        <StatusPill status={asset.status} />
                        {asset.status === "READY" && (
                          <button
                            onClick={() => handleDownload(asset.id)}
                            title="Tải xuống"
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
          </div>
        </div>
      </div>
    </div>
  );
}
