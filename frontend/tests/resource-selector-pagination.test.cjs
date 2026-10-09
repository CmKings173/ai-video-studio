const assert = require("node:assert/strict");
const test = require("node:test");
const React = require("react");
const { loadTypeScript } = require("./load-typescript.cjs");

const PAGE_SIZE = 50;

function page(items, number, total = 100) {
  return { items, total, page: number, page_size: PAGE_SIZE };
}

function nodes(node) {
  if (Array.isArray(node)) return node.flatMap(nodes);
  if (!node || typeof node !== "object") return [];
  if (typeof node.type === "function") return [node, ...nodes(node.type(node.props))];
  return [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)];
}

function textOf(node) {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  return textOf(node.props?.children);
}

function createHarness({ route, pageSets, initialProjectId = "", productBrand = "brand-2", locale = "vi" }) {
  const state = [];
  const forms = [];
  const queryPages = {};
  const infiniteQueries = {};
  const queryStates = {};
  const refetchCalls = {};
  const createVideoCalls = [];
  const listCalls = { projects: [], products: [], brands: [], videos: [] };
  const queryCalls = [];
  let stateCursor = 0;
  let formCursor = 0;
  let refCursor = 0;
  const refs = [];

  const react = {
    ...React,
    use: () => ({ productId: "product-1" }),
    useEffect: () => {},
    useRef: (initial) => refs[refCursor++] ||= { current: initial },
    useCallback: (callback) => callback,
    useState(initial) {
      const index = stateCursor++;
      if (!(index in state)) state[index] = typeof initial === "function" ? initial() : initial;
      return [state[index], (value) => { state[index] = typeof value === "function" ? value(state[index]) : value; }];
    },
  };

  const useForm = ({ defaultValues = {} } = {}) => {
    const index = formCursor++;
    if (!forms[index]) forms[index] = { values: { ...defaultValues } };
    const form = forms[index];
    return {
      control: form,
      register: (name) => ({
        name,
        onChange: (event) => { form.values[name] = event.target.value; },
      }),
      handleSubmit: (callback) => (event) => { event?.preventDefault?.(); return callback(form.values); },
      reset: (value = {}) => { form.values = { ...value }; },
      setValue: (name, value) => { form.values[name] = value; },
      watch: (name) => form.values[name],
      formState: { errors: {}, isSubmitting: false },
    };
  };

  const listFn = (name) => async (params = {}) => {
    listCalls[name].push(params);
    return pageSets[name]?.[Math.max(0, (params.page ?? 1) - 1)] ?? page([], params.page ?? 1, 0);
  };

  const mocks = {
    react,
    "@/lib/i18n": { useI18n: () => ({ locale, t: (vi, en) => locale === "en" ? en : vi, setLocale: () => {} }) },
    "next/link": ({ children, ...props }) => React.createElement("a", props, children),
    "next/navigation": {
      useRouter: () => ({ push: () => {} }),
      useSearchParams: () => ({ get: (key) => key === "projectId" ? initialProjectId : null }),
    },
    "@tanstack/react-query": {
      useInfiniteQuery(options) {
        const name = options.queryKey[0];
        infiniteQueries[name] = options;
        if (!queryPages[name]) queryPages[name] = [pageSets[name]?.[0] ?? page([], 1, 0)];
        const pages = queryPages[name];
        return {
          data: { pages },
          error: null,
          isLoading: false,
          isFetchingNextPage: false,
          isFetchNextPageError: false,
          hasNextPage: options.getNextPageParam(pages.at(-1), pages) !== undefined,
          fetchNextPage: async () => {
            const next = options.getNextPageParam(pages.at(-1), pages);
            if (next !== undefined) pages.push(await options.queryFn({ pageParam: next }));
          },
          refetch: async () => { refetchCalls[name] = (refetchCalls[name] ?? 0) + 1; },
          ...queryStates[name],
        };
      },
      useQuery(options) {
        queryCalls.push(options.queryKey);
        const [root, kind, id] = options.queryKey;
        if (root === "videos") return { data: pageSets.videoList ?? page([], 1, 0), isLoading: false, error: null };
        if (root === "products" && kind === "list") return { data: pageSets.productList ?? page([], 1, 0), isLoading: false, error: null };
        if (root === "products" && kind === "detail") return { data: pageSets.productDetail, isLoading: false, error: null, refetch: async () => {} };
        if (root === "products" && kind === "product-1" && id === "assets") return { data: page([], 1, 0), isLoading: false, error: null };
        if (root === "brands" && kind === "detail" && id) return { data: { id, name: id === productBrand ? "Brand Two" : id }, isLoading: false, error: null };
        return { data: undefined, isLoading: false, error: null };
      },
      useMutation: (options) => ({ mutate: () => {}, mutateAsync: options.mutationFn, isPending: false }),
      useQueryClient: () => ({ invalidateQueries: async () => {} }),
    },
    "react-hook-form": {
      useForm,
      useWatch: ({ control, name }) => control.values[name],
    },
    "@hookform/resolvers/zod": { zodResolver: () => () => ({}) },
    zod: {
      z: {
        string: () => validator(),
        object: () => validator(),
        enum: () => validator(),
        coerce: { number: () => validator() },
      },
    },
    "@/lib/api/projects": { listProjects: listFn("projects"), getProject: async (id) => ({ id, name: id }) },
    "@/lib/api/products": {
      listProducts: listFn("products"),
      getProduct: async () => pageSets.productDetail,
      getProductAssets: async () => page([], 1, 0),
      createProduct: async () => ({}),
      patchProduct: async () => ({}),
      archiveProduct: async () => ({}),
    },
    "@/lib/api/brands": {
      listBrands: listFn("brands"),
      getBrand: async (id) => ({ id, name: id === productBrand ? "Brand Two" : id }),
      createBrand: async () => ({}),
    },
    "@/lib/api/videos": {
      listVideos: async (params) => { listCalls.videos.push(params); return pageSets.videoList ?? page([], 1, 0); },
      createVideo: async (data) => { createVideoCalls.push(data); return {}; },
    },
    "@/lib/api/errors": { getErrorMessage: (error) => String(error ?? "Request failed"), isRevisionConflict: () => false },
    "@/components/ui/select": {
      Select: (props) => React.createElement("select", props, props.children),
    },
    "@/components/ui/input": {
      Input: (props) => React.createElement("input", props),
    },
    "@/components/ui/button": {
      Button: ({ children, ...props }) => React.createElement("button", props, children),
    },
    "@/components/ui/textarea": { Textarea: (props) => React.createElement("textarea", props) },
    "@/components/ui/dialog": {
      Dialog: ({ isOpen, children }) => isOpen ? React.createElement("div", { role: "dialog" }, children) : null,
    },
    "@/components/ui/alert": { Alert: ({ children, ...props }) => React.createElement("div", props, children) },
    "@/components/ui/skeleton": { Skeleton: () => null },
    "@/components/page-kit": {
      PageHeader: ({ children }) => React.createElement("header", null, children),
      StatusPill: ({ status }) => React.createElement("span", null, status),
      EmptyState: ({ title, detail }) => React.createElement("div", null, title, detail),
      Card: ({ children, href, title }) => React.createElement("a", { href }, title, children),
    },
  };

  const entry = route === "videos" ? "app/videos/page.tsx"
    : route === "newVideo" ? "app/videos/new/page.tsx"
      : route === "products" ? "app/products/page.tsx"
        : "app/products/[productId]/page.tsx";
  const component = loadTypeScript(entry, mocks).default;
  const render = () => {
    stateCursor = 0;
    refCursor = 0;
    formCursor = 0;
    const outer = route === "productDetail" ? component({ params: Promise.resolve({ productId: "product-1" }) }) : component();
    const tree = route === "productDetail" ? outer.type(outer.props) : outer;
    return route === "newVideo" ? React.Children.toArray(tree.props.children)[0].type() : tree;
  };

  return {
    render,
    queryPages,
    infiniteQueries,
    queryStates,
    refetchCalls,
    createVideoCalls,
    listCalls,
    queryCalls,
    forms,
  };
}

