import { apiClient } from "./client";
import type { AssetDTO, Page, ProductCreate, ProductDTO, ProductPatch } from "./types";

export type ListProductsParams = {
  page?: number;
  size?: number;
  search?: string;
  archived?: boolean;
  brand_id?: string;
};

export async function listProducts(params?: ListProductsParams): Promise<Page<ProductDTO>> {
  return apiClient.get<Page<ProductDTO>>("/api/v1/products", { params });
}

export async function createProduct(payload: ProductCreate): Promise<ProductDTO> {
  return apiClient.post<ProductDTO>("/api/v1/products", payload);
}

export async function getProduct(productId: string): Promise<ProductDTO> {
  return apiClient.get<ProductDTO>(`/api/v1/products/${productId}`);
}

export async function patchProduct(
  productId: string,
  payload: ProductPatch,
  revision: number
): Promise<ProductDTO> {
  return apiClient.patch<ProductDTO>(`/api/v1/products/${productId}`, payload, { revision });
}

export async function archiveProduct(productId: string, revision: number): Promise<ProductDTO> {
  return apiClient.post<ProductDTO>(`/api/v1/products/${productId}/archive`, undefined, { revision });
}

export async function getProductAssets(
  productId: string,
  params?: { page?: number; size?: number }
): Promise<Page<AssetDTO>> {
  return apiClient.get<Page<AssetDTO>>(`/api/v1/products/${productId}/assets`, { params });
}
