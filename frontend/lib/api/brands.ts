import { apiClient } from "./client";
import type { BrandCreate, BrandDTO, BrandPatch, Page } from "./types";

export type ListBrandsParams = {
  page?: number;
  size?: number;
  search?: string;
  archived?: boolean;
};

export async function listBrands(params?: ListBrandsParams): Promise<Page<BrandDTO>> {
  return apiClient.get<Page<BrandDTO>>("/api/v1/brands", { params });
}

export async function createBrand(payload: BrandCreate): Promise<BrandDTO> {
  return apiClient.post<BrandDTO>("/api/v1/brands", payload);
}

export async function getBrand(brandId: string): Promise<BrandDTO> {
  return apiClient.get<BrandDTO>(`/api/v1/brands/${brandId}`);
}

export async function patchBrand(
  brandId: string,
  payload: BrandPatch,
  revision: number
): Promise<BrandDTO> {
  return apiClient.patch<BrandDTO>(`/api/v1/brands/${brandId}`, payload, { revision });
}
