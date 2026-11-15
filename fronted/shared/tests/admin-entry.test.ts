import { afterEach, test } from "node:test";
import assert from "node:assert/strict";

const values = new Map<string, string>(), tabValues = new Map<string, string>();
const storage = (data: Map<string, string>) => ({
  getItem: (name: string) => data.get(name) ?? null,
  setItem: (name: string, value: string) => data.set(name, value),
  removeItem: (name: string) => data.delete(name),
});
const browser = new EventTarget();
const site = { hostname: "admin.esports.club", host: "admin.esports.club", port: "", protocol: "https:", href: "https://admin.esports.club/#/login", search: "", hash: "#/login", assign: (_url: string) => {} };
Object.assign(globalThis, { window: browser, location: site, localStorage: storage(values), sessionStorage: storage(tabValues) });
const { getStoreSlug, isAdminEntry, canonicalizeStoreUrl, storeUrl } = await import("../src/store.ts");
const { getSession, persistSession, clearSession, updateProfile, updateAccessToken } = await import("../src/session.ts");
const { createApiClient, refreshSession } = await import("../src/client.ts");
const { subscribeRealtime } = await import("../src/realtime.ts");
const originalFetch = globalThis.fetch;
const ok = (data: unknown) => new Response(JSON.stringify({ code: 0, data }), { status: 200 });
function login(slug: string, token = `${slug}-access`) {
  persistSession("admin", { store_slug: slug, token, refresh: `${slug}-refresh`, profile: { id: 1, username: "admin", role: { id: 1, code: "staff", name: "店内成员" }, permissions: ["orders:view"] } });
}
afterEach(() => {
  clearSession("admin"); values.clear(); tabValues.clear(); globalThis.fetch = originalFetch;
  Object.assign(site, { hostname: "admin.esports.club", host: "admin.esports.club", port: "", href: "https://admin.esports.club/#/login", search: "", hash: "#/login" });
});

test("neutral admin entry ignores both store selectors and never adopts a default tenant session", async () => {
  site.search = "?store=shenglang"; site.hash = "#/login?store=xinan";
  values.set("xa:fronted:admin:session:v1", JSON.stringify({ token: "legacy-access", user: { id: 1 } }));
  values.set("xa:store:shenglang:xa:fronted:admin:session:v1", JSON.stringify({ token: "default-access", user: { id: 1 } }));
  assert.equal(isAdminEntry(), true); assert.equal(getStoreSlug(), ""); assert.equal(getSession("admin").token, "");
  let requests = 0; globalThis.fetch = (async () => { requests++; return ok({}); }) as typeof fetch;
  await assert.rejects(createApiClient("admin").get("/admin/orders/"), (error: any) => error.status === 401);
  assert.equal(requests, 0, "an anonymous neutral view cannot reach a business database");
});

test("admin login transport omits tenant and authorization headers until the backend identifies the account", async () => {
  let sent: RequestInit | undefined;
  globalThis.fetch = (async (_url, options) => { sent = options; return ok({ requiresOTP: true, otpChallenge: "challenge" }); }) as typeof fetch;
  const result = await createApiClient("admin").post("/admin/auth/login", { username: "admin", password: "test-only" }, { public: true });
  assert.equal(result.requiresOTP, true);
  assert.equal((sent!.headers as Record<string, string>)["X-Store-Slug"], undefined);
  assert.equal((sent!.headers as Record<string, string>).Authorization, undefined);
  assert.equal(getStoreSlug(), ""); assert.equal(getSession("admin").token, "");
});

test("final authenticated login requires an explicit store and stores its credentials only in that tenant", () => {
  assert.throws(() => persistSession("admin", { token: "access", profile: { id: 1 } }), /所属门店/);
  assert.equal(getStoreSlug(), "");
  login("login-store-b");
  assert.equal(getStoreSlug(), "login-store-b");
  assert.equal(getSession("admin").token, "login-store-b-access");
  assert.ok(values.has("xa:store:login-store-b:xa:fronted:admin:session:v1"));
  assert.equal(values.has("xa:store:shenglang:xa:fronted:admin:session:v1"), false);
  assert.equal(tabValues.get("xa:fronted:admin:active-store:v1"), "login-store-b");
  site.search = "?store=wrong"; assert.equal(getStoreSlug(), "login-store-b");
});

