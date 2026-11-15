import { afterEach, test } from "node:test";
import assert from "node:assert/strict";

// Register the actual browser storage-event listener before importing sessions.
const values = new Map<string, string>();
const browser = new EventTarget();
let writes = 0;
Object.assign(globalThis, {
  window: browser,
  location: { search: "?store=tab-store", hash: "" },
  localStorage: {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { writes++; values.set(key, value); },
    removeItem: (key: string) => values.delete(key),
  },
});
const { getSession, persistSession, clearSession, subscribeSession, updateAccessToken, updateProfile } = await import("../src/session.ts");
const { createApiClient } = await import("../src/client.ts");
const originalFetch = globalThis.fetch;
function login() { persistSession("provider", { token: "account-one", refresh: "one-refresh", user: { id: 1, role: "provider" } }); }
function storageEvent(key: string | null) {
  const event = new Event("storage");
  Object.defineProperty(event, "key", { value: key });
  browser.dispatchEvent(event);
}
function sync(role: "admin" | "provider", value: unknown, store = "tab-store") {
  const key = `xa:store:${store}:xa:fronted:${role}:session:v1`;
  if (value === null) values.delete(key);
  else values.set(key, JSON.stringify(value));
  storageEvent(key);
}
function fromOtherTab(store: string, userId: number, token: string) {
  const key = `xa:store:${store}:xa:fronted:provider:session:v1`;
  values.set(key, JSON.stringify({ token, refresh: "other-refresh", user: { id: userId, role: "provider" } }));
  const event = new Event("storage");
  Object.defineProperty(event, "key", { value: key });
  browser.dispatchEvent(event);
}
afterEach(() => {
  globalThis.fetch = originalFetch;
  Object.assign(location, { search: "?store=tab-store", hash: "" });
  clearSession("provider"); clearSession("admin"); values.clear(); writes = 0;
});

test("another store tab changing an overlapping account ID does not affect this store", () => {
  login(); const original = getSession("provider");
  fromOtherTab("different-store", 1, "different-store-token");
  assert.equal(getSession("provider"), original);
  assert.equal(getSession("provider").token, "account-one");
});

test("same-store account changes publish a new revision to invalidate existing views", () => {
  login(); const revision = getSession("provider").revision;
  let notifications = 0; const unsubscribe = subscribeSession("provider", () => notifications++);
  try {
    fromOtherTab("tab-store", 2, "account-two");
    assert.equal(getSession("provider").user?.id, 2);
    assert.equal(getSession("provider").token, "account-two");
    assert.notEqual(getSession("provider").revision, revision);
    assert.equal(notifications, 1);
  } finally { unsubscribe(); }
});

test("a response from before an external same-store login cannot update the new account", async () => {
  login();
  let resolve!: (value: Response) => void;
  const pending = new Promise<Response>((yes) => { resolve = yes; });
  globalThis.fetch = (() => pending) as typeof fetch;
  const reading = createApiClient("provider").get("/wallet/info/");
  fromOtherTab("tab-store", 2, "account-two");
  resolve(new Response(JSON.stringify({ code: 0, data: { balance: 123 } }), { status: 200 }));
  await assert.rejects(reading, (error: any) => error.code === "SESSION_CHANGED");
  assert.equal(getSession("provider").token, "account-two");
});

test("same-login admin profile sync updates permissions without invalidating pending reads", async () => {
  persistSession("admin", { token: "admin-access", refresh: "admin-refresh", user: { id: 1, role: "admin", permissions: ["orders:view"] } });
  const original = getSession("admin");
  let resolve!: (value: Response) => void;
  globalThis.fetch = (() => new Promise<Response>((yes) => { resolve = yes; })) as typeof fetch;
  const reading = createApiClient("admin").get("/admin/orders/");
  let notifications = 0;
  const unsubscribe = subscribeSession("admin", () => notifications++);
  try {
    const updated = { ...original, revision: 90000, user: { ...original.user, nickname: "Updated", permissions: ["orders:view", "orders:dispatch"] } };
    sync("admin", updated);
    assert.equal(getSession("admin").revision, original.revision, "remote revision is local to its tab");
    assert.equal(getSession("admin").sessionId, original.sessionId);
    assert.deepEqual(getSession("admin").user?.permissions, updated.user.permissions);
    assert.equal(notifications, 1);
    sync("admin", updated);
    assert.equal(notifications, 1, "the same profile must not ping-pong between tabs");
    resolve(new Response(JSON.stringify({ code: 0, data: { list: [] } }), { status: 200 }));
    assert.deepEqual(await reading, { list: [] });
  } finally { unsubscribe(); }
});