function validator() {
  let proxy;
  proxy = new Proxy({}, {
    get() {
      return () => proxy;
    },
  });
  return proxy;
}

function find(tree, predicate) {
  return nodes(tree).find(predicate);
}

function findSelect(tree, id) {
  return find(tree, (node) => node.type === "select" && node.props.id === id);
}

function findLoadMore(tree, label) {
  return find(tree, (node) => node.type === "button" && node.props["aria-label"] === label);
}

function optionValues(select) {
  return React.Children.toArray(select.props.children).map((option) => ({ value: option.props.value, label: textOf(option) }));
}

const projects = [page([{ id: "project-1", name: "Project One" }], 1), page([{ id: "project-2", name: "Project Two" }], 2)];
const products = [page([{ id: "product-1", name: "Product One" }], 1), page([{ id: "product-2", name: "Product Two" }], 2)];
const brands = [page([{ id: "brand-1", name: "Brand One" }], 1), page([{ id: "brand-2", name: "Brand Two" }], 2)];

test("video filters omit auxiliary search, load page two and retain selections outside loaded pages", async () => {
  const h = createHarness({
    route: "videos",
    pageSets: {
      projects,
      products,
      videoList: page([{ id: "video-1", title: "Clip", project_id: "project-2", product_id: "product-2", status: "READY", kind: "QUICK_CLIP", aspect_ratio: "9:16", target_duration: 5 }], 1, 1),
    },
  });
  let tree = h.render();
  assert.equal(find(tree, (node) => node.type === "input" && /Tìm (dự án|sản phẩm) đã tải/.test(node.props["aria-label"] ?? "")), undefined);
  await findLoadMore(tree, "Tải thêm dự án").props.onClick();
  await findLoadMore(tree, "Tải thêm sản phẩm").props.onClick();
  tree = h.render();

  const projectSelect = find(tree, (node) => node.type === "select" && node.props["aria-label"] === "Lọc theo dự án");
  const productSelect = find(tree, (node) => node.type === "select" && node.props["aria-label"] === "Lọc theo sản phẩm");
  assert.ok(optionValues(projectSelect).some((option) => option.value === "project-2"));
  assert.ok(optionValues(productSelect).some((option) => option.value === "product-2"));
  projectSelect.props.onChange({ target: { value: "project-2" } });
  productSelect.props.onChange({ target: { value: "product-2" } });

  h.queryPages.projects.splice(0, h.queryPages.projects.length, page([], 1, 0));
  h.queryPages.products.splice(0, h.queryPages.products.length, page([], 1, 0));
  tree = h.render();

  assert.deepEqual(optionValues(find(tree, (node) => node.type === "select" && node.props["aria-label"] === "Lọc theo dự án")).filter((option) => option.value), [
    { value: "project-2", label: "Project Two" },
  ]);
  assert.deepEqual(optionValues(find(tree, (node) => node.type === "select" && node.props["aria-label"] === "Lọc theo sản phẩm")).filter((option) => option.value), [
    { value: "product-2", label: "Product Two" },
  ]);
  assert.equal(h.infiniteQueries.projects.initialPageParam, 1);
  assert.equal(h.listCalls.projects.at(-1).page, 2);
  assert.equal(h.listCalls.products.at(-1).page, 2);
  assert.equal(h.listCalls.projects.at(-1).size, PAGE_SIZE);
  assert.equal(h.listCalls.products.at(-1).size, PAGE_SIZE);
  assert.equal(h.queryCalls.filter((key) => key[0] === "videos").at(-1)[2].project_id, "project-2");
  assert.equal(h.queryCalls.filter((key) => key[0] === "videos").at(-1)[2].product_id, "product-2");
});

