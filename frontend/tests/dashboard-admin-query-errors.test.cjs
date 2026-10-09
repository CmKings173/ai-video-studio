const test = require("node:test");
const assert = require("node:assert/strict");
const React = require("react");
const { loadTypeScript } = require("./load-typescript.cjs");

function nodes(node) {
  if (!node || typeof node !== "object") return [];
  if (typeof node.type === "function") return nodes(node.type(node.props));
  return [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)];
}

function text(node) {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join(" ");
  if (typeof node.type === "function") return text(node.type(node.props));
  return React.Children.toArray(node.props?.children).map(text).join(" ");
}

function pageHarness({ queries = [], infiniteQueries = [], isAdmin = true } = {}) {
  const calls = { refetch: [], fetchNextPage: [] };
  const queryOptions = [];
  const infiniteOptions = [];
  const states = [];
  let stateCursor = 0;
  let queryCursor = 0;
  let infiniteCursor = 0;

  const mocks = {
    react: {
      ...React,
      useState(initial) {
        const index = stateCursor++;
        if (!(index in states)) states[index] = initial;
        return [states[index], (next) => {
          states[index] = typeof next === "function" ? next(states[index]) : next;
        }];
      },
    },
    "next/link": ({ href, children, ...props }) => React.createElement("a", { href, ...props }, children),
    "@tanstack/react-query": {
      useQuery(options) {
        queryOptions.push(options);
        return queries[queryCursor++] ?? {};
      },
      useInfiniteQuery(options) {
        infiniteOptions.push(options);
        return infiniteQueries[infiniteCursor++] ?? {};
      },
      useMutation: () => ({ isPending: false, mutate() {}, mutateAsync: async () => {} }),
      useQueryClient: () => ({ invalidateQueries: async () => {} }),
    },
    "@/lib/auth/auth-context": { useAuth: () => ({ isAdmin }) },
    "@/lib/api/dashboard": { getDashboardSummary: async () => ({}), getSystemStatus: async () => ({}) },
    "@/lib/api/videos": { listVideos: async () => ({ items: [] }) },
    "@/lib/api/projects": { listProjects: async () => ({ items: [] }) },
    "@/lib/api/admin": new Proxy({}, { get: () => async () => ({}) }),
    "@/lib/api/errors": { getErrorMessage: (_error, fallback) => fallback },
    "react-hook-form": {
      useForm: () => ({
        register: () => ({}), handleSubmit: () => () => {}, reset() {},
        formState: { errors: {}, isSubmitting: false },
      }),
    },
    "@hookform/resolvers/zod": { zodResolver: () => () => {} },
    "@/components/page-kit": {
      PageHeader: () => null,
      QueryErrorNotice: ({ title, detail, onRetry, retryLabel = "Thử lại", isRetrying }) =>
        React.createElement("div", { role: "alert" }, title, React.createElement("p", null, detail),
          React.createElement("button", { type: "button", disabled: isRetrying, onClick: onRetry }, retryLabel)),
      MetricCard: ({ label, value }) => React.createElement("article", { "data-metric": label },
        React.createElement("span", null, label), React.createElement("strong", null, value)),
      StatusPill: ({ status }) => React.createElement("span", { "data-status": status }, status),
      EmptyState: ({ title, detail, action }) => React.createElement("section", { "data-empty-state": title },
        React.createElement("h3", null, title), React.createElement("p", null, detail),
        action?.label ? React.createElement("span", null, action.label) : null),
    },
    "@/components/ui/skeleton": { Skeleton: (props) => React.createElement("div", { ...props, "data-skeleton": true }) },
    "@/components/ui/badge": { Badge: ({ status, children }) => React.createElement("span", { "data-status": status }, children ?? status) },
    "@/components/ui/alert": { Alert: ({ title, children, ...props }) => React.createElement("div", { ...props, role: "alert", "data-title": title }, title, children) },
    "@/components/ui/button": { Button: ({ children, isLoading, ...props }) => React.createElement("button", { ...props, disabled: props.disabled || isLoading }, children) },
    "@/components/ui/tabs": { Tabs: ({ tabs, onChange }) => React.createElement("nav", null,
      tabs.map((tab) => React.createElement("button", { key: tab.id, role: "tab", onClick: () => onChange(tab.id) },
        `${tab.label}${tab.count === undefined ? "" : ` ${tab.count}`}`))) },
    "@/components/ui/input": { Input: (props) => React.createElement("input", props) },
    "@/components/ui/select": { Select: ({ children, ...props }) => React.createElement("select", props, children) },
    "@/components/ui/dialog": { Dialog: () => null },
  };

  const renderPage = (path) => {
    queryCursor = 0;
    infiniteCursor = 0;
    stateCursor = 0;
    const Page = loadTypeScript(path, mocks).default;
    return Page({});
  };
  const render = (path) => {
    queryOptions.length = 0;
    infiniteOptions.length = 0;
    return renderPage(path);
  };
  const dashboard = () => render("app/dashboard/page.tsx");
  const admin = () => render("app/admin/page.tsx");
  return { dashboard, admin, calls, queryOptions, infiniteOptions, states };
}

