const assert = require("node:assert/strict");
const test = require("node:test");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const postcss = require("postcss");
const tailwind = require("tailwindcss");
const { loadTypeScript } = require("./load-typescript.cjs");

const Button = loadTypeScript("components/ui/button.tsx").Button;
const fields = Object.fromEntries(["input", "select", "textarea", "alert"].map((name) => [
  `@/components/ui/${name}`, loadTypeScript(`components/ui/${name}.tsx`, {
    react: { ...React, useId: () => `fixture-${name}` },
  }),
]));

function fixture(route, fetching = null) {
  const calls = { projects: 0, products: 0, videos: 0 };
  const queryUses = { projects: 0, products: 0, videos: 0 };
  const queries = Object.fromEntries(Object.keys(calls).map((name) => [name, {
    error: new Error(`${name} unavailable`), isLoading: false,
    isFetching: fetching === name, hasNextPage: false,
    refetch: () => { calls[name]++; },
  }]));
  const Page = loadTypeScript(route, {
    react: { ...React, useState: (initial) => [initial, () => {}], useEffect: () => {} },
    "next/link": ({ children, ...props }) => React.createElement("a", props, children),
    "next/navigation": { useRouter: () => ({ push() {} }), useSearchParams: () => ({ get: () => null }) },
    "@tanstack/react-query": {
      useInfiniteQuery: ({ queryKey }) => {
        const name = queryKey[0];
        queryUses[name]++;
        return queries[name];
      },
      useQuery: () => { queryUses.videos++; return queries.videos; },
      useMutation: () => ({}), useQueryClient: () => ({ invalidateQueries() {} }),
    },
    "react-hook-form": {
      useForm: () => ({ register: (name) => ({ name }), control: {}, handleSubmit: () => () => {},
        setValue() {}, formState: { errors: {}, isSubmitting: false } }),
      useWatch: ({ name }) => name === "kind" ? "QUICK_CLIP" : "",
    },
    ...fields,
    "@/components/ui/button": { Button },
  }).default;
  const retries = [];
  const sharedRetries = [];
  const textOf = (children) => React.Children.toArray(children).map((child) =>
    typeof child === "string" || typeof child === "number" ? String(child) : ""
  ).join("");
  // Resolve actual shared forwardRef components, keeping their emitted HTML and handlers.
  function resolve(node) {
    if (Array.isArray(node)) return React.Children.toArray(node).map(resolve);
    if (!React.isValidElement(node)) return node;
    if (node.type === Button && ["Thử lại", "Đang tải..."].includes(textOf(node.props.children))) sharedRetries.push(node);
    if (typeof node.type === "function") return resolve(node.type(node.props));
    if (node.type?.render) return resolve(node.type.render(node.props, null));
    if (typeof node.type !== "string") return resolve(node.props.children);
    const props = { ...node.props, key: node.key };
    if (node.type === "button" && ["Thử lại", "Đang tải..."].includes(textOf(props.children))) {
      props["data-retry"] = String(retries.length);
      retries.push(props);
    }
    return React.createElement(node.type, props, resolve(props.children));
  }
  const html = renderToStaticMarkup(resolve(React.createElement(Page)));
  return { html, retries, sharedRetries, calls, queryUses };
}

function retryHTML(h, index) {
  return [...h.html.matchAll(/<button\b[^>]*>/g)].map(([tag]) => tag)
    .find((tag) => tag.includes(`data-retry="${index}"`));
}

async function declarations(h, retry) {
  const { root } = await postcss([tailwind({ content: [{ raw: h.html, extension: "html" }] })])
    .process("@tailwind utilities;", { from: undefined });
  const selectors = retry.className.split(/\s+/).map((token) =>
    "." + token.replace(/([^a-zA-Z0-9_-])/g, "\\$1")
  );
  const result = {};
  root.walkRules((rule) => {
    if (selectors.includes(rule.selector)) rule.walkDecls((decl) => {
      result[decl.prop] = decl.value;
    });
  });
  return result;
}

