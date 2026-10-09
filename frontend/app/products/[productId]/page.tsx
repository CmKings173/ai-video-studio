"use client";

import { useI18n } from "@/lib/i18n";
import React, { use, useState } from "react";
import Link from "next/link";
import { useInfiniteQuery, useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  UploadCloud,
  Edit2,
  Archive,
  ArrowLeft,
} from "lucide-react";
import { getProduct, patchProduct, archiveProduct, getProductAssets } from "@/lib/api/products";
import { getBrand, listBrands } from "@/lib/api/brands";
import { queryKeys } from "@/lib/query/query-keys";
import { flattenPageItems, nextPageParam } from "@/lib/api/pagination";
import { getErrorMessage } from "@/lib/api/errors";
import { PageHeader, StatusPill, EmptyState, QueryErrorNotice } from "@/components/page-kit";
import { PaginationControls } from "@/components/list-pagination";
import { RelatedResourceState } from "@/components/related-resource-state";
import { useRevisionRecovery } from "@/lib/hooks/use-revision-recovery";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select } from "@/components/ui/select";
import { Dialog } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert } from "@/components/ui/alert";

const editSchema = z.object({
  name: z.string().min(1, "required_name").max(255, "name_too_long"),
  description: z.string().max(20000, "description_too_long").default(""),
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
  useI18n();
  const { productId } = use(params);
  return <ProductDetailBody key={`detail-${productId}`} productId={productId} />;
}

function ProductDetailBody({ productId }: { productId: string }) {
  const { t } = useI18n();
  const validationMessage = (message?: string) => {
    const messages: Record<string, string> = {
      required_name: t("Tên không được để trống", "Name is required"),
      name_too_long: t("Tên tối đa 255 ký tự", "Name must be at most 255 characters"),
      description_too_long: t("Mô tả tối đa 20.000 ký tự", "Description must be at most 20,000 characters"),
    };
    return message ? messages[message] ?? t("Thông tin không hợp lệ", "Invalid value") : undefined;
  };
  const queryClient = useQueryClient();
  const [isEditOpen, setIsEditOpen] = useState(false);
  const [editRevision, setEditRevision] = useState<number | null>(null);
  const [assetsPage, setAssetsPage] = useState(1);
  const [brandSearch, setBrandSearch] = useState("");

  const {
    data: product,
    isLoading: productLoading,
    error: productError,
    refetch,
  } = useQuery({
    queryKey: queryKeys.products.detail(productId),
    queryFn: () => getProduct(productId),
  });

  const assetsQuery = useQuery({
    queryKey: queryKeys.products.assets(productId, { page: assetsPage, size: 20 }),
    queryFn: () => getProductAssets(productId, { page: assetsPage, size: 20 }),
  });
  const assetsData = assetsQuery.data;

  const brandsQuery = useInfiniteQuery({
    queryKey: queryKeys.brands.list({ size: 50 }),
    queryFn: ({ pageParam }) => listBrands({ page: pageParam, size: 50 }),
    initialPageParam: 1,
    getNextPageParam: nextPageParam,
  });
  const brandItems = flattenPageItems(brandsQuery.data?.pages);

  const {
    register,
    handleSubmit,
    reset,
    control,
    formState: { errors, isSubmitting, isDirty },
  } = useForm<EditFormValues>({
    resolver: zodResolver(editSchema),
  });
  const selectedBrandId = useWatch({ control, name: "brand_id" }) || "";
  const productBrandQuery = useQuery({
    queryKey: queryKeys.brands.detail(product?.brand_id ?? ""),
    queryFn: () => getBrand(product!.brand_id!),
    enabled: Boolean(product?.brand_id && !brandItems.some((brand) => brand.id === product.brand_id)),
  });
  const selectedBrandQuery = useQuery({
    queryKey: queryKeys.brands.detail(selectedBrandId),
    queryFn: () => getBrand(selectedBrandId),
    enabled: Boolean(selectedBrandId && !brandItems.some((brand) => brand.id === selectedBrandId)),
  });
  const matchingBrands = brandItems.filter((item) => item.name.toLocaleLowerCase().includes(brandSearch.trim().toLocaleLowerCase()));
  const visibleBrands: { id: string; name: string }[] = [...matchingBrands];
  const currentSelectedBrand = brandItems.find((item) => item.id === selectedBrandId)
    ?? selectedBrandQuery.data
    ?? (selectedBrandId ? { id: selectedBrandId, name: selectedBrandId } : null);
  if (currentSelectedBrand && !visibleBrands.some((item) => item.id === currentSelectedBrand.id)) visibleBrands.unshift(currentSelectedBrand);

  const recovery = useRevisionRecovery({
    refetch: refetch,
    dirty: isDirty,
    onRecovered: (current) => { setEditRevision(current.revision); reset({ name: current.name, description: current.description || "", brand_id: current.brand_id || "", tone: contextString(current.context, "tone") }); },
  });

  const openEdit = () => {
    if (isSubmitting || recovery.recovering) return;
    if (recovery.conflict) { setIsEditOpen(true); return; }
    if (product) {
      reset({ name: product.name, description: product.description || "", brand_id: product.brand_id || "", tone: contextString(product.context, "tone") });
      setEditRevision(product.revision);
      recovery.clear();
      setIsEditOpen(true);
    }
  };

  const editMutation = useMutation({
    mutationFn: (data: EditFormValues) => {
      if (recovery.blocked) throw new Error(t("Vui lòng tải lại dữ liệu trước khi thử lại.", "Please reload the data before retrying."));
      if (!product) throw new Error(t("Chưa tải được sản phẩm", "Product not loaded"));
      const context = { ...product.context };
      if (data.tone) context.tone = data.tone;
      else delete context.tone;
      return patchProduct(
        productId,
        {
          name: data.name,
          description: data.description,
          brand_id: data.brand_id ? data.brand_id : null,
          context,
        },
        editRevision ?? product.revision
      );
    },
    onSuccess: (saved) => {
      reset({ name: saved.name, description: saved.description || "", brand_id: saved.brand_id || "", tone: contextString(saved.context, "tone") });
      setEditRevision(saved.revision);
      recovery.clear();
      queryClient.invalidateQueries({ queryKey: queryKeys.products.detail(productId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.products.all });
      setIsEditOpen(false);
    },
    onError: (err) => recovery.fail(err),
  });

  const archiveMutation = useMutation({
    mutationFn: () => {
      if (recovery.blocked) throw new Error(t("Vui lòng tải lại dữ liệu trước khi thử lại.", "Please reload the data before retrying."));
      if (!product) throw new Error(t("Chưa tải được sản phẩm", "Product not loaded"));
      return archiveProduct(productId, product.revision);
    },
    onSuccess: () => {
      recovery.clear();
      queryClient.invalidateQueries({ queryKey: queryKeys.products.detail(productId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.products.all });
    },
    onError: (err) => recovery.fail(err),
  });

  if (productLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-48" />
        <Skeleton className="h-40" />
      </div>
    );
  }

  if (!product) {
    return (
      <div className="space-y-4">
        <Alert variant="destructive" title={t("Không tìm thấy sản phẩm", "Product not found")}>
          {getErrorMessage(productError, t("Sản phẩm không tồn tại hoặc đã bị xóa.", "The product does not exist or has been deleted."))}
        </Alert>
        <Link href="/products" className="secondary-action inline-flex flex-wrap items-center gap-2">
          <ArrowLeft className="w-4 h-4" />
          <span>{t("Quay lại danh sách sản phẩm", "Back to products")}</span>
        </Link>
      </div>
    );
  }

  const brand = brandItems.find((b) => b.id === product.brand_id) ?? productBrandQuery.data;
  const productTone = contextString(product.context, "tone");

  const mutationNotice = recovery.message && (
    <Alert variant="destructive" title={t("Thông báo", "Notice")}>
      <div className="space-y-3">
        <p>{recovery.message}</p>
        {recovery.reloadMessage && <p role="alert">{t(`Không tải được dữ liệu mới nhất: ${recovery.reloadMessage}. Bản nháp vẫn được giữ.`, `Unable to load the latest data: ${recovery.reloadMessage}. Your draft is preserved.`)}</p>}
        {recovery.conflict && <Button type="button" size="sm" variant="secondary" disabled={recovery.recovering} onClick={recovery.recover}>
          {recovery.recovering ? t("Đang tải lại...", "Reloading...") : t("Tải lại", "Reload")}
        </Button>}
      </div>
    </Alert>
  );

  return (
    <div className="space-y-8">
      <div>
        <Link
          href="/products"
          className="text-xs text-[#9ea5b0] hover:text-[#f1f3f5] inline-flex items-center gap-1.5 mb-4"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>{t("Tất cả sản phẩm", "All products")}</span>
        </Link>

        <PageHeader
          eyebrow={t(`Sản phẩm · Phiên bản #${product.revision} ${brand ? `· ${brand.name}` : ""}`, `Product · Revision #${product.revision} ${brand ? `· ${brand.name}` : ""}`)}
          title={product.name}
          description={productTone || product.description || t("Chưa có mô tả phong cách.", "No style description yet.")}
        >
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" size="sm" disabled={isSubmitting || recovery.recovering} onClick={openEdit}>
              <Edit2 className="w-4 h-4" />
              <span>{t("Chỉnh sửa", "Edit")}</span>
            </Button>
            {!product.archived && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  if (!recovery.blocked && confirm(t("Bạn có chắc muốn lưu trữ sản phẩm này?", "Are you sure you want to archive this product?"))) {
                    archiveMutation.mutate();
                  }
                }}
                disabled={recovery.blocked}
                isLoading={archiveMutation.isPending}
              >
                <Archive className="w-4 h-4" />
                <span>{t("Lưu trữ", "Archive")}</span>
              </Button>
            )}
            <Link href="/assets/upload" className="primary-action text-xs flex items-center gap-1.5">
              <UploadCloud className="w-4 h-4" />
              <span>{t("Tải tài nguyên lên", "Upload assets")}</span>
            </Link>
          </div>
        </PageHeader>
      </div>

      {!isEditOpen && mutationNotice}
      {productError && <QueryErrorNotice title={t("Không thể làm mới thông tin sản phẩm", "Unable to refresh product information")}
        detail={t(`Đang hiển thị dữ liệu đã lưu; dữ liệu này có thể đã cũ. ${getErrorMessage(productError)}`, `Showing saved data, which may be outdated. ${getErrorMessage(productError)}`)}
        onRetry={recovery.recover} isRetrying={recovery.recovering} />}

      {/* Detail Layout */}
      <section className="grid content-grid">
        <div className="table-panel space-y-4">
          <div className="flex min-w-0 flex-wrap items-center justify-between">
            <h2>{t("Hình ảnh & Tài nguyên (", "Images & media (")}{assetsQuery.error ? "?" : assetsData?.total ?? "…"})</h2>
            <Link href="/assets/upload" className="text-xs text-blue-400 hover:text-blue-300 font-medium">
              {t("+ Thêm ảnh sản phẩm", "+ Add product image")}
            </Link>
          </div>

          <RelatedResourceState query={assetsQuery} page={assetsPage} label={t("tài nguyên sản phẩm", "product assets")} empty={
            <EmptyState
              title={t("Chưa có tài nguyên cho sản phẩm này", "No assets for this product yet")}
              detail={t("Tải lên ảnh sản phẩm rõ nét, nền trong suốt hoặc nền phòng chụp đơn giản để đưa vào workflow AI (I2V, Ref2V).", "Upload clear product images with transparent or clean studio backgrounds for AI workflows (I2V, Ref2V).")}
              action={{ label: t("Tải tài nguyên lên", "Upload assets"), href: "/assets/upload" }}
            />
          }>
            <div className="table-scroll" role="region" aria-label={t("Tài nguyên sản phẩm", "Product assets")} tabIndex={0}>
              <table>
                <thead>
                  <tr>
                    <th>{t("Tên tệp", "Filename")}</th>
                    <th>{t("Vai trò", "Role")}</th>
                    <th>{t("Kích thước", "Size")}</th>
                    <th>{t("Trạng thái", "Status")}</th>
                  </tr>
                </thead>
                <tbody>
                  {assetsData?.items.map((asset) => (
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
            </div>
          </RelatedResourceState>
          <PaginationControls data={assetsQuery.error ? undefined : assetsData} page={assetsPage} onPageChange={setAssetsPage} busy={assetsQuery.isFetching} label={t("Phân trang tài nguyên sản phẩm", "Product asset pagination")} />
        </div>

        {/* Visual Identity & Specs Panel */}
        <aside className="preview-panel space-y-4">
          <h2>{t("Nhận diện & Hướng dẫn hình ảnh", "Visual identity & guidelines")}</h2>
          <div className="space-y-3 text-xs">
            <div>
              <span className="text-[#9ea5b0] block font-semibold uppercase tracking-wider mb-1">
                {t("Phong cách / Cảm xúc hình ảnh:", "Visual tone / mood:")}
              </span>
              <p className="text-[#f1f3f5] bg-[#0b101a] p-3 rounded-lg border border-[#2c3038]">
                {productTone || t("Chưa thiết lập", "Not set")}
              </p>
            </div>

            <div>
              <span className="text-[#9ea5b0] block font-semibold uppercase tracking-wider mb-1">
                {t("Mô tả chi tiết:", "Detailed description:")}
              </span>
              <p className="text-[#c3c6d7] leading-relaxed">
                {product.description || t("Chưa có mô tả chi tiết.", "No detailed description yet.")}
              </p>
            </div>

            <div className="pt-2 border-t border-[#2c3038]">
              <span className="text-[#9ea5b0] block font-semibold uppercase tracking-wider mb-1">
                {t("Thương hiệu:", "Brand:")}
              </span>
              <p className="text-[#f1f3f5] font-semibold">{brand?.name || t("Không thuộc thương hiệu", "No brand")}</p>
            </div>
          </div>
        </aside>
      </section>

      {/* Edit Modal */}
      <Dialog
        isOpen={isEditOpen}
        onClose={() => { if (!isSubmitting && !recovery.recovering) setIsEditOpen(false); }}
        title={t("Chỉnh sửa sản phẩm", "Edit product")}
        description={t(`Cập nhật thông tin và phong cách hình ảnh (Phiên bản #${editRevision ?? product.revision})`, `Update information and visual tone (Revision #${editRevision ?? product.revision})`)}
      >
        <form onSubmit={handleSubmit(async (data) => { if (!recovery.blocked) await editMutation.mutateAsync(data).catch(() => {}); })} className="space-y-4">
          {mutationNotice}
          <fieldset disabled={isSubmitting || recovery.recovering} className="space-y-4">
            <Input id="name" label={t("Tên sản phẩm", "Product name")} error={validationMessage(errors.name?.message)} {...register("name")} />

            <Input
              id="edit_brand_search"
              aria-label={t("Tìm thương hiệu đã tải", "Search loaded brands")}
              placeholder={t("Tìm thương hiệu đã tải...", "Search loaded brands...")}
              value={brandSearch}
              onChange={(event) => setBrandSearch(event.target.value)}
            />

            <Select id="brand_id" label={t("Thương hiệu", "Brand")} {...register("brand_id")}>
              <option value="">{t("-- Không thuộc thương hiệu nào --", "-- No brand --")}</option>
              {visibleBrands.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </Select>
            {brandsQuery.isLoading && <p role="status" className="text-xs text-[#9ea5b0]">{t("Đang tải thương hiệu...", "Loading brands...")}</p>}
            {brandsQuery.error && <div role="alert" className="text-xs text-red-400">{getErrorMessage(brandsQuery.error)} <button type="button" className="underline" onClick={() => brandsQuery.refetch()}>{t("Thử lại", "Retry")}</button></div>}
            {selectedBrandQuery.error && <p role="alert" className="text-xs text-red-400">{t("Không tải được thông tin thương hiệu hiện tại; mã thương hiệu vẫn được giữ trong lựa chọn.", "Unable to load the current brand; its ID remains selected.")}</p>}
            {brandsQuery.hasNextPage && <button type="button" aria-label={t("Tải thêm thương hiệu trong chỉnh sửa sản phẩm", "Load more brands while editing product")} disabled={brandsQuery.isFetchingNextPage} onClick={() => brandsQuery.fetchNextPage()} className="text-xs text-blue-400 underline disabled:opacity-50">{brandsQuery.isFetchingNextPage ? t("Đang tải thương hiệu...", "Loading brands...") : t("Tải thêm thương hiệu", "Load more brands")}</button>}
            {brandsQuery.isFetchNextPageError && <p role="alert" className="text-xs text-red-400">{t("Không tải được trang thương hiệu tiếp theo. Hãy thử lại.", "Unable to load the next brand page. Please retry.")}</p>}

            <Input
              id="tone"
              label={t("Phong cách & Cảm xúc hình ảnh", "Visual tone & mood")}
              placeholder={t("Ví dụ: Tương phản cao, ánh sáng viền trong phòng chụp tối", "Example: High contrast, dark studio rim light")}
              {...register("tone")}
            />

            <Textarea id="description" label={t("Mô tả sản phẩm", "Product description")} error={validationMessage(errors.description?.message)} {...register("description")} />

            <div className="flex flex-wrap justify-end gap-3 pt-4 border-t border-[#2c3038]">
              <Button type="button" variant="outline" disabled={isSubmitting || recovery.recovering} onClick={() => { if (!isSubmitting && !recovery.recovering) setIsEditOpen(false); }}>
                {t("Hủy", "Cancel")}
              </Button>
              <Button type="submit" variant="primary" disabled={recovery.blocked} isLoading={isSubmitting}>
                {t("Lưu thay đổi", "Save changes")}
              </Button>
            </div>
          </fieldset>
        </form>
      </Dialog>
    </div>
  );
}
