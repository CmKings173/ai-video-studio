const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const postcss = require("postcss");
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const { loadTypeScript } = require("./load-typescript.cjs");
const root = path.resolve(__dirname, "..");
const read = (file) => fs.readFileSync(path.join(root, file), "utf8");
const css = postcss.parse(read("app/globals.css"));

// Evaluate only the media/container rules used by this slice. These are source
// assertions, not browser geometry or overflow measurements.
function declarations(selector, viewport, container = 0) {
  const result = {};
  css.walkRules((rule) => {
    if (!rule.selectors.includes(selector)) return;
    for (let parent = rule.parent; parent && parent.type !== "root"; parent = parent.parent) {
      if (parent.type !== "atrule") continue;
      const match = parent.params.match(/(min|max)-width:\s*(\d+)px/);
      if (!match) continue;
      const width = parent.name === "container" ? container : viewport;
      if (match[1] === "min" ? width < +match[2] : width > +match[2]) return;
    }
    rule.walkDecls((decl) => { result[decl.prop] = decl.value; });
  });
  return result;
}

for (const [width, rail, padding, inner, columns] of [
  [1440, 248, 24, 1144, 2],
  [1280, 248, 24, 984, 2],
  [1024, 64, 24, 912, 1],
  [768, 64, 24, 656, 1],
  [480, 64, 16, 384, 1],
]) {
  test(`source width matrix: ${width}px, left rail ${rail}px, main content ${inner}px`, () => {
    assert.equal(declarations(".studio-shell", width)["grid-template-columns"], `${rail}px minmax(0, 1fr)`);
    assert.equal(declarations(".studio-shell", width)["min-height"], "100dvh");
    assert.equal(declarations(".sidebar", width).position, "sticky");
    assert.equal(declarations(".sidebar", width).height, "100dvh");
    assert.equal(declarations(".sidebar", width)["overflow-y"], "auto");
    assert.equal(declarations(".main-column", width)["min-width"], "0");
    assert.equal(declarations("main", width).padding, `${padding}px`);
    assert.equal(width - rail - padding * 2, inner);
    assert.equal(declarations(".content-grid", width, inner)["grid-template-columns"],
      columns === 2 ? "minmax(0, 1.4fr) minmax(340px, 0.6fr)" : "minmax(0, 1fr)");
    assert.equal(declarations(".sidebar-label", width).display, width <= 1024 ? "none" : undefined);
    assert.equal(declarations(".sidebar nav", width)["grid-template-columns"], undefined, "navigation never becomes a top grid");
  });
}

test("fields adapt to their panel width; scroll containers own overflow", () => {
  assert.equal(declarations(".responsive-field-grid", 1440)["grid-template-columns"],
    "repeat(auto-fit, minmax(min(100%, 220px), 1fr))");
  assert.equal(declarations(".table-scroll", 480)["overflow-x"], "auto");
  assert.equal(declarations(".table-scroll", 480)["max-width"], "100%");
  assert.equal(declarations(".table-scroll table", 480)["min-width"], "560px");
  assert.equal(declarations(".sidebar a:focus-visible", 480).outline, "2px solid var(--accent)");
});

