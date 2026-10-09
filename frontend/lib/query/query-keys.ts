export const queryKeys = {
  auth: {
    me: ["auth", "me"] as const,
  },
  dashboard: {
    summary: ["dashboard", "summary"] as const,
    systemStatus: ["dashboard", "system-status"] as const,
  },
  projects: {
    all: ["projects"] as const,
    list: (filters?: Record<string, unknown>) => ["projects", "list", filters] as const,
    detail: (id: string) => ["projects", "detail", id] as const,
    videos: (id: string, filters?: Record<string, unknown>) => ["projects", id, "videos", filters] as const,
    assets: (id: string, filters?: Record<string, unknown>) => ["projects", id, "assets", filters] as const,
  },
  brands: {
    all: ["brands"] as const,
    list: (filters?: Record<string, unknown>) => ["brands", "list", filters] as const,
    detail: (id: string) => ["brands", "detail", id] as const,
  },
  products: {
    all: ["products"] as const,
    list: (filters?: Record<string, unknown>) => ["products", "list", filters] as const,
    detail: (id: string) => ["products", "detail", id] as const,
    assets: (id: string, filters?: Record<string, unknown>) => ["products", id, "assets", filters] as const,
  },
  videos: {
    all: ["videos"] as const,
    list: (filters?: Record<string, unknown>) => ["videos", "list", filters] as const,
    detail: (id: string) => ["videos", "detail", id] as const,
    scenes: (id: string) => ["videos", id, "scenes"] as const,
    finalVersions: (id: string) => ["videos", id, "final-versions"] as const,
  },
  scenes: {
    detail: (id: string) => ["scenes", "detail", id] as const,
    generations: (id: string, filters?: Record<string, unknown>) => ["scenes", id, "generations", filters] as const,
    promptPreview: (id: string) => ["scenes", id, "prompt-preview"] as const,
  },
  generations: {
    detail: (id: string) => ["generations", "detail", id] as const,
    attempts: (id: string) => ["generations", id, "attempts"] as const,
  },
  finalVersions: {
    detail: (id: string) => ["final-versions", "detail", id] as const,
  },
  assets: {
    all: ["assets"] as const,
    list: (filters?: Record<string, unknown>) => ["assets", "list", filters] as const,
    detail: (id: string) => ["assets", "detail", id] as const,
    download: (id: string) => ["assets", "download", id] as const,
  },
  admin: {
    users: (filters?: Record<string, unknown>) => ["admin", "users", filters] as const,
    workflows: (filters?: Record<string, unknown>) => ["admin", "workflows", filters] as const,
    systemStatus: ["admin", "system-status"] as const,
    storageSummary: ["admin", "storage-summary"] as const,
  },
};
