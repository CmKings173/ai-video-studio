"use client";

import { useI18n } from "@/lib/i18n";
import React, { useState } from "react";
import { useInfiniteQuery, useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Plus, Search, Building2 } from "lucide-react";
import { listProducts, createProduct } from "@/lib/api/products";
import { listBrands, createBrand } from "@/lib/api/brands";
import { queryKeys } from "@/lib/query/query-keys";
import { flattenPageItems, nextPageParam } from "@/lib/api/pagination";
import { getErrorMessage } from "@/lib/api/errors";
import { PageHeader, Card, StatusPill, EmptyState } from "@/components/page-kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select } from "@/components/ui/select";
import { Dialog } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert } from "@/components/ui/alert";
import { PaginationControls } from "@/components/list-pagination";

const productSchema = z.object({
  name: z.string().min(1, "required_product_name").max(255, "name_too_long"),
  description: z.string().max(20000, "description_too_long").default(""),
  brand_id: z.string().optional().nullable(),
  tone: z.string().optional(),
});

type ProductFormValues = z.infer<typeof productSchema>;

const brandSchema = z.object({
  name: z.string().min(1, "required_brand_name").max(255, "name_too_long"),
  description: z.string().max(20000, "description_too_long").default(""),
});

type BrandFormValues = z.infer<typeof brandSchema>;

function contextString(context: Record<string, unknown> | undefined, key: string): string {
  const value = context?.[key];
  return typeof value === "string" ? value : "";
}