test("new-video URL project stays read-only, skips both selectors and product lookup, and is sent to the API", async () => {
  const h = createHarness({ route: "newVideo", initialProjectId: "project-2", pageSets: { projects } });
  const tree = h.render();
  assert.equal(findSelect(tree, "project_id"), undefined);
  assert.equal(findSelect(tree, "product_id"), undefined);
  assert.match(textOf(tree), /project-2/);
  assert.equal(h.listCalls.products.length, 0);
  assert.equal(Object.keys(h.infiniteQueries).some((key) => key.includes("products")), false);
  await find(tree, (node) => node.type === "form").props.onSubmit();
  assert.equal(h.createVideoCalls[0].project_id, "project-2");
  assert.equal(h.createVideoCalls[0].kind, "QUICK_CLIP");
  assert.equal(h.createVideoCalls[0].aspect_ratio, "9:16");
  assert.equal(Object.hasOwn(h.createVideoCalls[0], "product_id"), false);
});

test("new-video auto-selects the sole project only after the complete active-project list loads", async () => {
  const onlyProject = page([{ id: "only-project", name: "Only active project" }], 1, 1);
  const h = createHarness({ route: "newVideo", pageSets: { projects: [onlyProject] } });
  const tree = h.render();
  assert.equal(findSelect(tree, "project_id"), undefined);
  assert.match(textOf(tree), /Dự án duy nhất đang hoạt động/);
  assert.match(textOf(tree), /Only active project/);
  assert.equal(h.forms[0].values.project_id, "only-project");
  const projectRequest = await h.infiniteQueries.projects.queryFn({ pageParam: 1 });
  assert.equal(h.listCalls.projects[0].archived, false);
  assert.equal(projectRequest.items[0].id, "only-project");
  await find(tree, (node) => node.type === "form").props.onSubmit();
  assert.equal(h.createVideoCalls[0].project_id, "only-project");
  assert.equal(Object.hasOwn(h.createVideoCalls[0], "product_id"), false);
});