function button(tree, label) {
  const found = nodes(tree).find((node) => node.type === "button" && text(node).includes(label));
  assert.ok(found, `button containing ${label} should be rendered`);
  return found;
}

const summary = { projects: 0, videos: 0, assemblies_pending: 0, generations_running: 0,
  generations_pending: 0, assets_ready: 0, generations_failed: 0 };
const healthy = {
  status: "ok",
  postgres: { healthy: true }, minio: { healthy: true }, comfyui: { healthy: true },
  local_storage: { healthy: true, details: { free_bytes: 1024 } },
};
const emptyPage = { items: [], total: 0, page: 1, page_size: 50 };

function dashboardQueries() {
  return [summary, healthy, emptyPage, emptyPage].map((data) => ({
    data, isLoading: false, isError: false, isFetching: false,
  }));
}

test("dashboard health initial loading shows skeletons without an error and then renders successful health", () => {
  const queries = dashboardQueries();
  queries[1] = { data: undefined, isLoading: true, isError: false, isFetching: true };
  const h = pageHarness({ queries });
  let tree = h.dashboard();
  assert.ok(nodes(tree).some((node) => node.props?.["data-skeleton"]));
  assert.doesNotMatch(text(tree), /Không thể lấy trạng thái hệ thống|Trạng thái chưa xác minh|Hoạt động tốt|Ngoại tuyến/);
  queries[1] = { data: healthy, isLoading: false, isError: false };
  tree = h.dashboard();
  assert.equal(nodes(tree).some((node) => node.props?.["data-status"] === "OK"), true);
  assert.match(text(tree), /Hoạt động tốt/);
  assert.equal(nodes(tree).some((node) => node.props?.["data-skeleton"]), false);
});

test("dashboard initial health failure retries only health and recovers to the successful response", async () => {
  const queries = dashboardQueries();
  let retries = 0;
  queries[1] = { data: undefined, isLoading: false, isError: true, refetch: async () => {
    retries++;
    queries[1] = { data: healthy, isLoading: false, isError: false };
  } };
  const h = pageHarness({ queries });
  let tree = h.dashboard();
  assert.match(text(tree), /Không thể lấy trạng thái hệ thống/);
  assert.equal(nodes(tree).some((node) => node.props?.["data-status"] === "DEGRADED"), false);
  await button(tree, "Thử lại trạng thái").props.onClick();
  tree = h.dashboard();
  assert.equal(retries, 1);
  assert.doesNotMatch(text(tree), /Không thể lấy trạng thái hệ thống/);
  assert.match(text(tree), /Hoạt động tốt/);
});

