// Execute the installed TypeScript compiler's output with explicit boundary fakes.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { createRequire } = require("node:module");
const ts = require("typescript");
const root = path.resolve(__dirname, "..");

function loadTypeScript(entry, mocks = {}) {
  const cache = new Map();
  function load(filename) {
    if (cache.has(filename)) return cache.get(filename).exports;
    const loadedModule = { exports: {} };
    cache.set(filename, loadedModule);
    const nativeRequire = createRequire(filename);
    const requireLocal = (name) => {
      if (Object.hasOwn(mocks, name)) return mocks[name];
      // Handler tests isolate locale; real switching is covered separately.
      if (name === "@/lib/i18n") return { useI18n: () => ({ locale: "vi", t: (vi) => vi, setLocale: () => {} }), translateText: vi => vi, getLocaleSnapshot: () => "vi" };
      if (name === "lucide-react" || name.startsWith("@/components/")) {
        return new Proxy({}, { get: () => () => null });
      }
      if (name.startsWith("@/") || name.startsWith(".")) {
        const base = name.startsWith("@/")
          ? path.join(root, name.slice(2))
          : path.resolve(path.dirname(filename), name);
        const source = [base, `${base}.ts`, `${base}.tsx`].find((p) => fs.existsSync(p) && fs.statSync(p).isFile());
        if (source) return load(source);
      }
      return nativeRequire(name);
    };
    const output = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
      fileName: filename,
    }).outputText;
    vm.runInThisContext(`(function(require, module, exports) {${output}\n})`, { filename })(requireLocal, loadedModule, loadedModule.exports);
    return loadedModule.exports;
  }
  return load(path.join(root, entry));
}

function pageHarness(data, extraMocks = {}) {
  const mutations = [];
  const refs = [];
  let cursor = 0;
  let queryCursor = 0;
  const react = { ...require("react"), use: (p) => p, useState: (v) => [v, () => {}], useMemo: (fn) => fn(), useCallback: (fn) => fn, useRef: (v) => refs[cursor++] ||= { current: v } };
  const mocks = {
    react,
    "@tanstack/react-query": {
      useQuery: () => ({ data: queryCursor++ === 0 ? data : undefined, isLoading: true, refetch: () => {} }),
      useInfiniteQuery: () => ({
        data: { pages: [{ items: [], total: 0, page: 1, page_size: 50 }] },
        isPending: false, isError: false, isFetching: false,
        hasNextPage: false, isFetchingNextPage: false, isFetchNextPageError: false,
        error: undefined, fetchNextPage: async () => {}, refetch: async () => {},
      }),
      useMutation: (options) => { mutations.push(options); return {}; },
      useQueryClient: () => ({ invalidateQueries: () => {} }),
    },
    "react-hook-form": {
      useForm: () => ({ formState: {}, register: () => {}, setValue: () => {}, control: {} }),
      useWatch: () => "",
    },
    "@/lib/hooks/use-video-events": { useVideoEvents: () => ({ generationProgress: {}, sceneActiveGeneration: {} }) },
    ...extraMocks,
  };
  return { mocks, mutations, render: (page, params) => { cursor = 0; queryCursor = 0; mutations.length = 0; page({ params }); } };
}

module.exports = { loadTypeScript, pageHarness };