test("new-video requires a project choice when multiple active projects exist and explains the project scope", async () => {
  const activeProjects = [page([{ id: "project-1", name: "Project One" }, { id: "project-2", name: "Project Two" }], 1, 2)];
  const h = createHarness({ route: "newVideo", pageSets: { projects: activeProjects } });
  const tree = h.render();
  const select = findSelect(tree, "project_id");
  assert.ok(select);
  assert.equal(select.props["aria-describedby"], "project_scope_help");
  assert.match(textOf(tree), /chọn dự án nơi lưu video/i);
  assert.deepEqual(optionValues(select).map((option) => option.value), ["", "project-1", "project-2"]);
  assert.equal(h.forms[0].values.project_id, "");
  select.props.onChange({ target: { value: "project-2", selectedOptions: [{ textContent: "Project Two" }] } });
  await find(tree, (node) => node.type === "form").props.onSubmit();
  assert.equal(h.createVideoCalls[0].project_id, "project-2");
});

test("new-video does not infer a sole project while another page may exist", async () => {
  const firstPage = page([{ id: "project-1", name: "Project One" }], 1, 51);
  const h = createHarness({ route: "newVideo", pageSets: { projects: [firstPage, page([{ id: "project-2", name: "Project Two" }], 2, 51)] } });
  let tree = h.render();
  assert.ok(findSelect(tree, "project_id"));
  assert.doesNotMatch(textOf(tree), /Dự án duy nhất đang hoạt động/);
  await findLoadMore(tree, "Tải thêm dự án").props.onClick();
  tree = h.render();
  assert.ok(findSelect(tree, "project_id"));
  assert.doesNotMatch(textOf(tree), /Dự án duy nhất đang hoạt động/);
  assert.deepEqual(optionValues(findSelect(tree, "project_id")).filter((option) => option.value), [
    { value: "project-1", label: "Project One" }, { value: "project-2", label: "Project Two" },
  ]);
});

test("new-video keeps project choice and retry when initial list loading fails instead of assuming there are no projects", () => {
  const h = createHarness({ route: "newVideo", pageSets: { projects: [] } });
  h.queryStates.projects = { data: undefined, error: new Error("Projects unavailable"), isLoading: false, isFetching: false };
  const tree = h.render();
  assert.ok(findSelect(tree, "project_id"));
  assert.match(textOf(tree), /Projects unavailable/);
  assert.ok(find(tree, (node) => node.type === "button" && textOf(node) === "Thử lại"));
  assert.equal(find(tree, (node) => node.type === "a" && node.props.href === "/projects"), undefined);
});