test("dashboard cached health remains visible while refreshing but is suppressed on failure and recovers on retry", async () => {
  const queries = dashboardQueries();
  queries[1] = { data: healthy, isLoading: false, isError: false, isFetching: true };
  const h = pageHarness({ queries });
  assert.match(text(h.dashboard()), /Hoạt động tốt/);
  queries[1] = { data: healthy, isLoading: false, isError: true, refetch: async () => {
    queries[1] = { data: { ...healthy, status: "degraded", postgres: { healthy: false } }, isLoading: false, isError: false };
  } };
  let tree = h.dashboard();
  assert.match(text(tree), /Không thể lấy trạng thái hệ thống/);
  assert.doesNotMatch(text(tree), /Hoạt động tốt/);
  assert.equal(nodes(tree).some((node) => node.props?.["data-status"] === "OK"), false);
  await button(tree, "Thử lại trạng thái").props.onClick();
  tree = h.dashboard();
  assert.doesNotMatch(text(tree), /Không thể lấy trạng thái hệ thống/);
  assert.equal(nodes(tree).some((node) => node.props?.["data-status"] === "DEGRADED"), true);
});

for (const { index, emptyTitle, staleTitle, label, item } of [
  { index: 2, emptyTitle: "Chưa có video nào", staleTitle: "Danh sách video chưa được cập nhật", label: "Cached video", item: { id: "cached-video", title: "Cached video", kind: "QUICK_CLIP", aspect_ratio: "9:16", target_duration: 8, status: "READY" } },
  { index: 3, emptyTitle: "Chưa có dự án", staleTitle: "Danh sách dự án chưa được cập nhật", label: "Cached project", item: { id: "cached-project", name: "Cached project", archived: false } },
]) {
  test(`dashboard ${index === 2 ? "videos" : "projects"} cached empty failure is unverified and retry success restores the empty state`, async () => {
    const queries = dashboardQueries();
    const h = pageHarness({ queries });
    assert.ok(text(h.dashboard()).includes(emptyTitle));
    queries[index] = { data: emptyPage, isLoading: false, isError: true, refetch: async () => {
      queries[index] = { data: emptyPage, isLoading: false, isError: false };
    } };
    let tree = h.dashboard();
    assert.ok(text(tree).includes(staleTitle));
    assert.equal(text(tree).includes(emptyTitle), false);
    assert.match(text(tree), /chưa thể xác minh/i);
    await button(tree, "Thử lại").props.onClick();
    tree = h.dashboard();
    assert.ok(text(tree).includes(emptyTitle));
    assert.equal(text(tree).includes(staleTitle), false);
  });

  test(`dashboard ${index === 2 ? "videos" : "projects"} cached populated failure keeps explicitly stale rows and retry replaces them`, async () => {
    const queries = dashboardQueries();
    queries[index] = { data: { items: [item] }, isLoading: false, isError: true, refetch: async () => {
      queries[index] = { data: { items: [{ ...item, title: "Fresh video", name: "Fresh project" }] }, isLoading: false, isError: false };
    } };
    const h = pageHarness({ queries });
    let tree = h.dashboard();
    assert.ok(text(tree).includes(label));
    assert.ok(text(tree).includes(staleTitle));
    assert.match(text(tree), /dữ liệu đã tải trước đó/);
    assert.equal(text(tree).includes(emptyTitle), false);
    await button(tree, "Thử lại").props.onClick();
    tree = h.dashboard();
    assert.match(text(tree), /Fresh/);
    assert.equal(text(tree).includes(label), false);
    assert.equal(text(tree).includes(staleTitle), false);
  });
}

