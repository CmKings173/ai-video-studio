const assert = require("node:assert/strict");
const test = require("node:test");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const { loadTypeScript } = require("./load-typescript.cjs");

const controls = [
  ["Input", "input"],
  ["Select", "select"],
  ["Textarea", "textarea"],
].map(([name, tag]) => ({
  name,
  tag,
  Component: loadTypeScript(`components/ui/${tag}.tsx`)[name],
}));

function attributes(html, tag) {
  const opening = html.match(new RegExp(`<${tag}\\b[^>]*>`));
  assert.ok(opening, `expected a rendered ${tag}`);
  return Object.fromEntries([...opening[0].matchAll(/([\w-]+)="([^"]*)"/g)]
    .map(([, name, value]) => [name, value]));
}

function markup(Component, props = {}) {
  return renderToStaticMarkup(React.createElement(Component, props));
}

for (const { name, tag, Component } of controls) {
  test(`${name}: field rows do not stretch to match adjacent help text`, () => {
    const html = markup(Component, { id: "field", label: "Title" });
    const wrapper = attributes(html, "div");
    assert.match(wrapper.class, /\bflex\b/);
    assert.match(wrapper.class, /\bflex-col\b/);
    assert.match(wrapper.class, /\bgap-1\.5\b/);
    assert.match(wrapper.class, /\bself-start\b/);
  });
  test(`${name}: label targets the caller's explicit ID`, () => {
    const html = markup(Component, { id: "field", label: "Title" });
    assert.equal(attributes(html, tag).id, "field");
    assert.equal(attributes(html, "label").for, "field");
  });

  test(`${name}: error forces aria-invalid despite caller false`, () => {
    const html = markup(Component, { error: "Required", "aria-invalid": false });
    assert.equal(attributes(html, tag)["aria-invalid"], "true");
  });

  test(`${name}: error describedby preserves all caller help IDs`, () => {
    const html = markup(Component, {
      id: "field", error: "Required", "aria-describedby": "hint format",
    });
    const errorId = attributes(html, "p").id;
    assert.ok(errorId, "error message must have an ID");
    assert.equal(attributes(html, tag)["aria-describedby"], `hint format ${errorId}`);
    assert.match(html, />Vui lòng nhập trường này\.<\/p>/);
  });

  test(`${name}: error alone describes the field`, () => {
    const html = markup(Component, { id: "field", error: "Required" });
    const errorId = attributes(html, "p").id;
    assert.ok(errorId);
    assert.equal(attributes(html, tag)["aria-describedby"], errorId);
  });

  for (const invalid of [false, true, "grammar"]) {
    test(`${name}: no error preserves explicit aria-invalid=${invalid} and help IDs`, () => {
      const html = markup(Component, {
        "aria-invalid": invalid, "aria-describedby": "hint format",
      });
      assert.equal(attributes(html, tag)["aria-invalid"], String(invalid));
      assert.equal(attributes(html, tag)["aria-describedby"], "hint format");
      assert.doesNotMatch(html, /<p\b/);
    });
  }

  test(`${name}: no error adds no validation ARIA attributes`, () => {
    const html = markup(Component, { error: "" });
    assert.equal(attributes(html, tag)["aria-invalid"], undefined);
    assert.equal(attributes(html, tag)["aria-describedby"], undefined);
    assert.doesNotMatch(html, /<p\b/);
  });

  test(`${name}: omitted ID generates label and error associations`, () => {
    const html = markup(Component, { label: "Title", error: "Required" });
    const field = attributes(html, tag);
    assert.ok(field.id, "generated field ID must be nonempty");
    assert.equal(attributes(html, "label").for, field.id);
    assert.ok(attributes(html, "p").id);
    assert.equal(field["aria-describedby"], attributes(html, "p").id);
  });

  test(`${name}: generated ID stays stable when error and explicit ID change`, () => {
    const renders = [];
    function Rerender() {
      const [phase, setPhase] = React.useState(0);
      // A real React render-phase update rerenders this same hook owner.
      const tree = Component.render({
        label: "Title",
        id: phase === 1 ? "caller-id" : undefined,
        error: phase === 2 ? "Required" : undefined,
      }, null);
      renders.push(tree);
      if (phase < 2) setPhase(phase + 1);
      return tree;
    }
    renderToStaticMarkup(React.createElement(Rerender));
    const fields = renders.map((tree) => React.Children.toArray(tree.props.children)
      .find((child) => child.type === tag));
    assert.equal(fields.length, 3);
    assert.ok(fields[0].props.id);
    assert.equal(fields[1].props.id, "caller-id");
    assert.equal(fields[2].props.id, fields[0].props.id);
  });

  test(`${name}: native props, handlers, className and forwarded ref survive`, () => {
    const ref = React.createRef();
    const events = [];
    const onChange = (event) => events.push(event.target.value);
    const onBlur = () => events.push("blur");
    let tree;
    function Inspect() {
      tree = Component.render({
        name: "title", disabled: true, required: true,
        className: "caller-class", onChange, onBlur,
        ...(tag === "select" ? { options: [{ value: "one", label: "One" }] } : {}),
      }, ref);
      return tree;
    }
    const html = renderToStaticMarkup(React.createElement(Inspect));
    const field = React.Children.toArray(tree.props.children).find((child) => child.type === tag);
    assert.equal(field.props.ref, ref);
    assert.equal(field.props.onChange, onChange);
    assert.equal(field.props.onBlur, onBlur);
    assert.equal(field.props.name, "title");
    assert.equal(field.props.disabled, true);
    assert.equal(field.props.required, true);
    assert.match(field.props.className, /caller-class/);
    field.props.onChange({ target: { value: "updated" } });
    field.props.onBlur();
    assert.deepEqual(events, ["updated", "blur"]);
    if (tag === "select") assert.match(html, /<option value="one"[^>]*>One<\/option>/);
  });

  test(`${name}: multiple SSR controls have unique field and error IDs`, () => {
    const html = renderToStaticMarkup(React.createElement("form", null,
      ...[0, 1, 2].map((key) => React.createElement(Component, {
        key, label: "Title", error: "Required",
      }))));
    const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(([, id]) => id);
    assert.equal(ids.length, 6);
    assert.equal(new Set(ids).size, 6);
    const labels = [...html.matchAll(/\bfor="([^"]+)"/g)].map(([, id]) => id);
    const descriptions = [...html.matchAll(/aria-describedby="([^"]+)"/g)].map(([, id]) => id);
    for (const id of [...labels, ...descriptions]) assert.ok(ids.includes(id));
  });
}

test("mixed SSR fields have unique generated IDs across control types", () => {
  const html = renderToStaticMarkup(React.createElement("form", null,
    ...controls.map(({ name, Component }) => React.createElement(Component, {
      key: name, label: name, error: "Required",
    }))));
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(([, id]) => id);
  assert.equal(ids.length, 6);
  assert.equal(new Set(ids).size, 6);
});