test("new-video does not auto-select one visible project when loading another page fails", async () => {
  const firstPage = page([{ id: "project-1", name: "Project One" }], 1, 51);
  const h = createHarness({ route: "newVideo", pageSets: { projects: [firstPage, page([{ id: "project-2", name: "Project Two" }], 2, 51)] } });
  h.queryStates.projects = { error: new Error("Next project page unavailable"), isFetchNextPageError: true };
  let tree = h.render();
  assert.ok(findSelect(tree, "project_id"));
  assert.doesNotMatch(textOf(tree), /Dự án duy nhất đang hoạt động/);
  assert.match(textOf(tree), /Next project page unavailable/);
  const nextPageError = find(tree, (node) => node.props?.role === "alert" && textOf(node).includes("Không tải được trang dự án tiếp theo"));
  const retry = find(nextPageError, (node) => node.type === "button" && textOf(node) === "Thử lại");
  await retry.props.onClick();
  assert.equal(h.listCalls.projects.at(-1).page, 2);
  tree = h.render();
  assert.ok(findSelect(tree, "project_id"));
  assert.doesNotMatch(textOf(tree), /Dự án duy nhất đang hoạt động/);
});

test("new-video offers a localized create-project link only after a verified empty result", () => {
  const h = createHarness({ route: "newVideo", pageSets: { projects: [page([], 1, 0)] } });
  const tree = h.render();
  assert.equal(findSelect(tree, "project_id"), undefined);
  const link = find(tree, (node) => node.type === "a" && node.props.href === "/projects");
  assert.ok(link);
  assert.match(textOf(link), /Tạo dự án/);
  assert.match(textOf(tree), /chưa có dự án đang hoạt động/i);
});

test("new-video explains the required project in English", () => {
  const activeProjects = [page([{ id: "project-1", name: "Project One" }, { id: "project-2", name: "Project Two" }], 1, 2)];
  const h = createHarness({ route: "newVideo", locale: "en", pageSets: { projects: activeProjects } });
  const tree = h.render();
  assert.match(textOf(tree), /Choose which project will store this video/);
  assert.equal(findSelect(tree, "project_id").props.label, "Project");
});

test("new-video ratio explains workflow clip sizing and final MP4 sizing", () => {
  const h = createHarness({ route: "newVideo", pageSets: { projects, products } });
  const tree = h.render();
  const ratio = findSelect(tree, "aspect_ratio");
  assert.equal(ratio.props["aria-describedby"], "aspect_ratio_help");
  const help = find(tree, (node) => node.props?.id === ratio.props["aria-describedby"]);
  assert.ok(help, "ratio help must be associated with the selector");
  assert.equal(textOf(help), "Sau khi tạo video, mở cảnh rồi chọn “Cấu hình & tạo clip” để chọn độ phân giải AI và workflow. Độ phân giải MP4 cuối được chọn tại “Ghép & xuất video”.");
});

test("new-video submission preserves URL project and omits the removed product field", async () => {
  const h = createHarness({ route: "newVideo", initialProjectId: "project-2", pageSets: { projects, products } });
  const tree = h.render();
  await find(tree, (node) => node.type === "form").props.onSubmit();
  assert.equal(h.createVideoCalls[0].project_id, "project-2");
  assert.equal(Object.hasOwn(h.createVideoCalls[0], "product_id"), false);
});

test("selector errors retain retry and page-two load more on each edited route", async () => {
  for (const route of ["videos", "newVideo", "products"]) {
    const h = createHarness({ route, pageSets: { projects, products, brands } });
    const resource = route === "products" ? "brands" : "projects";
    h.queryStates[resource] = { error: new Error("Selector unavailable"), isFetchNextPageError: true };
    let tree = h.render();
    assert.ok(find(tree, (node) => node.props?.role === "alert" && textOf(node).includes("Selector unavailable")));
    await find(tree, (node) => node.type === "button" && textOf(node) === "Thử lại").props.onClick();
    assert.equal(h.refetchCalls[resource], 1);
    const label = route === "products" ? "Tải thêm thương hiệu (lọc sản phẩm)" : "Tải thêm dự án";
    await findLoadMore(tree, label).props.onClick();
    tree = h.render();
    assert.equal(h.listCalls[resource].at(-1).page, 2);
    assert.equal(h.listCalls[resource].at(-1).size, PAGE_SIZE);
    const selector = route === "products" ? findSelect(tree, "brand_filter")
      : route === "newVideo" ? findSelect(tree, "project_id")
        : find(tree, (node) => node.type === "select" && node.props["aria-label"] === "Lọc theo dự án");
    assert.ok(optionValues(selector).some((option) => option.value === (route === "products" ? "brand-2" : "project-2")));
  }
});