test("dashboard keeps successful zero distinct from API failure and retries only the failed summary", async () => {
  const refetch = [0, 1, 2, 3].map(() => async () => {});
  const h = pageHarness({ queries: [
    { data: summary, isLoading: false, isError: false, refetch: refetch[0] },
    { data: healthy, isLoading: false, isError: false, refetch: refetch[1] },
    { data: { items: [] }, isLoading: false, isError: false, refetch: refetch[2] },
    { data: { items: [] }, isLoading: false, isError: false, refetch: refetch[3] },
  ] });
  let tree = h.dashboard();
  assert.equal(nodes(tree).filter((node) => node.props?.["data-metric"]).length, 4);
  assert.equal(nodes(tree).find((node) => node.props?.["data-metric"] === "Tổng số video").props.children[1].props.children, 0);

  let retryCount = 0;
  const failureHarness = pageHarness({ queries: [
    { data: undefined, isLoading: false, isError: true, error: new Error("HTTP 500"), refetch: async () => { retryCount++; } },
    { data: healthy, isLoading: false, isError: false, refetch: async () => {} },
    { data: { items: [] }, isLoading: false, isError: false, refetch: async () => {} },
    { data: { items: [] }, isLoading: false, isError: false, refetch: async () => {} },
  ] });
  tree = failureHarness.dashboard();
  assert.equal(nodes(tree).filter((node) => node.props?.["data-metric"]).length, 0);
  assert.match(text(tree), /Không thể tải số liệu tổng quan/);
  await button(tree, "Thử lại").props.onClick();
  assert.equal(retryCount, 1);
});

test("dashboard marks cached summary as stale and never infers degraded health from an unavailable status API", () => {
  const h = pageHarness({ queries: [
    { data: { ...summary, videos: 7 }, isLoading: false, isError: true, refetch() {} },
    { data: undefined, isLoading: false, isError: true, refetch() {} },
    { data: undefined, isLoading: false, isError: true, refetch() {} },
    { data: undefined, isLoading: false, isError: false, refetch() {} },
  ] });
  const tree = h.dashboard();
  assert.match(text(tree), /chưa được cập nhật/i);
  assert.match(text(nodes(tree).find((node) => node.props?.["data-metric"] === "Tổng số video")), /7/);
  assert.equal(nodes(tree).some((node) => node.props?.["data-status"] === "DEGRADED"), false);
  assert.doesNotMatch(text(tree), /Chưa có video nào/);
  assert.match(text(tree), /Không thể tải video gần đây/);
});

test("dashboard distinguishes successful populated lists from network timeout and retries only the failed list", async () => {
  const videos = [{ id: "video-1", title: "Launch", kind: "QUICK_CLIP", aspect_ratio: "9:16", target_duration: 8, status: "READY" }];
  const projects = [{ id: "project-1", name: "Campaign", archived: false }];
  const queries = [
    { data: summary, isLoading: false, isError: false, refetch: async () => {} },
    { data: healthy, isLoading: false, isError: false, refetch: async () => {} },
    { data: undefined, isLoading: false, isError: true, error: new Error("Network timeout"), refetch: async () => { queries[2] = { data: { items: videos }, isLoading: false, isError: false }; } },
    { data: { items: projects }, isLoading: false, isError: false, refetch: async () => {} },
  ];
  const h = pageHarness({ queries });
  let tree = h.dashboard();
  assert.match(text(tree), /Không thể tải video gần đây/);
  assert.match(text(tree), /Campaign/);
  assert.doesNotMatch(text(tree), /Chưa có video nào/);
  await button(tree, "Thử lại").props.onClick();
  tree = h.dashboard();
  assert.match(text(tree), /Launch/);
  assert.doesNotMatch(text(tree), /Không thể tải video gần đây/);
});

