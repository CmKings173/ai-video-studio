"use client";

import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Plus, Search, Building2 } from "lucide-react";
import { listProducts, createProduct } from "@/lib/api/products";
import { listBrands, createBrand } from "@/lib/api/brands";
import { queryKeys } from "@/lib/query/query-keys";
import { getErrorMessage } from "@/lib/api/errors";
import { PageHeader, Card, StatusPill, EmptyState } from "@/components/page-kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select } from "@/components/ui/select";
import { Dialog } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert } from "@/components/ui/alert";

const productSchema = z.object({
  name: z.string().min(1, "Tên sản phẩm không được để trống").max(255),
  description: z.string().max(20000).default(""),
  brand_id: z.string().optional().nullable(),
  tone: z.string().optional(),
});

type ProductFormValues = z.infer<typeof productSchema>;

const brandSchema = z.object({
  name: z.string().min(1, "Tên thương hiệu không được để trống").max(255),
  description: z.string().max(20000).default(""),
});

type BrandFormValues = z.infer<typeof brandSchema>;

function contextString(context: Record<string, unknown> | undefined, key: string): string {
  const value = context?.[key];
  return typeof value === "string" ? value : "";
}

export default function ProductsPage() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [selectedBrand, setSelectedBrand] = useState<string>("");
  const [isProductOpen, setIsProductOpen] = useState(false);
  const [isBrandOpen, setIsBrandOpen] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const { data: brandsData } = useQuery({
    queryKey: queryKeys.brands.all,
    queryFn: () => listBrands({ size: 100 }),
  });

  const { data: productsData, isLoading, error } = useQuery({
    queryKey: queryKeys.products.list({
      search: search || undefined,
      brand_id: selectedBrand || undefined,
    }),
    queryFn: () =>
      listProducts({
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
        eyebrow="Master Data"
        title="Sản phẩm & Thương hiệu"
        description="Sản phẩm tồn tại độc lập với chiến dịch để tái sử dụng hình ảnh và visual guidelines qua nhiều dự án."
      >
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="md" onClick={() => setIsBrandOpen(true)}>
            <Building2 className="w-4 h-4" />
            <span>Thêm Brand</span>
          </Button>
          <Button variant="primary" size="md" onClick={() => setIsProductOpen(true)}>
            <Plus className="w-4 h-4" />
            <span>Tạo sản phẩm</span>
          </Button>
        </div>
      </PageHeader>

      {/* Filter and Search Bar */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-4 p-4 rounded-md border border-[#2c3038] bg-[#181a1e]">
        <div className="relative w-full sm:w-80">
          <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-[#9ea5b0]" />
          <input
            type="text"
            placeholder="Tìm kiếm sản phẩm..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-10 pr-4 py-2 rounded-lg bg-[#0b101a] border border-[#2c3038] text-sm text-[#f1f3f5] placeholder-[#9ea5b0] focus:outline-none focus:border-blue-500"
          />
        </div>

        <div className="flex items-center gap-3 w-full sm:w-auto">
          <label className="text-xs text-[#9ea5b0] font-semibold uppercase tracking-wider shrink-0">
            Thương hiệu:
          </label>
          <select
            value={selectedBrand}
            onChange={(e) => setSelectedBrand(e.target.value)}
            className="rounded-lg bg-[#0b101a] border border-[#2c3038] text-sm text-[#f1f3f5] px-3 py-1.5 focus:outline-none focus:border-blue-500"
          >
            <option value="">Tất cả thương hiệu</option>
            {brandsData?.items.map((brand) => (
              <option key={brand.id} value={brand.id}>
                {brand.name}
              </option>
            ))}
          </select>
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
        <Alert variant="destructive" title="Không thể tải danh sách sản phẩm">
          {getErrorMessage(error)}
        </Alert>
      ) : !productsData?.items.length ? (
        <EmptyState
          title="Không tìm thấy sản phẩm"
          detail={search ? "Không có sản phẩm nào khớp với tìm kiếm." : "Tạo sản phẩm đầu tiên để bắt đầu sinh video."}
          action={{ label: "Tạo sản phẩm ngay", onClick: () => setIsProductOpen(true) }}
        />
      ) : (
        <section className="grid cards-grid">
          {productsData.items.map((product) => {
            const brand = brandsData?.items.find((b) => b.id === product.brand_id);
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
                  {tone || "Chưa có mô tả phong cách / tone visual."}
                </p>
              </Card>
            );
          })}
        </section>
      )}

      {/* Create Product Dialog */}
      <Dialog
        isOpen={isProductOpen}
        onClose={() => {
          setIsProductOpen(false);
          resetProduct();
          setServerError(null);
        }}
        title="Tạo sản phẩm mới"
        description="Định nghĩa sản phẩm và visual identity để AI áp dụng vào video."
      >
        {serverError && (
          <Alert variant="destructive" title="Lỗi">
            {serverError}
          </Alert>
        )}

        <form onSubmit={handleProductSubmit((data) => createProductMutation.mutateAsync(data))} className="space-y-4">
          <Input
            id="product_name"
            label="Tên sản phẩm"
            placeholder="Ví dụ: Aurora Luxury Perfume"
            error={productErrors.name?.message}
            {...registerProduct("name")}
          />

          <Select
            id="brand_id"
            label="Thương hiệu (Brand)"
            {...registerProduct("brand_id")}
          >
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
            placeholder="Ví dụ: Premium, cinematic, warm rim light"
            {...registerProduct("tone")}
          />

          <Textarea
            id="product_desc"
            label="Mô tả sản phẩm"
            placeholder="Mô tả chi tiết bao bì, chai lọ, logo, đặc tính sản phẩm..."
            {...registerProduct("description")}
          />

          <div className="flex justify-end gap-3 pt-4 border-t border-[#2c3038]">
            <Button type="button" variant="outline" onClick={() => setIsProductOpen(false)}>
              Hủy
            </Button>
            <Button type="submit" variant="primary" isLoading={isProductSubmitting}>
              Tạo sản phẩm
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
        title="Thêm thương hiệu (Brand)"
        description="Brand chứa visual guideline chung cho tập hợp các sản phẩm."
      >
        <form onSubmit={handleBrandSubmit((data) => createBrandMutation.mutateAsync(data))} className="space-y-4">
          <Input
            id="brand_name"
            label="Tên thương hiệu"
            placeholder="Ví dụ: Chanel, Apple, Nike"
            error={brandErrors.name?.message}
            {...registerBrand("name")}
          />

          <Textarea
            id="brand_desc"
            label="Mô tả thương hiệu"
            placeholder="Brand identity, guidelines..."
            {...registerBrand("description")}
          />

          <div className="flex justify-end gap-3 pt-4 border-t border-[#2c3038]">
            <Button type="button" variant="outline" onClick={() => setIsBrandOpen(false)}>
              Hủy
            </Button>
            <Button type="submit" variant="primary" isLoading={isBrandSubmitting}>
              Tạo thương hiệu
            </Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
