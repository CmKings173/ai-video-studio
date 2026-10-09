const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const React = require("react");
const { loadTypeScript } = require("./load-typescript.cjs");

const root = path.resolve(__dirname, "..");
const { flattenPageItems, nextPageParam } = loadTypeScript("lib/api/pagination.ts");

function asset(id) {
  return { id, filename: `${id}.png`, content_type: "image/png", status: "READY", kind: "IMAGE", project_id: "project", product_id: "product" };
}

function nodes(node) {
  if (!node || typeof node !== "object") return [];
  return [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)];
}

function generationEditorHarness() {
  let state = [];
  let cursor = 0;
  let queryIndex = 0;
  let pages = [];
  let infiniteQuery;
  const calls = [];
  const pageData = [
    { items: [asset("image-1")], total: 101, page: 1, page_size: 50 },
    { items: [asset("image-2")], total: 101, page: 2, page_size: 50 },
    { items: [asset("image-3")], total: 101, page: 3, page_size: 50 },
  ];
  const cap = { workflow_id: "workflow", execution_scope: "single_scene", mode: "r2v", quality_profile: "STANDARD", aspect_ratio: "9:16", required_asset_slots: [], max_reference_images: 4, max_reference_videos: 4, max_reference_audio: 4, max_total_reference_files: 12 };
  const scene = { id: "scene", revision: 1, scene_order: 0, duration_seconds: 5, enabled: true, spec: {}, generation_config: { mode: "AUTO", reference_image_asset_ids: ["saved-image"] } };
  const video = { id: "video", revision: 1, aspect_ratio: "9:16", project_id: "project", product_id: "product", scenes: [scene] };
  const editor = loadTypeScript("components/generation/generation-editor.tsx", {
    react: { ...React, useEffect: () => {}, useState: (initial) => {
      const index = cursor++;
      if (!(index in state)) state[index] = typeof initial === "function" ? initial() : initial;
      return [state[index], (value) => { state[index] = typeof value === "function" ? value(state[index]) : value; }];
    } },
    "@tanstack/react-query": {
      useQueryClient: () => ({ invalidateQueries: async () => {} }),
      useQuery: () => queryIndex++ === 0 ? { data: { available: true, combinations: [cap] } } : { data: undefined },
      useQueries: ({ queries }) => queries.map(() => ({ data: asset("saved-image") })),
      useInfiniteQuery: (options) => {
        infiniteQuery = options;
        return { data: { pages }, hasNextPage: pages.length < pageData.length, isFetchingNextPage: false,
          fetchNextPage: async () => {
            const next = options.getNextPageParam(pages.at(-1), pages);
            if (next !== undefined) pages.push(await options.queryFn({ pageParam: next }));
          } };
      },
    },
    "@/lib/api/assets": {
      getAsset: async (id) => asset(id),
      listAssets: async (params) => { calls.push(params); return pageData[params.page - 1]; },
    },
    "@/lib/api/generations": { generationCapabilities: async () => ({ available: true, combinations: [cap] }), previewPrompt: async () => ({}) },
    "@/lib/api/scenes": { patchScene: async () => ({}) },
  }).GenerationEditor;
  const render = () => {
    cursor = 0;
    queryIndex = 0;
    return editor({ scene, scenes: [scene], video, isLoading: false, onClose: () => {}, onSubmit: async () => {} });
  };
  const loadMoreButton = (tree) => nodes(tree).find((node) => node.props?.onClick && nodes(node).some((child) => child.type === "span" && child.props.children === "Tải thêm tài nguyên"));
  return { render, calls, pageData, get query() { return infiniteQuery; }, set pages(value) { pages = value; }, loadMoreButton };
}