test("dashboard retry recovers failed summary and project list without refetching healthy queries", async () => {
  const retryCounts = [0, 0, 0, 0];
  const queries = [
    { data: undefined, isLoading: false, isError: true, refetch: async () => {
      retryCounts[0]++;
      queries[0] = { data: { ...summary, videos: 9 }, isLoading: false, isError: false };
    } },
    { data: healthy, isLoading: false, isError: false, refetch: async () => { retryCounts[1]++; } },
    { data: { items: [] }, isLoading: false, isError: false, refetch: async () => { retryCounts[2]++; } },
    { data: undefined, isLoading: false, isError: true, refetch: async () => {
      retryCounts[3]++;
      queries[3] = { data: { items: [{ id: "project-2", name: "Recovered" }] }, isLoading: false, isError: false };
    } },
  ];
  const h = pageHarness({ queries });
  let tree = h.dashboard();
  await button(tree, "Thử lại").props.onClick();
  tree = h.dashboard();
  assert.match(text(tree), /9/);
  assert.match(text(tree), /Không thể tải dự án gần đây/);
  await button(tree, "Thử lại").props.onClick();
  tree = h.dashboard();
  assert.match(text(tree), /Recovered/);
  assert.deepEqual(retryCounts, [1, 0, 0, 1]);
});

test("dashboard shows degraded only when the successful health response says degraded", () => {
  const degraded = { ...healthy, status: "degraded", postgres: { healthy: false } };
  const h = pageHarness({ queries: [
    { data: summary, isLoading: false, isError: false },
    { data: degraded, isLoading: false, isError: false },
    { data: { items: [] }, isLoading: false, isError: false },
    { data: { items: [] }, isLoading: false, isError: false },
  ] });
  const tree = h.dashboard();
  assert.equal(nodes(tree).some((node) => node.props?.["data-status"] === "DEGRADED"), true);
  assert.match(text(tree), /Có sự cố/);
  assert.match(text(tree), /Chưa có video nào/);
});

test("admin system and storage API failures render retry states instead of degraded or zero summaries", async () => {
  let systemRetries = 0;
  let storageRetries = 0;
  const queries = [
    { data: undefined, isLoading: false, isError: true, refetch: async () => {
      systemRetries++;
      queries[0] = { data: healthy, isLoading: false, isError: false };
    } },
    { data: undefined, isLoading: false, isError: true, refetch: async () => {
      storageRetries++;
      queries[1] = { data: { database_assets: 0, database_bytes: 0, object_count: 0, object_bytes: 0, pending_assets: 0, deleted_assets: 0, failed_assets: 0 }, isLoading: false, isError: false };
    } },
  ];
  const h = pageHarness({
    queries,
    infiniteQueries: [
      { data: { pages: [emptyPage] }, isLoading: false, isError: false },
      { data: { pages: [emptyPage] }, isLoading: false, isError: false },
    ],
  });
  let tree = h.admin();
  assert.match(text(tree), /Không thể lấy trạng thái hệ thống/);
  assert.doesNotMatch(text(tree), /DEGRADED/);
  await button(tree, "Thử lại").props.onClick();
  assert.equal(systemRetries, 1);
  assert.equal(storageRetries, 0);
  tree = h.admin();
  assert.doesNotMatch(text(tree), /Không thể lấy trạng thái hệ thống/);
  assert.doesNotMatch(text(tree), /DEGRADED/);

  nodes(tree).find((node) => node.props?.role === "tab" && text(node).includes("Lưu trữ")).props.onClick();
  tree = h.admin();
  assert.match(text(tree), /Không thể tải tổng quan lưu trữ/);
  assert.doesNotMatch(text(tree), /Tài nguyên trong cơ sở dữ liệu 0|Đối tượng MinIO 0/);
  await button(tree, "Thử lại").props.onClick();
  assert.equal(storageRetries, 1);
  assert.equal(systemRetries, 1);
  tree = h.admin();
  assert.doesNotMatch(text(tree), /Không thể tải tổng quan lưu trữ/);
  assert.match(text(tree), /Tài nguyên trong cơ sở dữ liệu 0/);
  assert.match(text(tree), /Đối tượng MinIO 0/);
});