test("product list brand filter and create selector can reach page two and preserve the selected brand", async () => {
  const h = createHarness({ route: "products", pageSets: { brands, productList: page([{ id: "product-1", name: "Product One", brand_id: "brand-2", description: "", context: {}, archived: false }], 1, 1) } });
  let tree = h.render();
  assert.equal(find(tree, (node) => node.type === "input" && node.props["aria-label"] === "Tìm thương hiệu đã tải"), undefined);
  await findLoadMore(tree, "Tải thêm thương hiệu (lọc sản phẩm)").props.onClick();
  tree = h.render();

  const filter = findSelect(tree, "brand_filter");
  assert.ok(optionValues(filter).some((option) => option.value === "brand-2"));
  filter.props.onChange({ target: { value: "brand-2" } });
  h.queryPages.brands.splice(0, h.queryPages.brands.length, page([], 1, 0));
  tree = h.render();
  assert.deepEqual(optionValues(findSelect(tree, "brand_filter")).filter((option) => option.value), [
    { value: "brand-2", label: "Brand Two" },
  ]);
  assert.equal(h.queryCalls.filter((key) => key[0] === "products" && key[1] === "list").at(-1)[2].brand_id, "brand-2");
  h.queryPages.brands.splice(0, h.queryPages.brands.length, ...brands);

  find(tree, (node) => node.type === "button" && textOf(node).includes("Tạo sản phẩm")).props.onClick();
  tree = h.render();
  find(tree, (node) => node.type === "input" && node.props["aria-label"] === "Tìm thương hiệu đã tải").props.onChange({ target: { value: "" } });
  tree = h.render();
  const productBrand = findSelect(tree, "brand_id");
  assert.ok(optionValues(productBrand).some((option) => option.value === "brand-2"));
  productBrand.props.onChange({ target: { value: "brand-2", selectedOptions: [{ textContent: "Brand Two" }] } });
  find(tree, (node) => node.type === "input" && node.props["aria-label"] === "Tìm thương hiệu đã tải").props.onChange({ target: { value: "no matching brand" } });
  tree = h.render();
  assert.deepEqual(optionValues(findSelect(tree, "brand_id")).filter((option) => option.value), [
    { value: "brand-2", label: "Brand Two" },
  ]);
  assert.ok(optionValues(findSelect(tree, "brand_filter")).some((option) => option.value === "brand-1"), "create-dialog search must not filter toolbar options");
});

test("product edit brand selector keeps the existing brand when page one omits it", async () => {
  const productDetail = { id: "product-1", name: "Product One", brand_id: "brand-2", description: "", context: {}, archived: false, revision: 1 };
  const h = createHarness({ route: "productDetail", productBrand: "brand-2", pageSets: { brands, productDetail } });
  let tree = h.render();
  find(tree, (node) => node.type === "button" && textOf(node).includes("Chỉnh sửa")).props.onClick();
  tree = h.render();
  assert.ok(optionValues(findSelect(tree, "brand_id")).some((option) => option.value === "brand-2"));
  assert.ok(findLoadMore(tree, "Tải thêm thương hiệu trong chỉnh sửa sản phẩm"));

  find(tree, (node) => node.type === "input" && node.props["aria-label"] === "Tìm thương hiệu đã tải").props.onChange({ target: { value: "no matching brand" } });
  tree = h.render();
  assert.deepEqual(optionValues(findSelect(tree, "brand_id")).filter((option) => option.value), [
    { value: "brand-2", label: "Brand Two" },
  ]);
});
