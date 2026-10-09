const assert = require("node:assert/strict");
const test = require("node:test");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const postcss = require("postcss");
const tailwind = require("tailwindcss");
const { loadTypeScript } = require("./load-typescript.cjs");

function fixture() {
  const title = "Video" + "UnbrokenTitle".repeat(80);
  const name = "UnbrokenAssociation".repeat(60);
  let resourceCursor = 0;
  const Link = ({ children, ...props }) => React.createElement("a", props, children);
  const Page = loadTypeScript("app/videos/page.tsx", {
    react: { ...React, useState: (value) => [value, () => {}] },
    "next/link": Link,
    "@tanstack/react-query": {
      useInfiniteQuery: () => ({
        data: { pages: [{ items: [resourceCursor++ === 0 ? { id: "project", name } : { id: "product", name }], total: 1, page: 1, page_size: 50 }] },
        isLoading: false, error: null, isFetchingNextPage: false, isFetchNextPageError: false,
        hasNextPage: false, fetchNextPage: async () => {}, refetch: async () => {},
      }),
      useQuery: () => ({ data: { items: [{ id: "video", title, project_id: "project", product_id: "product", status: "STORYBOARD_READY", kind: "LONG_VIDEO", aspect_ratio: "9:16", target_duration: 60 }] }, isLoading: false }),
    },
    "@/components/page-kit": {
      PageHeader: ({ children }) => React.createElement("header", null, children),
      StatusPill: ({ status }) => React.createElement("span", null, status),
    },
  }).default;
  const tree = Page();
  const nodes = (node) => !node || typeof node !== "object" ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)];
  const row = nodes(tree).find((node) => node.props?.className?.split(" ").includes("row"));
  const titleLink = nodes(row).find((node) => node.props?.children === title);
  const actions = nodes(row).find((node) => React.Children.toArray(node.props?.children).some((child) => child.props?.href === "/videos/video/assembly"));
  return { tree, row, titleLink, actions, title, name };
}

test("videos list long-name fixture retains status and usable action links in a wrapping group", () => {
  const { tree, actions, title, name } = fixture();
  const html = renderToStaticMarkup(tree);
  assert.ok(html.includes(title));
  assert.ok(html.includes(name));
  assert.match(html, /STORYBOARD_READY/);
  const links = React.Children.toArray(actions.props.children).filter((node) => node.props?.href);
  assert.deepEqual(links.map((node) => [node.props.href, node.props.children]), [
    ["/videos/video", "Bảng phân cảnh"], ["/videos/video/assembly", "Ghép & xuất video"],
  ]);
  assert.ok(actions.props.className.split(" ").includes("flex-wrap"), "the action group itself must wrap, not just its links");
});

test("rendered videos row emits Tailwind CSS that wraps actions and constrains long titles", async () => {
  const { tree, row, titleLink, actions } = fixture();
  const html = renderToStaticMarkup(tree);
  const { root } = await postcss([tailwind({ content: [{ raw: html, extension: "html" }], corePlugins: { preflight: false } })]).process("@tailwind utilities;", { from: undefined });
  function declarations(node, viewport) {
    const result = {};
    const selectors = node.props.className.split(" ").map((token) => "." + token.replaceAll(":", "\\:"));
    root.walkRules((rule) => {
      if (!selectors.includes(rule.selector)) return;
      for (let parent = rule.parent; parent && parent.type !== "root"; parent = parent.parent) {
        const min = parent.type === "atrule" && parent.params.match(/min-width:\s*(\d+)px/);
        if (min && viewport < Number(min[1])) return;
      }
      rule.walkDecls((decl) => { result[decl.prop] = decl.value; });
    });
    return result;
  }
  for (const viewport of [1440, 1280, 1024, 768, 480]) {
    assert.equal(declarations(row, viewport)["flex-wrap"], "wrap");
    assert.equal(declarations(row, viewport)["flex-direction"], viewport >= 640 ? "row" : "column");
    assert.equal(declarations(actions, viewport)["flex-wrap"], "wrap");
    assert.equal(declarations(actions, viewport)["max-width"], "100%");
    assert.equal(declarations(actions, viewport)["flex-shrink"], "0");
    const info = React.Children.toArray(row.props.children)[0];
    assert.equal(declarations(info, viewport).flex, viewport >= 640 ? "1 1 0%" : undefined);
    assert.equal(declarations(info, viewport)["min-width"], "0px");
    assert.equal(declarations(info, viewport)["flex-basis"], viewport >= 640 ? "16rem" : undefined);
    assert.equal(declarations(titleLink, viewport)["min-width"], "0px");
    assert.equal(declarations(titleLink, viewport)["max-width"], "100%");
  }
  // This checks emitted CSS and rendered content; Pasteur owns actual browser geometry.
});