test("admin storage cleanup and reconciliation stay disabled while cached counts are refreshing", () => {
  const h = pageHarness({
    queries: [
      { data: healthy, isLoading: false, isError: false },
      { data: { database_assets: 5, database_bytes: 100, object_count: 5, object_bytes: 100, pending_assets: 0, deleted_assets: 0, failed_assets: 0 }, isLoading: false, isError: false, isFetching: true },
    ],
    infiniteQueries: [
      { data: { pages: [emptyPage] }, isLoading: false, isError: false },
      { data: { pages: [emptyPage] }, isLoading: false, isError: false },
    ],
  });
  let tree = h.admin();
  nodes(tree).find((node) => node.props?.role === "tab" && text(node).includes("Lưu trữ")).props.onClick();
  tree = h.admin();
  for (const label of ["Xem trước dọn dẹp", "Thực thi dọn dẹp", "Đối soát lưu trữ"]) {
    assert.equal(button(tree, label).props.disabled, true, `${label} must wait for the storage refresh`);
  }
});

test("admin empty users/workflows are explicit, while failed initial pages show retry and no fake rows", async () => {
  const infiniteQueries = [
    { data: undefined, isLoading: false, isError: true, refetch: async () => {
      infiniteQueries[0] = { data: { pages: [emptyPage] }, isLoading: false, isError: false, hasNextPage: false };
    } },
    { data: undefined, isLoading: false, isError: true, refetch: async () => {
      infiniteQueries[1] = { data: { pages: [emptyPage] }, isLoading: false, isError: false, hasNextPage: false };
    } },
  ];
  const h = pageHarness({
    queries: [
      { data: healthy, isLoading: false, isError: false },
      { data: { database_assets: 0, database_bytes: 0, object_count: 0, object_bytes: 0, pending_assets: 0, deleted_assets: 0, failed_assets: 0 }, isLoading: false, isError: false },
    ],
    infiniteQueries,
  });
  let tree = h.admin();
  nodes(tree).find((node) => node.props?.role === "tab" && text(node).includes("Tài khoản biên tập viên")).props.onClick();
  tree = h.admin();
  assert.match(text(tree), /Không thể tải danh sách người dùng/);
  assert.equal(nodes(tree).some((node) => node.type === "table"), false);
  assert.doesNotMatch(text(tree), /Chưa có người dùng/);
  await button(tree, "Thử lại").props.onClick();
  tree = h.admin();
  assert.match(text(tree), /Chưa có người dùng/);

  nodes(tree).find((node) => node.props?.role === "tab" && text(node).includes("Workflow ComfyUI")).props.onClick();
  tree = h.admin();
  assert.match(text(tree), /Không thể tải danh sách workflow/);
  assert.doesNotMatch(text(tree), /Phê duyệt & Bật/);
  await button(tree, "Thử lại").props.onClick();
  tree = h.admin();
  assert.match(text(tree), /Chưa có workflow/);
});

test("admin successful empty workflow response renders an explicit empty state", () => {
  const h = pageHarness({
    queries: [
      { data: healthy, isLoading: false, isError: false },
      { data: {}, isLoading: false, isError: false },
    ],
    infiniteQueries: [
      { data: { pages: [emptyPage] }, isLoading: false, isError: false },
      { data: { pages: [emptyPage] }, isLoading: false, isError: false },
    ],
  });
  let tree = h.admin();
  nodes(tree).find((node) => node.props?.role === "tab" && text(node).includes("Workflow ComfyUI")).props.onClick();
  tree = h.admin();
  assert.match(text(tree), /Chưa có workflow/);
  assert.equal(nodes(tree).some((node) => node.type === "table"), false);
});