test("a reloaded console restores only its tab's saved tenant session", () => {
  tabValues.set("xa:fronted:admin:active-store:v1", "restored-admin");
  values.set("xa:store:restored-admin:xa:fronted:admin:session:v1", JSON.stringify({ token: "restored-access", refresh: "restored-refresh", sessionId: "restored-login", user: { id: 2, role: { id: 1, code: "staff" } } }));
  site.search = "?store=unrelated";
  assert.equal(getStoreSlug(), "restored-admin");
  assert.equal(getSession("admin").token, "restored-access");
  assert.equal(getSession("admin").sessionId, "restored-login");
});

test("authenticated admin reads and websocket subscriptions use the login-selected tenant", async () => {
  login("transport-admin");
  let headers: Record<string, string> = {};
  globalThis.fetch = (async (_url, options) => { headers = options!.headers as Record<string, string>; return ok({ list: [] }); }) as typeof fetch;
  await createApiClient("admin").get("/admin/orders/");
  assert.equal(headers["X-Store-Slug"], "transport-admin"); assert.equal(headers.Authorization, "Bearer transport-admin-access");
  const previous = { document: globalThis.document, WebSocket: globalThis.WebSocket };
  const sockets: FakeSocket[] = [];
  class FakeSocket {
    static OPEN = 1; readyState = 1; url: string;
    onopen = null; onclose = null; onerror = null; onmessage = null;
    constructor(url: string) { this.url = url; sockets.push(this); }
    send() {} close() {}
  }
  Object.assign(globalThis, { document: Object.assign(new EventTarget(), { hidden: false }), WebSocket: FakeSocket });
  let stop = () => {};
  try { stop = subscribeRealtime("admin", () => {}); assert.match(sockets[0].url, /^wss:\/\/admin\.esports\.club\/ws\/orders\/\?store=transport-admin&token=/); }
  finally { stop(); Object.assign(globalThis, previous); }
});

test("same-login real admin role-object profile and token sync keep revision stable across tabs", () => {
  login("stable-admin"); const original = getSession("admin");
  const key = "xa:store:stable-admin:xa:fronted:admin:session:v1";
  values.set(key, JSON.stringify({ ...original, token: "renewed-access", user: { ...original.user, nickname: "Updated" } }));
  const event = new Event("storage"); Object.defineProperty(event, "key", { value: key }); browser.dispatchEvent(event);
  assert.equal(getSession("admin").revision, original.revision);
  assert.equal(getSession("admin").token, "renewed-access");
  updateProfile("admin", { permissions: ["orders:view", "orders:dispatch"] });
  updateAccessToken("admin", "next-access", original.revision);
  assert.equal(getSession("admin").revision, original.revision);
});

test("logout returns to neutral login while other tenant credentials remain isolated", () => {
  login("kept-admin-a"); login("logged-out-admin-b");
  clearSession("admin");
  assert.equal(getStoreSlug(), ""); assert.equal(getSession("admin").token, "");
  assert.equal(tabValues.has("xa:fronted:admin:active-store:v1"), false);
  assert.equal(values.has("xa:store:logged-out-admin-b:xa:fronted:admin:session:v1"), false);
  assert.equal(values.has("xa:store:kept-admin-a:xa:fronted:admin:session:v1"), true);
});

test("external logout clears the current admin binding and invalidates pending tenant reads", async () => {
  login("external-logout-admin");
  let resolve!: (response: Response) => void;
  globalThis.fetch = (() => new Promise<Response>(yes => { resolve = yes; })) as typeof fetch;
  const reading = createApiClient("admin").get("/admin/orders/");
  const key = "xa:store:external-logout-admin:xa:fronted:admin:session:v1";
  values.delete(key);
  const event = new Event("storage"); Object.defineProperty(event, "key", { value: key }); browser.dispatchEvent(event);
  assert.equal(getStoreSlug(), ""); assert.equal(getSession("admin").token, "");
  resolve(ok({ private_order: "old" }));
  await assert.rejects(reading, (error: any) => error.code === "SESSION_CHANGED");
});

