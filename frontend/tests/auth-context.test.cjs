const assert = require("node:assert/strict");
const { after, test } = require("node:test");
const { loadTypeScript } = require("./load-typescript.cjs");

const previousWindow = global.window;
after(() => {
  if (previousWindow === undefined) delete global.window;
  else global.window = previousWindow;
});

function user(id) {
  return { id, name: `Editor ${id}`, email: `${id}@example.test`, role: "EDITOR" };
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function createAuthHarness({ getMe, login, logout = async () => {} }) {
  const states = [];
  const refs = [];
  const effects = [];
  const listeners = new Map();
  const storage = new Map();
  const cache = { clears: 0, clear() { this.clears += 1; } };
  const routes = [];
  let stateIndex = 0;
  let refIndex = 0;
  const localStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  };
  global.window = {
    localStorage,
    addEventListener: (name, callback) => listeners.set(name, callback),
    removeEventListener: (name, callback) => {
      if (listeners.get(name) === callback) listeners.delete(name);
    },
  };

  const react = {
    createContext(defaultValue) {
      const context = { value: defaultValue };
      context.Provider = ({ value, children }) => {
        context.value = value;
        return children;
      };
      react._lastContext = context;
      return context;
    },
    useContext: (context) => context.value,
    useState(initial) {
      const index = stateIndex++;
      if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial;
      return [states[index], (value) => {
        states[index] = typeof value === "function" ? value(states[index]) : value;
      }];
    },
    useRef(initial) {
      const index = refIndex++;
      if (!(index in refs)) refs[index] = { current: initial };
      return refs[index];
    },
    useCallback: (callback) => callback,
    useEffect: (effect) => effects.push(effect),
  };
  const csrfState = { token: null };
  class ApiClientError extends Error {
    constructor(status, code) {
      super(code);
      this.status = status;
      this.code = code;
    }
  }
  const mocks = {
    react,
    "react/jsx-runtime": {
      jsx: (type, props) => typeof type === "function" ? type(props) : { type, props },
      jsxs: (type, props) => typeof type === "function" ? type(props) : { type, props },
    },
    "next/navigation": { useRouter: () => ({ replace: (path) => routes.push(path) }) },
    "@tanstack/react-query": { useQueryClient: () => cache },
    "../api/auth": {
      getMe: (...args) => getMe(...args),
      login: (...args) => login(...args),
      logout: (...args) => logout(...args),
    },
    "../api/errors": { ApiClientError, getErrorMessage: (error) => error.message },
    "../api/client": {
      CSRF_STORAGE_KEY: "ai_video_studio_csrf_token",
      AUTH_INVALID_EVENT: "studio:auth-invalid",
      loadCsrfToken: () => csrfState.token,
      persistCsrfToken: (token) => {
        csrfState.token = token;
        if (token) storage.set("ai_video_studio_csrf_token", token);
        else storage.delete("ai_video_studio_csrf_token");
      },
      clearCsrfToken: () => {
        csrfState.token = null;
        storage.delete("ai_video_studio_csrf_token");
      },
      syncCsrfTokenFromStorage: (token) => { csrfState.token = token; },
    },
  };
  const { AuthProvider } = loadTypeScript("lib/auth/auth-context.tsx", mocks);
  function render() {
    stateIndex = 0;
    refIndex = 0;
    AuthProvider({ children: null });
    return AuthProviderContext.value;
  }
  // The mocked createContext returns the same provider context through jsx.
  const AuthProviderContext = react._lastContext;
  return { render, effects, listeners, storage, localStorage, cache, routes, csrfState, ApiClientError };
}

test("stale account response cannot replace a newer login and account changes clear query cache", async () => {
  const oldAccount = deferred();
  const harness = createAuthHarness({
    getMe: () => oldAccount.promise,
    login: async () => ({ user: user("account-b"), csrf_token: "csrf-account-b" }),
  });
  let auth = harness.render();
  const staleRequest = auth.refetchUser();
  await auth.login({ email: "b@example.test", password: "test-password" });

  auth = harness.render();
  assert.equal(auth.user.id, "account-b");
  assert.equal(auth.csrfToken, "csrf-account-b");
  assert.ok(harness.cache.clears > 0);
  assert.deepEqual(harness.routes, ["/dashboard"]);

  oldAccount.resolve(user("account-a"));
  await staleRequest;
  auth = harness.render();
  assert.equal(auth.user.id, "account-b");
});

test("cross-tab CSRF changes refresh the user, clear cached data, and 401 clears auth state", async () => {
  let getMe = async () => user("account-b");
  const harness = createAuthHarness({
    getMe: (...args) => getMe(...args),
    login: async () => ({ user: user("account-a"), csrf_token: "csrf-account-a" }),
  });
  harness.render();
  harness.effects[1]();

  const storageListener = harness.listeners.get("storage");
  await storageListener({
    storageArea: harness.localStorage,
    key: "ai_video_studio_csrf_token",
    newValue: "csrf-account-b",
  });
  await new Promise(setImmediate);
  let auth = harness.render();
  assert.equal(auth.user.id, "account-b");
  assert.equal(auth.csrfToken, "csrf-account-b");
  assert.ok(harness.cache.clears > 0);

  getMe = async () => { throw new harness.ApiClientError(401, "AUTH_REQUIRED"); };
  harness.listeners.get("studio:auth-invalid")();
  await new Promise(setImmediate);
  auth = harness.render();
  assert.equal(auth.user, null);
  assert.equal(auth.csrfToken, null);
  assert.equal(auth.isAuthenticated, false);
  assert.equal(harness.storage.has("ai_video_studio_csrf_token"), false);
});