for (const file of ["app/admin/page.tsx", "app/dashboard/page.tsx", "app/projects/[projectId]/page.tsx", "app/products/[productId]/page.tsx"]) {
  test(`${file}: every table has its own focusable scroll region`, () => {
    const source = ts.createSourceFile(file, read(file), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    let tables = 0;
    function visit(node) {
      if (ts.isJsxElement(node) && node.openingElement.tagName.getText(source) === "table") {
        tables++;
        assert.ok(ts.isJsxElement(node.parent));
        const wrapper = node.parent.openingElement.getText(source);
        assert.match(wrapper, /className="table-scroll"/);
        assert.match(wrapper, /tabIndex=\{0\}/);
        assert.match(wrapper, /role="region"/);
        assert.match(wrapper, /aria-label=/);
      }
      ts.forEachChild(node, visit);
    }
    visit(source);
    assert.ok(tables > 0);
  });
}

for (const [file, count] of [["app/videos/[videoId]/page.tsx", 2], ["app/videos/[videoId]/assembly/page.tsx", 1], ["app/assets/upload/page.tsx", 1]]) {
  test(`${file}: fixed two-column forms are retired`, () => {
    const source = read(file);
    assert.doesNotMatch(source, /\bgrid-cols-2\b/);
    assert.equal((source.match(/responsive-field-grid/g) || []).length, count);
  });
}

test("tabs stay native keyboard buttons with separate scrolling and unshrinking labels", () => {
  let selected;
  const { Tabs } = loadTypeScript("components/ui/tabs.tsx");
  const element = Tabs({ tabs: [{ id: "one", label: "First long label" }, { id: "two", label: "Second long label" }], activeTab: "one", onChange: (id) => { selected = id; } });
  assert.match(element.props.className, /flex-nowrap/);
  assert.match(element.props.className, /overflow-x-auto/);
  const buttons = element.props.children;
  for (const button of buttons) {
    assert.equal(button.type, "button");
    assert.equal(button.props.type, "button");
    assert.equal(button.props.tabIndex, undefined, "preserve native Tab/Enter/Space behavior");
    assert.match(button.props.className, /shrink-0/);
    assert.match(button.props.className, /whitespace-nowrap/);
    assert.match(button.props.className, /focus-visible:outline/);
  }
  assert.equal(buttons[0].props["aria-pressed"], true);
  buttons[1].props.onClick();
  assert.equal(selected, "two");
});

for (const isAdmin of [false, true]) {
  test(`shell accessible icon navigation and account, admin=${isAdmin}`, () => {
    const { StudioShell } = loadTypeScript("components/studio-shell.tsx", {
      react: { ...React, useEffect: () => {}, useState: (value) => [value, () => {}] },
      "next/link": ({ children, ...props }) => React.createElement("a", props, children),
      "next/navigation": { usePathname: () => "/videos/video-1/assembly", useRouter: () => ({ replace() {} }) },
      "@/lib/auth/auth-context": { useAuth: () => ({ user: { name: "Editor", email: "editor@example.com", role: isAdmin ? "ADMIN" : "EDITOR" }, isAuthenticated: true, isLoading: false, isAdmin, logout() {} }) },
      "./ui/badge": { Badge: () => null },
    });
    const html = renderToStaticMarkup(React.createElement(StudioShell, null, "Content"));
    // The custom tooltip is exercised separately; SSR retains the link's name/current state.
    assert.match(html, /href="\/videos"[^>]*aria-label="Video"[^>]*aria-current="page"/);
    assert.equal(html.includes('href="/admin"'), isAdmin);
    assert.match(html, /class="sidebar-avatar" role="img" aria-label="Editor/);
    assert.match(html, /title="Đăng xuất"[^>]*aria-label="Đăng xuất"/);
    assert.match(html, /class="sidebar-label"/);
    assert.match(html, /aria-label="Tổng quan AI Video Studio" title="Tổng quan AI Video Studio"/);
  });
}


test("upload panels respond to usable width and wrap unbroken content", () => {
  for (const inner of [384, 656, 912, 984, 1144]) {
    assert.equal(declarations(".upload-layout", 1440, inner)["grid-template-columns"],
      inner >= 960 ? "repeat(2, minmax(0, 1fr))" : "minmax(0, 1fr)");
  }
  assert.equal(declarations(".asset-upload-page", 480)["overflow-wrap"], "anywhere");
  assert.equal(declarations(".asset-upload-page fieldset", 480)["min-width"], "0");
  assert.equal(declarations(".asset-upload-page select", 480)["max-width"], "100%");
  assert.equal(declarations(".upload-asset-row", 480)["flex-wrap"], "wrap");
});

test("failed logout is announced outside the rail with a keyboard dismiss button", async () => {
  let error = null;
  const { StudioShell } = loadTypeScript("components/studio-shell.tsx", {
    react: { ...React, useEffect: () => {}, useState: () => [error, (value) => { error = value; }] },
    "next/link": ({ children, ...props }) => React.createElement("a", props, children),
    "next/navigation": { usePathname: () => "/dashboard", useRouter: () => ({ replace() {} }) },
    "@/lib/auth/auth-context": { useAuth: () => ({ user: { name: "Editor", email: "e@example.com", role: "EDITOR" }, isAuthenticated: true, isLoading: false, isAdmin: false,
      logout: async () => { throw new Error("offline"); } }) },
    "./ui/badge": { Badge: () => null },
  });
  const nodes = (node) => !node || typeof node !== "object" ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)];
  nodes(StudioShell({ children: "Content" })).find((node) => node.props?.["aria-label"] === "Đăng xuất").props.onClick();
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(error);
  const tree = StudioShell({ children: "Content" });
  const aside = nodes(tree).find((node) => node.type === "aside");
  assert.equal(nodes(aside).some((node) => node.props?.role === "alert"), false);
  const main = nodes(tree).find((node) => node.type === "main");
  assert.ok(nodes(main).some((node) => node.props?.role === "alert"));
  const dismiss = nodes(main).find((node) => node.type === "button" && node.props?.["aria-label"] === "Đóng thông báo lỗi đăng xuất");
  assert.ok(dismiss);
  dismiss.props.onClick();
  assert.equal(error, null);
});
