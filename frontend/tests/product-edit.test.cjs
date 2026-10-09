const assert = require("node:assert/strict");
const test = require("node:test");
const { loadTypeScript, pageHarness } = require("./load-typescript.cjs");

for (const [label, tone] of [["rename", "warm"], ["tone update", "cool"], ["empty tone", ""]]) {
  test(`product ${label} preserves unrelated context and revision`, async () => {
    const context = { tone: "warm", audience: ["creators"], nested: { keep: true }, flag: false };
    const product = { id: "product-1", revision: 7, context };
    let sent;
    const harness = pageHarness(product, {
      "@/lib/api/products": { patchProduct: async (...args) => { sent = args; } },
    });
    const page = loadTypeScript("app/products/[productId]/page.tsx", harness.mocks).default;
    harness.render((props) => { const body = page(props); return body.type(body.props); }, { productId: product.id });
    await harness.mutations[0].mutationFn({ name: "Renamed", description: "desc", brand_id: "", tone });
    const expected = { ...context };
    if (tone) expected.tone = tone; else delete expected.tone;
    assert.deepEqual(sent, [product.id, { name: "Renamed", description: "desc", brand_id: null, context: expected }, 7]);
    assert.equal(context.tone, "warm", "cached context must not be mutated");
  });
}
