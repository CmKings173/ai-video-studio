const assert = require("node:assert/strict");
const test = require("node:test");
const { loadTypeScript } = require("./load-typescript.cjs");
const { NextRequest } = require("next/server");

const wrappers = [
  ["videos", "listVideos", [], { search: "a & b", project_id: "project", status: "READY" }],
  ["projects", "listProjects", [], { search: "campaign", archived: false }],
  ["products", "listProducts", [], { search: "product", archived: true, brand_id: "brand" }],
  ["assets", "listAssets", [], { project_id: "project", product_id: "product", status: "READY", kind: "IMAGE" }],
  ["brands", "listBrands", [], { search: "brand", archived: false }],
  ["generations", "listSceneGenerations", ["scene"], {}],
  ["admin", "listUsers", [], {}],
  ["admin", "listWorkflows", [], {}],
  ["projects", "getProjectVideos", ["project"], {}],
  ["projects", "getProjectAssets", ["project"], {}],
  ["products", "getProductAssets", ["product"], {}],
];

for (const [module, name, prefix, filters] of wrappers) {
  for (const pagination of [{ page: 1, size: 5 }, { page: 2, size: 50 }, { page: 1 }]) {
    test(`${name} transmits canonical pagination ${JSON.stringify(pagination)}`, async (t) => {
      const originalFetch = global.fetch;
      t.after(() => { global.fetch = originalFetch; });
      const urls = [];
      global.fetch = async (url) => {
        const parsed = new URL(url, "http://fixture.test");
        urls.push(parsed);
        return Response.json({ items: [], total: 101,
          page: Number(parsed.searchParams.get("page") ?? 1),
          page_size: Number(parsed.searchParams.get("page_size") ?? 20) });
      };
      const wrapper = loadTypeScript(`lib/api/${module}.ts`)[name];
      const input = { ...pagination, ...filters };
      const result = await wrapper(...prefix, input);
      assert.equal(urls.length, 1);
      assert.equal(urls[0].searchParams.has("size"), false, "internal size must never reach HTTP");
      assert.equal(urls[0].searchParams.get("page"), String(pagination.page));
      assert.equal(urls[0].searchParams.get("page_size"), pagination.size === undefined ? null : String(pagination.size));
      assert.equal(result.page_size, pagination.size ?? 20);
      for (const [key, value] of Object.entries(filters)) assert.equal(urls[0].searchParams.get(key), String(value));
      assert.deepEqual(input, { ...pagination, ...filters }, "normalization must not mutate caller filters");
    });
  }
}

test("Next proxy preserves canonical pagination and unrelated query parameters", async (t) => {
  const originalFetch = global.fetch;
  t.after(() => { global.fetch = originalFetch; });
  const urls = [];
  global.fetch = async (url) => { urls.push(new URL(url)); return Response.json({ items: [] }); };
  const { GET } = loadTypeScript("app/api/[...path]/route.ts");
  const response = await GET(new NextRequest("http://fixture.test/api/v1/videos?page=2&page_size=5&search=a%20%26%20b&project_id=project"), {
    params: Promise.resolve({ path: ["v1", "videos"] }),
  });
  assert.equal(response.status, 200);
  assert.equal(urls.length, 1);
  assert.equal(urls[0].searchParams.get("page_size"), "5");
  assert.equal(urls[0].searchParams.get("page"), "2");
  assert.equal(urls[0].searchParams.get("search"), "a & b");
  assert.equal(urls[0].searchParams.get("project_id"), "project");
  assert.equal(urls[0].searchParams.has("size"), false);
});

test("pagination sizes respect backend bounds without mutating filters", async (t) => {
  const originalFetch = global.fetch;
  t.after(() => { global.fetch = originalFetch; });
  const urls = [];
  global.fetch = async (url) => { urls.push(new URL(url, "http://fixture.test")); return Response.json({}); };
  const { listVideos } = loadTypeScript("lib/api/videos.ts");
  for (const size of [1, 100]) await listVideos({ page: 1, size });
  assert.deepEqual(urls.map((url) => url.searchParams.get("page_size")), ["1", "100"]);
  for (const size of [0, -1, 101, 1.5, NaN, Infinity]) {
    await assert.rejects(listVideos({ size }), RangeError);
  }
  assert.equal(urls.length, 2, "invalid sizes must fail before transport");
  await listVideos();
  assert.equal(urls.at(-1).search, "", "omission preserves backend defaults");
  await listVideos({ search: undefined, project_id: "p", size: 5 });
  assert.equal(urls.at(-1).searchParams.has("search"), false);
});