test("admin page-two failure preserves page one and retries only the next page", async () => {
  let fetches = 0;
  let refetches = 0;
  const firstUser = { id: "user-1", name: "One", email: "one@example.test", role: "EDITOR", is_active: true };
  const secondUser = { id: "user-2", name: "Two", email: "two@example.test", role: "EDITOR", is_active: true };
  const infiniteQueries = [
    { data: { pages: [{ ...emptyPage, items: [firstUser], total: 51 }] }, isLoading: false, isError: true,
      isFetchNextPageError: true, error: new Error("page 2 failed"), hasNextPage: true,
      isFetchingNextPage: false, fetchNextPage: async () => {
        fetches++;
        infiniteQueries[0] = { data: { pages: [{ ...emptyPage, items: [firstUser], total: 2 }, { ...emptyPage, page: 2, items: [secondUser], total: 2 }] }, isLoading: false, isError: false, hasNextPage: false };
      }, refetch: async () => { refetches++; } },
    { data: { pages: [emptyPage] }, isLoading: false, isError: false },
  ];
  const h = pageHarness({
    queries: [
      { data: healthy, isLoading: false, isError: false },
      { data: {}, isLoading: false, isError: false },
    ],
    infiniteQueries,
  });
  let tree = h.admin();
  nodes(tree).find((node) => node.props?.role === "tab" && text(node).includes("Tài khoản biên tập viên")).props.onClick();
  tree = h.admin();
  assert.match(text(tree), /one@example\.test/);
  assert.match(text(tree), /Không thể tải thêm trang người dùng/);
  assert.doesNotMatch(text(tree), /Chưa có người dùng/);
  await button(tree, "Thử lại trang tiếp theo").props.onClick();
  assert.equal(fetches, 1);
  assert.equal(refetches, 0);
  tree = h.admin();
  assert.match(text(tree), /two@example\.test/);
  assert.doesNotMatch(text(tree), /Không thể tải thêm trang người dùng/);
});

test("admin successful empty lists stay explicit, and workflow page-two failure preserves page one", async () => {
  const workflow = { id: "wf-1", code: "director_t2v", mode: "t2v", version: 1, enabled: false };
  let fetches = 0;
  const infiniteQueries = [
    { data: { pages: [emptyPage] }, isLoading: false, isError: false, hasNextPage: false },
    { data: { pages: [{ ...emptyPage, items: [workflow], total: 51 }] }, isLoading: false, isError: true,
      isFetchNextPageError: true, hasNextPage: true, isFetchingNextPage: false,
      fetchNextPage: async () => {
        fetches++;
        infiniteQueries[1] = { data: { pages: [{ ...emptyPage, items: [workflow], total: 2 }, { ...emptyPage, page: 2, items: [{ ...workflow, id: "wf-2", code: "director_i2v" }], total: 2 }] }, isLoading: false, isError: false, hasNextPage: false };
      }, refetch: async () => {} },
  ];
  const h = pageHarness({
    queries: [
      { data: healthy, isLoading: false, isError: false },
      { data: {}, isLoading: false, isError: false },
    ],
    infiniteQueries,
  });
  let tree = h.admin();
  nodes(tree).find((node) => node.props?.role === "tab" && text(node).includes("Tài khoản biên tập viên")).props.onClick();
  tree = h.admin();
  assert.match(text(tree), /Chưa có người dùng/);
  assert.equal(nodes(tree).some((node) => node.type === "table"), false);

  nodes(tree).find((node) => node.props?.role === "tab" && text(node).includes("Workflow ComfyUI")).props.onClick();
  tree = h.admin();
  assert.match(text(tree), /director_t2v/);
  assert.match(text(tree), /Không thể tải thêm trang workflow/);
  await button(tree, "Thử lại trang tiếp theo").props.onClick();
  assert.equal(fetches, 1);
  tree = h.admin();
  assert.match(text(tree), /director_i2v/);
  assert.doesNotMatch(text(tree), /Không thể tải thêm trang workflow/);
});

test("non-admin still gets the access-denied page and no admin mutations", () => {
  const h = pageHarness({ isAdmin: false });
  const tree = h.admin();
  assert.match(text(tree), /Truy cập bị từ chối/);
  assert.doesNotMatch(text(tree), /Thêm người dùng|Thực thi dọn dẹp|Phê duyệt & Bật/);
});
