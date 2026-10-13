const assert = require("node:assert/strict");
const test = require("node:test");
const { loadTypeScript } = require("./load-typescript.cjs");

test("auth client keeps JWT in the browser cookie and sends the matching CSRF header", async () => {
  const previousWindow = global.window;
  const previousFetch = global.fetch;
  const stored = new Map();
  const requests = [];
  global.window = {
    localStorage: {
      getItem: (key) => stored.get(key) ?? null,
      setItem: (key, value) => stored.set(key, value),
      removeItem: (key) => stored.delete(key),
    },
    dispatchEvent: () => true,
  };
  global.fetch = async (url, init) => {
    requests.push({ url, ...init });
    if (url.endsWith("/auth/login")) {
      return new Response(JSON.stringify({
        user: { id: "user-1", name: "Editor", email: "editor@example.test", role: "EDITOR" },
        csrf_token: "csrf-token-for-the-current-signed-jwt",
      }), {
        status: 200,
        headers: {
          "content-type": "application/json",
          "set-cookie": "studio_session=header.jwt.signature; HttpOnly; SameSite=Strict",
        },
      });
    }
    if (url.endsWith("/auth/logout")) return new Response(null, { status: 204 });
    return new Response(JSON.stringify({
      id: "user-1", name: "Editor", email: "editor@example.test", role: "EDITOR",
    }), { status: 200, headers: { "content-type": "application/json" } });
  };

  try {
    const { login, logout, getMe } = loadTypeScript("lib/api/auth.ts");
    const loginResult = await login({ email: "editor@example.test", password: "example-password" });
    await logout();
    const user = await getMe();

    assert.equal(loginResult.user.id, "user-1");
    assert.equal(user.id, "user-1");
    assert.equal(requests[0].credentials, "include");
    assert.equal(requests[1].credentials, "include");
    assert.equal(requests[2].credentials, "include");
    assert.equal(requests[1].headers["X-CSRF-Token"], "csrf-token-for-the-current-signed-jwt");
    assert.equal(requests[2].headers["X-CSRF-Token"], undefined);
    assert.equal(requests.every(({ headers }) => !Object.hasOwn(headers, "Authorization")), true);
    assert.deepEqual([...stored.keys()], ["ai_video_studio_csrf_token"]);
    assert.equal([...stored.values()].includes("header.jwt.signature"), false);
  } finally {
    global.fetch = previousFetch;
    if (previousWindow === undefined) delete global.window;
    else global.window = previousWindow;
  }
});