test("rendered native format and status filters emit distinct accessible names", () => {
  const { html } = fixture("app/videos/page.tsx");
  const selects = [...html.matchAll(/<select\b[^>]*>/g)].map(([tag]) => tag);
  assert.equal(selects.length, 4);
  for (const name of ["Lọc theo định dạng", "Lọc theo trạng thái", "Lọc theo dự án", "Lọc theo sản phẩm"]) {
    assert.equal(selects.filter((tag) => tag.includes(`aria-label="${name}"`)).length, 1, name);
  }
});

for (const route of ["app/videos/page.tsx", "app/videos/new/page.tsx"]) {
  const resources = route.includes("/new/") ? ["projects"] : ["projects", "products", "videos"];
  for (const [index, resource] of resources.entries()) {
    test(`${route}: ${resource} retry emits shared Button min-height >=36px and calls only its query`, async () => {
      const h = fixture(route);
      assert.equal(h.sharedRetries.length, resources.length, "every retry uses the real shared Button");
      assert.equal(h.retries.length, resources.length);
      const retry = h.retries[index];
      assert.equal(retry.type, "button", "retry must not submit the create form");
      assert.match(retryHTML(h, index), /\btype="button"/);
      const css = await declarations(h, retry);
      assert.ok(parseFloat(css["min-height"]) >= 36 && css["min-height"].endsWith("px"), JSON.stringify(css));
      // The real md Button emits 0.875rem on each side: padding alone exceeds
      // the 24px target width at the default 16px root size. Main owns DOM geometry.
      const px = (value) => value?.endsWith("rem") ? parseFloat(value) * 16 : value?.endsWith("px") ? parseFloat(value) : NaN;
      assert.ok(px(css["padding-left"]) + px(css["padding-right"]) >= 24, JSON.stringify(css));
      assert.equal(Boolean(retry.disabled), false);
      assert.doesNotMatch(retryHTML(h, index), /\bdisabled(?:=|\s|>)/);
      await retry.onClick();
      assert.deepEqual(h.calls, { projects: Number(resource === "projects"), products: Number(resource === "products"), videos: Number(resource === "videos") });
    });

    test(`${route}: ${resource} retry is disabled only while its query fetches`, async () => {
      const h = fixture(route, resource);
      assert.equal(h.sharedRetries.length, resources.length);
      assert.equal(h.retries[index].disabled, true);
      assert.match(retryHTML(h, index), /\bdisabled=""/);
      assert.deepEqual(h.calls, { projects: 0, products: 0, videos: 0 });
      for (const other of resources.keys()) {
        if (other === index) continue;
        assert.equal(Boolean(h.retries[other].disabled), false);
        assert.doesNotMatch(retryHTML(h, other), /\bdisabled(?:=|\s|>)/);
        await h.retries[other].onClick();
      }
      assert.deepEqual(h.calls, Object.fromEntries(Object.keys(h.calls).map((name) =>
        [name, Number(resources.includes(name) && name !== resource)]
      )));
      const recovered = fixture(route);
      assert.equal(Boolean(recovered.retries[index].disabled), false);
      assert.doesNotMatch(retryHTML(recovered, index), /\bdisabled(?:=|\s|>)/);
      await recovered.retries[index].onClick();
      assert.deepEqual(recovered.calls, { projects: Number(resource === "projects"), products: Number(resource === "products"), videos: Number(resource === "videos") });
    });
  }
}

test("create video keeps the required project association and omits the optional product field/query", () => {
  const h = fixture("app/videos/new/page.tsx");
  assert.match(h.html, /name="project_id"/);
  assert.doesNotMatch(h.html, /product_id|Sản phẩm liên kết|Linked product/);
  assert.equal(h.queryUses.projects, 1);
  assert.equal(h.queryUses.products, 0);
});