function adminHarness(activeTab) {
  let states = [];
  let cursor = 0;
  const listCalls = { users: [], workflows: [] };
  const pages = {
    users: [{ items: [{ id: "user-1", name: "One", email: "one@example.test", role: "EDITOR", is_active: true }], total: 51, page: 1, page_size: 50 }],
    workflows: [{ items: [{ id: "workflow-1", code: "one", mode: "t2v", version: "1", enabled: true }], total: 51, page: 1, page_size: 50 }],
  };
  const data = {
    users: [pages.users[0], { items: [{ id: "user-2", name: "Two", email: "two@example.test", role: "ADMIN", is_active: true }], total: 51, page: 2, page_size: 50 }],
    workflows: [pages.workflows[0], { items: [{ id: "workflow-2", code: "two", mode: "i2v", version: "2", enabled: false }], total: 51, page: 2, page_size: 50 }],
  };
  const queries = {};
  const admin = loadTypeScript("app/admin/page.tsx", {
    react: { ...React, useEffect: () => {}, useState: (initial) => {
      const index = cursor++;
      if (!(index in states)) states[index] = index === 0 ? activeTab : initial;
      return [states[index], (value) => { states[index] = typeof value === "function" ? value(states[index]) : value; }];
    } },
    "@tanstack/react-query": {
      useQuery: (options) => ({ data: options.queryKey.includes("system-status")
        ? { postgres: { healthy: true }, minio: { healthy: true }, comfyui: { healthy: true }, local_storage: { healthy: true, details: { free_bytes: 1 } } }
        : {}, isLoading: false, refetch: async () => {} }),
      useInfiniteQuery: (options) => {
        const key = options.queryKey[1];
        const name = key === "users" ? "users" : "workflows";
        queries[name] = options;
        return { data: { pages: pages[name] }, isLoading: false, hasNextPage: pages[name].length < data[name].length,
          isFetchingNextPage: false, fetchNextPage: async () => {
            const next = options.getNextPageParam(pages[name].at(-1), pages[name]);
            if (next !== undefined) pages[name].push(await options.queryFn({ pageParam: next }));
          } };
      },
      useMutation: () => ({ isPending: false, mutate: () => {} }),
      useQueryClient: () => ({ invalidateQueries: async () => {} }),
    },
    "react-hook-form": { useForm: () => ({ register: () => ({}), handleSubmit: () => () => {}, reset: () => {}, formState: { errors: {}, isSubmitting: false } }) },
    "@hookform/resolvers/zod": { zodResolver: () => () => {} },
    "@/lib/auth/auth-context": { useAuth: () => ({ isAdmin: true }) },
    "@/lib/api/admin": {
      getAdminSystemStatus: async () => ({}), getStorageSummary: async () => ({}), cleanupStorage: async () => ({}), reconcileStorage: async () => ({}),
      listUsers: async (params) => { listCalls.users.push(params); return data.users[params.page - 1]; },
      listWorkflows: async (params) => { listCalls.workflows.push(params); return data.workflows[params.page - 1]; },
      createUser: async () => ({}), patchUser: async () => ({}), approveWorkflow: async () => ({}),
    },
  }).default;
  const render = () => { cursor = 0; return admin({}); };
  return { render, queries, listCalls, pages };
}

function containsText(node, label) {
  return Boolean(node && typeof node === "object" && (node.props?.children === label
    || React.Children.toArray(node.props?.children).some((child) => containsText(child, label))));
}

function loadMoreButton(tree, label) {
  return nodes(tree).find((node) => node.props?.onClick && containsText(node, label));
}

test("pagination follows server page metadata and merges duplicate IDs in stable order", () => {
  const first = { items: [{ id: "a" }, { id: "b" }], total: 5, page: 1, page_size: 2 };
  const second = { items: [{ id: "b" }, { id: "c" }], total: 5, page: 2, page_size: 2 };
  const last = { items: [{ id: "d" }], total: 5, page: 3, page_size: 2 };
  assert.equal(nextPageParam(first), 2);
  assert.equal(nextPageParam(second), 3);
  assert.equal(nextPageParam(last), undefined);
  assert.equal(nextPageParam({ items: [], total: 0, page: 1, page_size: 50 }), undefined);
  assert.deepEqual(flattenPageItems([first, second, last]), [{ id: "a" }, { id: "b" }, { id: "c" }, { id: "d" }]);
});