export default function ProductsPage() {
  const { t } = useI18n();
  const validationMessage = (message?: string) => {
    const messages: Record<string, string> = {
      required_product_name: t("Tên sản phẩm không được để trống", "Product name is required"),
      required_brand_name: t("Tên thương hiệu không được để trống", "Brand name is required"),
      name_too_long: t("Tên tối đa 255 ký tự", "Name must be at most 255 characters"),
      description_too_long: t("Mô tả tối đa 20.000 ký tự", "Description must be at most 20,000 characters"),
    };
    return message ? messages[message] ?? t("Thông tin không hợp lệ", "Invalid value") : undefined;
  };
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [selectedBrand, setSelectedBrand] = useState<string>("");
  const [selectedBrandOption, setSelectedBrandOption] = useState<{ id: string; name: string } | null>(null);
  const [selectedProductBrandOption, setSelectedProductBrandOption] = useState<{ id: string; name: string } | null>(null);
  const [brandSearch, setBrandSearch] = useState("");
  const [isProductOpen, setIsProductOpen] = useState(false);
  const [isBrandOpen, setIsBrandOpen] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const brandsQuery = useInfiniteQuery({
    queryKey: queryKeys.brands.list({ size: 50 }),
    queryFn: ({ pageParam }) => listBrands({ page: pageParam, size: 50 }),
    initialPageParam: 1,
    getNextPageParam: nextPageParam,
  });
  const brandItems = flattenPageItems(brandsQuery.data?.pages);
  const matchingBrands = brandItems.filter((brand) => brand.name.toLocaleLowerCase().includes(brandSearch.trim().toLocaleLowerCase()));
  const visibleFilterBrands: { id: string; name: string }[] = [...brandItems];
  if (selectedBrandOption && !visibleFilterBrands.some((brand) => brand.id === selectedBrand)) visibleFilterBrands.unshift(selectedBrandOption);
  const visibleProductBrands: { id: string; name: string }[] = [...matchingBrands];
  if (selectedProductBrandOption && !visibleProductBrands.some((brand) => brand.id === selectedProductBrandOption.id)) visibleProductBrands.unshift(selectedProductBrandOption);

  const { data: productsData, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: queryKeys.products.list({
      page,
      size: 20,
      search: search || undefined,
      brand_id: selectedBrand || undefined,
    }),
    queryFn: () =>
      listProducts({
        page,
        size: 20,
        search: search || undefined,
        brand_id: selectedBrand || undefined,
      }),
  });

  const {
    register: registerProduct,
    handleSubmit: handleProductSubmit,
    reset: resetProduct,
    formState: { errors: productErrors, isSubmitting: isProductSubmitting },
  } = useForm<ProductFormValues>({
    resolver: zodResolver(productSchema),
  });

  const {
    register: registerBrand,
    handleSubmit: handleBrandSubmit,
    reset: resetBrand,
    formState: { errors: brandErrors, isSubmitting: isBrandSubmitting },
  } = useForm<BrandFormValues>({
    resolver: zodResolver(brandSchema),
  });
  const productBrandField = registerProduct("brand_id");

  const brandPaginationControls = (context: string) => (
    <div className="space-y-1">
      {brandsQuery.isLoading && <p role="status" className="text-xs text-[#9ea5b0]">{t("Đang tải thương hiệu...", "Loading brands...")}</p>}
      {brandsQuery.error && <div role="alert" className="text-xs text-red-400">{getErrorMessage(brandsQuery.error)} <button type="button" className="underline" onClick={() => brandsQuery.refetch()}>{t("Thử lại", "Retry")}</button></div>}
      {brandsQuery.hasNextPage && <button type="button" aria-label={t(`Tải thêm thương hiệu (${context})`, `Load more brands (${context})`)} disabled={brandsQuery.isFetchingNextPage} onClick={() => brandsQuery.fetchNextPage()} className="text-xs text-blue-400 underline disabled:opacity-50">{brandsQuery.isFetchingNextPage ? t("Đang tải thương hiệu...", "Loading brands...") : t("Tải thêm thương hiệu", "Load more brands")}</button>}
      {brandsQuery.isFetchNextPageError && <p role="alert" className="text-xs text-red-400">{t("Không tải được trang thương hiệu tiếp theo. Hãy thử lại.", "Unable to load the next brand page. Please retry.")}</p>}
    </div>
  );

  const createProductMutation = useMutation({
    mutationFn: (data: ProductFormValues) =>
      createProduct({
        name: data.name,
        description: data.description,
        brand_id: data.brand_id ? data.brand_id : null,
        context: data.tone ? { tone: data.tone } : {},
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.products.all });
      setIsProductOpen(false);
      resetProduct();
    },
    onError: (err) => setServerError(getErrorMessage(err)),
  });

  const createBrandMutation = useMutation({
    mutationFn: (data: BrandFormValues) =>
      createBrand({
        name: data.name,
        description: data.description,
        context: {},
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.brands.all });
      setIsBrandOpen(false);
      resetBrand();
    },
    onError: (err) => setServerError(getErrorMessage(err)),
  });

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow={t("Dữ liệu sản phẩm", "Master data")}
        title={t("Sản phẩm & Thương hiệu", "Products & Brands")}
        description={t("Sản phẩm tồn tại độc lập với chiến dịch để tái sử dụng hình ảnh và hướng dẫn hình ảnh qua nhiều dự án.", "Products are independent of campaigns, allowing images and visual guidelines to be reused across projects.")}
      >
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="md" onClick={() => setIsBrandOpen(true)}>
            <Building2 className="w-4 h-4" />
            <span>{t("Thêm thương hiệu", "Add brand")}</span>
          </Button>
          <Button variant="primary" size="md" onClick={() => setIsProductOpen(true)}>
            <Plus className="w-4 h-4" />
            <span>{t("Tạo sản phẩm", "Create product")}</span>
          </Button>
        </div>
      </PageHeader>

      {/* Filter and Search Bar */}
      <div className="grid grid-cols-1 sm:grid-cols-2 items-start gap-3 p-4 rounded-md border border-[#2c3038] bg-[#181a1e]">
        <div className="min-w-0 space-y-1.5">
          <label htmlFor="product_name_search" className="text-xs font-medium text-[#9ea5b0]">{t("Tên sản phẩm", "Product name")}</label>
          <div className="relative h-10 min-w-0 self-start">
            <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-[#9ea5b0]" />
            <input
              type="text"
              id="product_name_search"
              placeholder={t("Tìm kiếm sản phẩm...", "Search products...")}
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1); }}
              className="h-10 w-full min-w-0 max-w-full pl-10 pr-4 py-2 rounded-lg bg-[#0b101a] border border-[#2c3038] text-sm text-[#f1f3f5] placeholder-[#9ea5b0] focus:outline-none focus:border-blue-500"
            />
          </div>
        </div>

        <div className="min-w-0 space-y-1.5">
          <label htmlFor="brand_filter" className="text-xs font-medium text-[#9ea5b0]">
            {t("Thương hiệu", "Brand")}
          </label>
          <select
            id="brand_filter"
            value={selectedBrand}
            onChange={(event) => {
              const id = event.target.value;
              setSelectedBrand(id);
              setPage(1);
              setSelectedBrandOption(brandItems.find((brand) => brand.id === id) ?? null);
            }}
            className="h-10 min-w-0 w-full max-w-full rounded-lg bg-[#0b101a] border border-[#2c3038] text-sm text-[#f1f3f5] px-3 py-2 focus:outline-none focus:border-blue-500"
          >
            <option value="">{t("Tất cả thương hiệu", "All brands")}</option>
            {visibleFilterBrands.map((brand) => (
              <option key={brand.id} value={brand.id}>
                {brand.name}
              </option>
            ))}
          </select>
          {brandPaginationControls(t("lọc sản phẩm", "product filter"))}
        </div>
      </div>

      {/* Product Cards Grid */}
      {isLoading ? (
        <div className="grid cards-grid">
          <Skeleton className="h-44" />
          <Skeleton className="h-44" />
          <Skeleton className="h-44" />
        </div>
      ) : error ? (
        <Alert variant="destructive" title={t("Không thể tải danh sách sản phẩm", "Unable to load products")}>
          {getErrorMessage(error)}
          <button type="button" disabled={isFetching} onClick={() => { if (!isFetching) void refetch(); }} className="ml-3 underline disabled:opacity-50">
            {isFetching ? t("Đang tải...", "Loading...") : t("Thử lại", "Retry")}
          </button>
        </Alert>
      ) : !productsData?.items.length ? (
        <EmptyState
          title={page > 1 ? t("Trang này không có kết quả", "No results on this page") : t("Không tìm thấy sản phẩm", "Product not found")}
          detail={page > 1 ? t("Quay lại trang trước để xem các kết quả đã tải.", "Go back to the previous page to view loaded results.") : search ? t("Không có sản phẩm nào khớp với tìm kiếm.", "No products match your search.") : t("Tạo sản phẩm đầu tiên để bắt đầu tạo video.", "Create your first product to start creating videos.")}
          action={{ label: t("Tạo sản phẩm ngay", "Create product now"), onClick: () => setIsProductOpen(true) }}
        />
      ) : (
        <section className="grid cards-grid">
          {productsData.items.map((product) => {
            const brand = brandItems.find((b) => b.id === product.brand_id) ?? (selectedBrandOption?.id === product.brand_id ? selectedBrandOption : undefined);
            const tone = contextString(product.context, "tone") || product.description;
            return (
              <Card
                href={`/products/${product.id}`}
                key={product.id}
                title={product.name}
                badge={<StatusPill status={product.archived ? "ARCHIVED" : "READY"} />}
              >
                {brand && (
                  <span className="text-[11px] font-semibold text-blue-400 uppercase tracking-wider">
                    {brand.name}
                  </span>
                )}
                <p className="line-clamp-2 text-xs text-[#9ea5b0]">
                  {tone || t("Chưa có mô tả phong cách hình ảnh.", "No visual style description yet.")}
                </p>
              </Card>
            );
          })}
        </section>
      )}
      <PaginationControls
        data={error ? undefined : productsData}
        page={page}
        onPageChange={setPage}
        busy={isFetching}
      />

      {/* Create Product Dialog */}
      <Dialog
        isOpen={isProductOpen}
        onClose={() => {
          setIsProductOpen(false);
          resetProduct();
          setServerError(null);
        }}
        title={t("Tạo sản phẩm mới", "Create product")}
        description={t("Định nghĩa sản phẩm và nhận diện hình ảnh để AI áp dụng vào video.", "Define the product and visual identity for AI to apply to videos.")}
      >
        {serverError && (
          <Alert variant="destructive" title={t("Lỗi", "Error")}>
            {serverError}
          </Alert>
        )}

        <form onSubmit={handleProductSubmit((data) => createProductMutation.mutateAsync(data))} className="space-y-4">
          <Input
            id="product_name"
            label={t("Tên sản phẩm", "Product name")}
            placeholder={t("Ví dụ: Nước hoa cao cấp Aurora", "Example: Aurora Luxury Perfume")}
            error={validationMessage(productErrors.name?.message)}
            {...registerProduct("name")}
          />

          <Input
            id="product_brand_search"
            aria-label={t("Tìm thương hiệu đã tải", "Search loaded brands")}
            placeholder={t("Tìm thương hiệu đã tải...", "Search loaded brands...")}
            value={brandSearch}
            onChange={(event) => setBrandSearch(event.target.value)}
          />
          <Select
            id="brand_id"
            label={t("Thương hiệu", "Brand")}
            {...productBrandField}
            onChange={(event) => {
              void productBrandField.onChange(event);
              const id = event.target.value;
              setSelectedProductBrandOption(brandItems.find((brand) => brand.id === id) ?? (id ? { id, name: event.target.selectedOptions[0]?.textContent?.trim() || id } : null));
            }}
          >
            <option value="">{t("-- Không thuộc thương hiệu nào --", "-- No brand --")}</option>
            {visibleProductBrands.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </Select>
          {brandPaginationControls(t("tạo sản phẩm", "product creation"))}

          <Input
            id="tone"
            label={t("Phong cách & Cảm xúc hình ảnh", "Visual tone & mood")}
            placeholder={t("Ví dụ: Cao cấp, điện ảnh, ánh sáng viền ấm", "Example: Premium, cinematic, warm rim light")}
            {...registerProduct("tone")}
          />

          <Textarea
            id="product_desc"
            label={t("Mô tả sản phẩm", "Product description")}
            placeholder={t("Mô tả chi tiết bao bì, chai lọ, logo, đặc tính sản phẩm...", "Describe packaging, bottles, logos, and product features...")}
            error={validationMessage(productErrors.description?.message)}
            {...registerProduct("description")}
          />

          <div className="flex justify-end gap-3 pt-4 border-t border-[#2c3038]">
            <Button type="button" variant="outline" onClick={() => setIsProductOpen(false)}>
              {t("Hủy", "Cancel")}
            </Button>
            <Button type="submit" variant="primary" isLoading={isProductSubmitting}>
              {t("Tạo sản phẩm", "Create product")}
            </Button>
          </div>
        </form>
      </Dialog>

      {/* Create Brand Dialog */}
      <Dialog
        isOpen={isBrandOpen}
        onClose={() => {
          setIsBrandOpen(false);
          resetBrand();
          setServerError(null);
        }}
        title={t("Thêm thương hiệu", "Add brand")}
        description={t("Thương hiệu chứa hướng dẫn hình ảnh chung cho các sản phẩm.", "A brand holds shared visual guidelines for its products.")}
      >
        <form onSubmit={handleBrandSubmit((data) => createBrandMutation.mutateAsync(data))} className="space-y-4">
          <Input
            id="brand_name"
            label={t("Tên thương hiệu", "Brand name")}
            placeholder={t("Ví dụ: Chanel, Apple, Nike", "Example: Chanel, Apple, Nike")}
            error={validationMessage(brandErrors.name?.message)}
            {...registerBrand("name")}
          />

          <Textarea
            id="brand_desc"
            label={t("Mô tả thương hiệu", "Brand description")}
            placeholder={t("Nhận diện thương hiệu, hướng dẫn hình ảnh...", "Brand identity, guidelines...")}
            error={validationMessage(brandErrors.description?.message)}
            {...registerBrand("description")}
          />

          <div className="flex justify-end gap-3 pt-4 border-t border-[#2c3038]">
            <Button type="button" variant="outline" onClick={() => setIsBrandOpen(false)}>
              {t("Hủy", "Cancel")}
            </Button>
            <Button type="submit" variant="primary" isLoading={isBrandSubmitting}>
              {t("Tạo thương hiệu", "Create brand")}
            </Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
