const assert = require("node:assert/strict");
const { test } = require("node:test");
const { loadTypeScript } = require("./load-typescript.cjs");

function harness(previewOverrides = {}) {
  const states = [], refs = [], invalidations = [], mutations = [];
  let stateCursor = 0, refCursor = 0, effects = [], operation, completed = 0, queryOptions;
  const react = {
    ...require("react"),
    useCallback: callback => callback,
    useState(initial) {
      const index = stateCursor++;
      if (!(index in states)) states[index] = initial;
      return [states[index], value => { states[index] = value; }];
    },
    useRef(initial) { return refs[refCursor++] ||= { current: initial }; },
    useEffect(effect) { effects.push(effect); },
  };
  function Alert() {}
  const { DeletionControl } = loadTypeScript("components/deletion/deletion-control.tsx", {
    react,
    "@/components/ui/alert": { Alert },
    "@/lib/api/deletions": {},
    "@tanstack/react-query": {
      useQueryClient: () => ({ invalidateQueries: async ({ queryKey }) => { invalidations.push(queryKey); } }),
      useMutation(options) {
        mutations.push(options);
        return { isSuccess: mutations.length === 2 && !!operation, reset() {}, mutate() {} };
      },
      useQuery(options) { queryOptions = options; return { data: operation }; },
    },
  });
  const props = {
    resource: { type: "video", id: "video-a", name: "Test video" },
    invalidationKeys: [["videos"], ["video", "video-a"]],
    onComplete: () => { completed++; },
  };
  function render() {
    stateCursor = refCursor = 0; effects = []; mutations.length = 0;
    const tree = DeletionControl(props);
    for (const effect of effects) effect();
    return tree;
  }
  render();
  mutations[0].onSuccess({ resource_type: "VIDEO", resource_id: "video-a", revision: 1,
    allowed: true, async_cleanup: true, cleanup_eligible_outputs: 1, ...previewOverrides });
  render();
  return {
    accept(value) { operation = value; mutations[1].onSuccess(value); return render(); },
    update(value) { operation = value; return render(); },
    invalidations, completed: () => completed,
    polling: () => queryOptions.refetchInterval({ state: { data: operation } }),
    alerts(tree) {
      const found = [];
      function visit(node) {
        if (Array.isArray(node)) return node.forEach(visit);
        if (!node || typeof node !== "object") return;
        if (node.type === Alert) found.push(node.props);
        visit(node.props?.children);
      }
      visit(tree); return found;
    },
  };
}

function dto(status, code = null) {
  return { id: "operation-a", resource_type: "VIDEO", resource_id: "video-a", status,
    cleanup_status: status, cleanup_error_code: code, error_code: code };
}

test("normal retention wait shows pending warning rather than a cleanup failure", () => {
  const h = harness();
  const tree = h.accept(dto("CLEANUP_PENDING", "RETENTION_PENDING"));
  const alerts = h.alerts(tree);
  assert.ok(alerts.some(alert => alert.variant === "warning" && /dọn tệp vẫn đang chờ/.test(alert.title)));
  assert.ok(!alerts.some(alert => alert.variant === "destructive"));
  assert.equal(h.polling(), 2000);
});

for (const status of ["WAITING_FOR_DEPENDENCIES", "LOGICALLY_DELETED", "CLEANUP_PENDING", "CLEANUP_FAILED", "CLEANUP_BLOCKED", "CLEANUP_COMPLETE"]) {
  test(`${status} applies logical deletion once without waiting for file cleanup`, () => {
    const h = harness();
    h.accept(dto(status, status === "CLEANUP_PENDING" ? "RETENTION_PENDING" : null));
    assert.equal(h.completed(), 1);
    assert.deepEqual(h.invalidations, [["videos"], ["video", "video-a"]]);
    h.update(dto("CLEANUP_COMPLETE"));
    h.update(dto("CLEANUP_COMPLETE"));
    assert.equal(h.completed(), 1);
    assert.equal(h.invalidations.length, 2);
    assert.equal(h.polling(), false);
  });
}

test("blocked and failed cleanup remain visible problems with retry polling", () => {
  for (const status of ["CLEANUP_FAILED", "CLEANUP_BLOCKED"]) {
    const h = harness();
    const tree = h.accept(dto(status, "OUTPUT_PROVENANCE_UNRESOLVED"));
    assert.ok(h.alerts(tree).some(alert => alert.variant === "destructive"));
    assert.equal(h.polling(), 30000);
  }
});

test("unaccepted or mismatched operation cannot report logical deletion completion", () => {
  for (const status of ["DELETE_REQUESTED", "UNKNOWN_STATUS"]) {
    const pending = harness();
    pending.accept(dto(status));
    assert.equal(pending.completed(), 0);
    assert.deepEqual(pending.invalidations, []);
  }
  const mismatch = harness();
  mismatch.accept({ ...dto("LOGICALLY_DELETED"), resource_id: "other-video" });
  assert.equal(mismatch.completed(), 0);
});

test("preview counts and 100 percent progress cannot certify pending cleanup complete", () => {
  const h = harness({ cleanup_eligible_outputs: 0 });
  const tree = h.accept({ ...dto("CLEANUP_PENDING"), cleanup_progress_percent: 100 });
  assert.ok(h.alerts(tree).some(alert => alert.variant === "warning"));
  assert.equal(h.polling(), 2000);
});