test("generation editor appends scoped asset pages without changing saved reference order", async () => {
  const h = generationEditorHarness();
  h.pages = [h.pageData[0]];
  let tree = h.render();
  assert.equal(h.query.initialPageParam, 1);
  assert.deepEqual(h.query.queryKey.at(-1), { project_id: "project", product_id: "product", status: "READY", size: 50 });
  assert.equal(h.query.getNextPageParam(h.pageData[0]), 2);
  assert.deepEqual(await h.query.queryFn({ pageParam: 1 }), h.pageData[0]);
  assert.deepEqual(h.calls[0], { project_id: "project", product_id: "product", status: "READY", size: 50, page: 1 });

  const references = () => nodes(tree).find((node) => node.props?.label === "Ảnh tham chiếu");
  assert.deepEqual(references().props.selected, ["saved-image"]);
  await h.loadMoreButton(tree).props.onClick();
  tree = h.render();
  assert.ok(references().props.assets.some((item) => item.id === "image-2"));
  references().props.onChange(["image-2", "saved-image"]);
  tree = h.render();
  await h.loadMoreButton(tree).props.onClick();
  tree = h.render();
  assert.deepEqual(references().props.selected, ["image-2", "saved-image"]);
  assert.ok(references().props.assets.some((item) => item.id === "image-3"));
  assert.deepEqual(h.calls.slice(1).map(({ page, project_id, product_id, status, size }) => ({ page, project_id, product_id, status, size })), [
    { page: 2, project_id: "project", product_id: "product", status: "READY", size: 50 },
    { page: 3, project_id: "project", product_id: "product", status: "READY", size: 50 },
  ]);
  assert.equal(h.query.getNextPageParam(h.pageData[2], h.pageData), undefined);
});

test("admin user pagination appends the next page and keeps the size-scoped query key", async () => {
  const h = adminHarness("users");
  let tree = h.render();
  const query = h.queries.users;
  assert.deepEqual(query.queryKey.at(-1), { size: 50 });
  assert.equal(query.initialPageParam, 1);
  assert.equal(query.getNextPageParam({ items: [], total: 101, page: 1, page_size: 50 }), 2);
  assert.deepEqual(await query.queryFn({ pageParam: 1 }), h.pages.users[0]);
  assert.deepEqual(h.listCalls.users[0], { size: 50, page: 1 });
  await loadMoreButton(tree, "Tải thêm người dùng").props.onClick();
  tree = h.render();
  assert.ok(nodes(tree).some((node) => node.props?.children === "Two"));
  assert.deepEqual(h.listCalls.users.at(-1), { size: 50, page: 2 });
});

test("admin workflow pagination appends the next page", async () => {
  const h = adminHarness("workflows");
  let tree = h.render();
  const query = h.queries.workflows;
  assert.deepEqual(query.queryKey.at(-1), { size: 50 });
  await loadMoreButton(tree, "Tải thêm workflow").props.onClick();
  tree = h.render();
  assert.ok(nodes(tree).some((node) => node.props?.children === "two"));
  assert.deepEqual(h.listCalls.workflows.at(-1), { size: 50, page: 2 });
});

test("assembly and admin lists use server-driven infinite pagination controls", () => {
  const files = [
    ["app/videos/[videoId]/assembly/page.tsx", "Tải thêm tệp âm thanh"],
    ["app/admin/page.tsx", "Tải thêm người dùng"],
    ["app/admin/page.tsx", "Tải thêm workflow"],
  ];
  for (const [relative, label] of files) {
    const source = fs.readFileSync(path.join(root, relative), "utf8");
    assert.match(source, /useInfiniteQuery/, `${relative} must retain loaded pages`);
    assert.match(source, /getNextPageParam:\s*nextPageParam/, `${relative} must follow API page metadata`);
    assert.match(source, /fetchNextPage\(\)/, `${relative} must expose an action to load another page`);
    assert.ok(source.includes(label), `${relative} must label its list-specific load-more action`);
  }
});