test("same-login access renewal preserves revision and accepts an already pending read", async () => {
  login(); const original = getSession("provider");
  let resolve!: (value: Response) => void;
  globalThis.fetch = (() => new Promise<Response>((yes) => { resolve = yes; })) as typeof fetch;
  const reading = createApiClient("provider").get("/wallet/info/");
  sync("provider", { ...original, token: "renewed-access" });
  assert.equal(getSession("provider").revision, original.revision);
  assert.equal(getSession("provider").sessionId, original.sessionId);
  assert.equal(getSession("provider").token, "renewed-access");
  resolve(new Response(JSON.stringify({ code: 0, data: { balance: 123 } }), { status: 200 }));
  assert.deepEqual(await reading, { balance: 123 });
  updateAccessToken("provider", "local-renewal", original.revision);
  assert.equal(getSession("provider").revision, original.revision);
  assert.equal(getSession("provider").sessionId, original.sessionId);
});

test("unchanged JSON profiles do not write storage or notify subscribers", () => {
  persistSession("provider", { token: "access", refresh: "refresh", user: {
    id: 1, role: "provider", permissions: ["orders:view"], settings: { notifications: true, labels: ["one", "two"] },
  } });
  const original = getSession("provider"), beforeWrites = writes;
  let notifications = 0; const unsubscribe = subscribeSession("provider", () => notifications++);
  try {
    updateProfile("provider", { permissions: ["orders:view"], settings: { labels: ["one", "two"], notifications: true }, id: 99, role: "customer" });
    assert.equal(getSession("provider"), original);
    assert.equal(writes, beforeWrites);
    assert.equal(notifications, 0);
    updateProfile("provider", { permissions: ["orders:view", "orders:grab"] });
    assert.equal(writes, beforeWrites + 1);
    assert.equal(notifications, 1);
    assert.equal(getSession("provider").revision, original.revision);
    assert.equal(getSession("provider").user?.id, 1);
    assert.equal(getSession("provider").user?.role, "provider");
  } finally { unsubscribe(); }
});

test("every explicit login receives a fresh persisted login ID even for identical credentials", () => {
  login(); const original = getSession("provider");
  login(); const next = getSession("provider");
  assert.notEqual(next.sessionId, original.sessionId);
  assert.notEqual(next.revision, original.revision);
  assert.equal(JSON.parse(values.get("xa:store:tab-store:xa:fronted:provider:session:v1")!).sessionId, next.sessionId);
});

test("same-user external relogin invalidates old requests even when credentials are unchanged", async () => {
  login(); const original = getSession("provider");
  let resolve!: (value: Response) => void;
  globalThis.fetch = (() => new Promise<Response>((yes) => { resolve = yes; })) as typeof fetch;
  const reading = createApiClient("provider").get("/wallet/info/");
  sync("provider", { ...original, sessionId: `${original.sessionId}-new-login` });
  assert.notEqual(getSession("provider").revision, original.revision);
  assert.throws(() => updateAccessToken("provider", "late-access", original.revision), (error: any) => error.code === "SESSION_CHANGED");
  resolve(new Response(JSON.stringify({ code: 0, data: { balance: 123 } }), { status: 200 }));
  await assert.rejects(reading, (error: any) => error.code === "SESSION_CHANGED");
});

test("a matching login ID cannot hide a changed account ID or role", () => {
  login(); const original = getSession("provider");
  sync("provider", { ...original, user: { id: 2, role: "provider" } });
  const changedAccount = getSession("provider");
  assert.notEqual(changedAccount.revision, original.revision);
  sync("provider", { ...changedAccount, user: { id: 2, role: "customer" } });
  assert.notEqual(getSession("provider").revision, changedAccount.revision);
});

test("external logout and storage clear invalidate the current login", () => {
  login(); const original = getSession("provider");
  sync("provider", null);
  assert.equal(getSession("provider").token, "");
  assert.equal(getSession("provider").sessionId, "");
  assert.notEqual(getSession("provider").revision, original.revision);
  login(); const second = getSession("provider");
  values.clear(); storageEvent(null);
  assert.equal(getSession("provider").user, null);
  assert.notEqual(getSession("provider").revision, second.revision);
});

test("another store cannot alter this login even with the same account and login IDs", () => {
  login(); const original = getSession("provider");
  sync("provider", { ...original, token: "other-store-access", user: { ...original.user, nickname: "Other store" } }, "different-store");
  assert.equal(getSession("provider"), original);
});

test("old session records gain a stable login ID and legacy renewals keep it only for the same refresh identity", () => {
  Object.assign(location, { search: "?store=legacy-session-tabs" });
  const old = { token: "legacy-access", refresh: "legacy-refresh", user: { id: 1, role: "provider" } };
  const key = "xa:store:legacy-session-tabs:xa:fronted:provider:session:v1";
  values.set(key, JSON.stringify(old));
  const original = getSession("provider");
  assert.ok(original.sessionId);
  assert.equal(JSON.parse(values.get(key)!).sessionId, original.sessionId);
  sync("provider", { ...old, token: "legacy-renewed", user: { ...old.user, nickname: "Legacy tab" } }, "legacy-session-tabs");
  assert.equal(getSession("provider").revision, original.revision);
  assert.equal(getSession("provider").sessionId, original.sessionId);
  sync("provider", { ...old, refresh: "different-login-refresh" }, "legacy-session-tabs");
  assert.notEqual(getSession("provider").revision, original.revision);
  clearSession("provider");
});
