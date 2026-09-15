"use client";

import React, { use, useState } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  UploadCloud,
  Edit2,
  Archive,
  ArrowLeft,
  RefreshCw,
} from "lucide-react";
import { getProduct, patchProduct, archiveProduct, getProductAssets } from "@/lib/api/products";
import { listBrands } from "@/lib/api/brands";
import { queryKeys } from "@/lib/query/query-keys";
import { getErrorMessage, isRevisionConflict } from "@/lib/api/errors";
import { PageHeader, StatusPill, EmptyState } from "@/components/page-kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select } from "@/components/ui/select";
import { Dialog } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert } from "@/components/ui/alert";

const editSchema = z.object({
  name: z.string().min(1, "Tên không được để trống").max(255),
  description: z.string().max(20000).default(""),
  brand_id: z.string().optional().nullable(),
  tone: z.string().optional(),
});

type EditFormValues = z.infer<typeof editSchema>;

function contextString(context: Record<string, unknown> | undefined, key: string): string {
  const value = context?.[key];
  return typeof value === "string" ? value : "";
}

export default function ProductDetailPage({
  params,
}: {
  params: Promise<{ productId: string }>;
}) {
  const { productId } = use(params);
  const queryClient = useQueryClient();
  const [isEditOpen, setIsEditOpen] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    data: product,
    isLoading: productLoading,
    error: productError,
    refetch,
  } = useQuery({
    queryKey: queryKeys.products.detail(productId),
    queryFn: () => getProduct(productId),
  });

  const { data: assetsData, isLoading: assetsLoading } = useQuery({
    queryKey: queryKeys.products.assets(productId),
    queryFn: () => getProductAssets(productId),
  });

  const { data: brandsData } = useQuery({
    queryKey: queryKeys.brands.all,
    queryFn: () => listBrands({ size: 100 }),
  });

  const {
    register,
    handleSubmit,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<EditFormValues>({
    resolver: zodResolver(editSchema),
  });

  const openEdit = () => {
    if (product) {
      setValue("name", product.name);
      setValue("description", product.description || "");
      setValue("brand_id", product.brand_id || "");
      setValue("tone", contextString(product.context, "tone"));
      setServerError(null);
      setIsEditOpen(true);
    }
  };

  const editMutation = useMutation({
    mutationFn: (data: EditFormValues) => {
      if (!product) throw new Error("Product not loaded");
      return patchProduct(
        productId,
        {
          name: data.name,
          description: data.description,
          brand_id: data.brand_id ? data.brand_id : null,
          context: data.tone ? { tone: data.tone } : {},
        },
        product.revision
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.products.detail(productId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.products.all });
      setIsEditOpen(false);
    },
    onError: (err) => setServerError(getErrorMessage(err)),
  });

  const archiveMutation = useMutation({
    mutationFn: () => {
      if (!product) throw new Error("Product not loaded");
      return archiveProduct(productId, product.revision);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.products.detail(productId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.products.all });
    },
    onError: (err) => setServerError(getErrorMessage(err)),
  });

  if (productLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-48" />
        <Skeleton className="h-40" />
      </div>
    );
  }

  if (productError || !product) {
    return (
      <div className="space-y-4">
        <Alert variant="destructive" title="Không tìm thấy sản phẩm">
          {getErrorMessage(productError, "Sản phẩm không tồn tại hoặc đã bị xóa.")}
        </Alert>
        <Link href="/products" className="secondary-action inline-flex items-center gap-2">
          <ArrowLeft className="w-4 h-4" />
          <span>Quay lại danh sách sản phẩm</span>
        </Link>
      </div>
    );
  }

  const brand = brandsData?.items.find((b) => b.id === product.brand_id);
  const productTone = contextString(product.context, "tone");

  return (
    <div className="space-y-8">
      <div>
        <Link
          href="/products"
          className="text-xs text-[#9ea5b0] hover:text-[#f1f3f5] inline-flex items-center gap-1.5 mb-4"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>Tất cả sản phẩm</span>
        </Link>

        <PageHeader
          eyebrow={`Sản phẩm master · Rev #${product.revision} ${brand ? `· ${brand.name}` : ""}`}
          title={product.name}
          description={productTone || product.description || "Chưa có mô tả phong cách."}
        >
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={openEdit}>
              <Edit2 className="w-4 h-4" />
              <span>Chỉnh sửa</span>
            </Button>
            {!product.archived && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  if (confirm("Bạn có chắc muốn lưu trữ sản phẩm này?")) {
                    archiveMutation.mutate();
                  }
                }}
                isLoading={archiveMutation.isPending}
              >
                <Archive className="w-4 h-4" />
                <span>Lưu trữ</span>
              </Button>
            )}
            <Link href="/assets/upload" className="primary-action text-xs flex items-center gap-1.5">
              <UploadCloud className="w-4 h-4" />
              <span>Upload asset</span>
            </Link>
          </div>
        </PageHeader>
      </div>

      {serverError && (
        <Alert variant="destructive" title="Lỗi">
          <div className="flex items-center justify-between gap-4">
            <span>{serverError}</span>
            {isRevisionConflict(serverError) && (
              <Button size="sm" variant="secondary" onClick={() => refetch()}>
                <RefreshCw className="w-3.5 h-3.5" />
                <span>Tải lại</span>
              </Button>
            )}
          </div>
        </Alert>
      )}

      {/* Detail Layout */}
      <section className="grid content-grid">
        <div className="table-panel space-y-4">
          <div className="flex items-center justify-between">
            <h2>Tài nguyên hình ảnh &amp; Media ({assetsData?.total ?? 0})</h2>
            <Link href="/assets/upload" className="text-xs text-blue-400 hover:text-blue-300 font-medium">
              + Thêm ảnh sản phẩm
            </Link>
          </div>

          {assetsLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-16" />
              <Skeleton className="h-16" />
            </div>
          ) : !assetsData?.items.length ? (
            <EmptyState
              title="Chưa có asset cho sản phẩm này"
              detail="Upload hình ảnh sản phẩm góc chụp rõ, nền trong hoặc clean studio để đưa vào workflow AI (I2V, Ref2V)."
              action={{ label: "Tải asset lên", href: "/assets/upload" }}
            />
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Tên file</th>
                  <th>Vai trò</th>
                  <th>Kích thước</th>
                  <th>Trạng thái</th>
                </tr>
              </thead>
              <tbody>
                {assetsData.items.map((asset) => (
                  <tr key={asset.id}>
                    <td className="font-semibold text-sm">{asset.filename}</td>
                    <td className="text-xs text-blue-400 font-semibold">{asset.role}</td>
                    <td className="text-xs text-[#9ea5b0]">
                      {(asset.size_bytes / (1024 * 1024)).toFixed(2)} MB
                    </td>
                    <td>
                      <StatusPill status={asset.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Visual Identity & Specs Panel */}
        <aside className="preview-panel space-y-4">
          <h2>Visual Identity &amp; Guidelines</h2>
          <div className="space-y-3 text-xs">
            <div>
              <span className="text-[#9ea5b0] block font-semibold uppercase tracking-wider mb-1">
                Visual Tone / Mood:
              </span>
              <p className="text-[#f1f3f5] bg-[#0b101a] p-3 rounded-lg border border-[#2c3038]">
                {productTone || "Chưa thiết lập"}
              </p>
            </div>

            <div>
              <span className="text-[#9ea5b0] block font-semibold uppercase tracking-wider mb-1">
                Mô tả chi tiết:
              </span>
              <p className="text-[#c3c6d7] leading-relaxed">
                {product.description || "Chưa có mô tả chi tiết."}
              </p>
            </div>

            <div className="pt-2 border-t border-[#2c3038]">
              <span className="text-[#9ea5b0] block font-semibold uppercase tracking-wider mb-1">
                Thương hiệu (Brand):
              </span>
              <p className="text-[#f1f3f5] font-semibold">{brand?.name || "Không thuộc thương hiệu"}</p>
            </div>
          </div>
        </aside>
      </section>

      {/* Edit Modal */}
      <Dialog
        isOpen={isEditOpen}
        onClose={() => setIsEditOpen(false)}
        title="Chỉnh sửa sản phẩm"
        description={`Cập nhật thông tin và visual tone (Revision #${product.revision})`}
      >
        <form onSubmit={handleSubmit((data) => editMutation.mutateAsync(data))} className="space-y-4">
          <Input id="name" label="Tên sản phẩm" error={errors.name?.message} {...register("name")} />

          <Select id="brand_id" label="Thương hiệu" {...register("brand_id")}>
            <option value="">-- Không thuộc brand nào --</option>
            {brandsData?.items.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </Select>

          <Input
            id="tone"
            label="Visual tone & Mood"
            placeholder="Ví dụ: High contrast, dark studio rim light"
            {...register("tone")}
          />

          <Textarea id="description" label="Mô tả sản phẩm" {...register("description")} />

          <div className="flex justify-end gap-3 pt-4 border-t border-[#2c3038]">
            <Button type="button" variant="outline" onClick={() => setIsEditOpen(false)}>
              Hủy
            </Button>
            <Button type="submit" variant="primary" isLoading={isSubmitting}>
              Lưu thay đổi
            </Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