test("another admin tab's different-store login leaves this tab bound to its own tenant", () => {
  login("independent-tab-owner"); const original = getSession("admin");
  const key = "xa:store:independent-other-store:xa:fronted:admin:session:v1";
  values.set(key, JSON.stringify({ ...original, token: "other-store-token", sessionId: "other-store-login" }));
  const event = new Event("storage"); Object.defineProperty(event, "key", { value: key }); browser.dispatchEvent(event);
  assert.equal(getStoreSlug(), "independent-tab-owner"); assert.equal(getSession("admin"), original);
});

test("a different-store relogin rejects responses from the previous tenant", async () => {
  login("old-admin-scope");
  let resolve!: (response: Response) => void;
  globalThis.fetch = (() => new Promise<Response>(yes => { resolve = yes; })) as typeof fetch;
  const reading = createApiClient("admin").get("/admin/orders/");
  login("new-admin-scope");
  resolve(ok({ private_order: "old" }));
  await assert.rejects(reading, (error: any) => error.code === "SESSION_CHANGED");
  assert.equal(getStoreSlug(), "new-admin-scope");
});

test("legacy local admin preview retains its existing query-based tenant routing", () => {
  Object.assign(site, { hostname: "127.0.0.1", host: "127.0.0.1:13103", port: "13103", search: "?store=preview-admin" });
  assert.equal(isAdminEntry(), false);
  assert.equal(getStoreSlug(), "preview-admin");
  values.set("xa:store:preview-admin:xa:fronted:admin:session:v1", JSON.stringify({ token: "old-preview-access", refresh: "old-preview-refresh", user: { id: 2 } }));
  assert.equal(getStoreSlug(), "preview-admin"); assert.equal(getSession("admin").token, "old-preview-access");
  site.search = ""; assert.equal(getStoreSlug(), "shenglang", "the old preview keeps its prior routing convention");
});

test("new local admin port and canonical links remain neutral without a verified login", () => {
  Object.assign(site, { hostname: "127.0.0.1", host: "127.0.0.1:14103", port: "14103", search: "?store=shenglang", href: "http://127.0.0.1:14103/?store=shenglang#/login?redirect=%2Forders&store=xinan" });
  assert.equal(getStoreSlug(), "");
  let result = "";
  Object.assign(globalThis, { history: { state: null, replaceState: (_state: unknown, _title: string, url: string) => { result = url; } } });
  canonicalizeStoreUrl(); assert.equal(result, "http://127.0.0.1:14103/#/login?redirect=%2Forders");
  assert.equal(storeUrl("https://admin.esports.club/?store=xinan#/orders", "xinan"), "https://admin.esports.club/#/orders");
});

test("expired access renews within its selected tenant without changing the login revision", async () => {
  login("refresh-admin"); const original = getSession("admin");
  globalThis.fetch = (async (_url, options) => {
    assert.equal((options!.headers as Record<string, string>)["X-Store-Slug"], undefined, "the backend resolves refresh tenancy from its validated credential");
    assert.equal(JSON.parse(String(options!.body)).refresh, "refresh-admin-refresh");
    return ok({ token: "renewed-admin", store_slug: "refresh-admin" });
  }) as typeof fetch;
  assert.equal(await refreshSession("admin"), "renewed-admin");
  assert.equal(getSession("admin").revision, original.revision); assert.equal(getStoreSlug(), "refresh-admin");
});

test("refresh cannot replace an admin login with credentials from another tenant", async () => {
  login("refresh-owner");
  globalThis.fetch = (async () => ok({ token: "other-tenant-access", store_slug: "other-tenant" })) as typeof fetch;
  await assert.rejects(refreshSession("admin"), /门店已变化/);
  assert.equal(getStoreSlug(), ""); assert.equal(getSession("admin").token, "");
});
