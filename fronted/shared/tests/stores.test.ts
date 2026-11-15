import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { createApiClient, fundRequest, refreshSession } from "../src/client.ts";
import { clearSession, getSession, persistSession } from "../src/session.ts";
import { subscribeRealtime } from "../src/realtime.ts";
import { adoptLegacyStoreStorage, getStoreSlug, storeStorageKey, storeUrl, switchStore } from "../src/store.ts";
import { privateMediaTicket } from "../src/media.ts";

const values = new Map<string, string>();
const storage = {
  get length() { return values.size; },
  key: (index: number) => [...values.keys()][index] ?? null,
  getItem: (key: string) => values.get(key) ?? null,
  setItem: (key: string, value: string) => values.set(key, value),
  removeItem: (key: string) => values.delete(key),
};
Object.assign(globalThis, { localStorage: storage, sessionStorage: storage });
const originalFetch = globalThis.fetch;
const site = { href: "http://localhost/?store=alpha#/orders?id=7", search: "?store=alpha", hash: "#/orders?id=7", protocol: "http:", host: "localhost", assign: (_url: string) => {} };
Object.assign(globalThis, { location: site });
function select(slug: string) { site.search = `?store=${slug}`; site.href = `http://localhost/${site.search}${site.hash}`; }
function login(token: string) { persistSession("provider", { token, refresh: `${token}-refresh`, userInfo: { id: 1, role: "provider" } }); }
const ok = (data: unknown, status = 200) => new Response(JSON.stringify({ code: status === 200 ? 0 : status, data }), { status });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((yes) => { resolve = yes; }); return { promise, resolve }; }
afterEach(() => { globalThis.fetch = originalFetch; values.clear(); site.hash = "#/orders?id=7"; select("alpha"); });

test("same numeric account IDs keep separate store sessions and logout", () => {
  select("sessions-a"); login("a-token");
  select("sessions-b"); assert.equal(getSession("provider").token, ""); login("b-token");
  assert.equal(getSession("provider").token, "b-token");
  clearSession("provider");
  select("sessions-a"); assert.equal(getSession("provider").token, "a-token");
  assert.ok([...values.keys()].some((key) => key.includes("sessions-a")));
});

test("public, authenticated and multipart requests all carry the selected store", async () => {
  select("transport"); login("transport-token");
  const headers: Record<string, string>[] = [];
  globalThis.fetch = (async (_url, options) => { headers.push(options!.headers as Record<string, string>); return ok({}); }) as typeof fetch;
  const api = createApiClient("provider");
  await api.get("/users/me/");
  await api.get("/stores/current/", undefined, { public: true });
  await api.upload("/users/upload/", new FormData());
  assert.deepEqual(headers.map((value) => value["X-Store-Slug"]), ["transport", "transport", "transport"]);
  assert.equal(headers[0].Authorization, "Bearer transport-token");
  assert.equal(headers[1].Authorization, undefined);
  assert.equal(headers[2]["Content-Type"], undefined);
});

test("refresh uses its source store and cannot overwrite another store after JSON decoding", async () => {
  select("refresh-a"); login("expired-a");
  const decoded = deferred<any>();
  let sentHeader: any;
  globalThis.fetch = (async (_url, options) => { sentHeader = options!.headers; return { status: 401, ok: false, json: () => decoded.promise } as Response; }) as typeof fetch;
  const refresh = refreshSession("provider");
  await new Promise((resolve) => setTimeout(resolve, 0));
  select("refresh-b"); login("valid-b");
  decoded.resolve({ code: 401 });
  await assert.rejects(refresh, (error: any) => error.code === "SESSION_CHANGED");
  assert.equal(sentHeader["X-Store-Slug"], "refresh-a");
  assert.equal(getSession("provider").token, "valid-b");
});

test("late anonymous responses are rejected when the store changes", async () => {
  select("public-a"); const pending = deferred<Response>();
  globalThis.fetch = (() => pending.promise) as typeof fetch;
  const read = createApiClient("boss").get("/banners/", undefined, { public: true });
  select("public-b"); pending.resolve(ok({ private_brand: "a" }));
  await assert.rejects(read, (error: any) => error.code === "SESSION_CHANGED");
});

test("pending fund intent IDs never cross store boundaries", async () => {
  const ids: string[] = [];
  globalThis.fetch = (async (_url, options) => { ids.push(JSON.parse(String(options!.body)).client_request_id); throw new TypeError("network interrupted"); }) as typeof fetch;
  select("fund-a"); login("fund-a-token");
  await assert.rejects(fundRequest("provider", "/wallet/withdraw/", { amount: 80 }));
  await assert.rejects(fundRequest("provider", "/wallet/withdraw/", { amount: 80 }));
  select("fund-b"); login("fund-b-token");
  await assert.rejects(fundRequest("provider", "/wallet/withdraw/", { amount: 80 }));
  assert.equal(ids[0], ids[1]); assert.notEqual(ids[0], ids[2]);
});

test("realtime sockets bind store URLs and ignore old-store deliveries", () => {
  const previous = { window: globalThis.window, document: globalThis.document, WebSocket: globalThis.WebSocket };
  const sockets: FakeSocket[] = [];
  class FakeSocket {
    static OPEN = 1; readyState = 1;
    onopen: (() => void) | null = null; onclose: (() => void) | null = null; onerror: (() => void) | null = null; onmessage: ((event: { data: string }) => void) | null = null;
    url: string; closed = false;
    constructor(url: string) { this.url = url; sockets.push(this); }
    send() {} close() { this.closed = true; }
  }
  Object.assign(globalThis, { window: new EventTarget(), document: Object.assign(new EventTarget(), { hidden: false }), WebSocket: FakeSocket });
  let received = 0; let stopA = () => {}; let stopB = () => {};
  try {
    select("socket-a"); login("socket-a-token"); stopA = subscribeRealtime("provider", () => received++);
    assert.match(sockets[0].url, /store=socket-a&token=socket-a-token/);
    select("socket-b"); login("socket-b-token");
    sockets[0].onmessage?.({ data: '{"type":"order_update"}' });
    assert.equal(received, 0); assert.equal(sockets[0].closed, true);
    stopB = subscribeRealtime("provider", () => received++);
    assert.match(sockets[1].url, /store=socket-b&token=socket-b-token/);
  } finally { stopA(); stopB(); Object.assign(globalThis, previous); }
});

test("store links preserve root queries and switching drops old business hashes", () => {
  select("links");
  const linked = new URL(storeUrl("https://club.example/?campaign=fall#/audition?token=proof", "new-store"));
  assert.equal(linked.searchParams.get("store"), "new-store"); assert.equal(linked.searchParams.get("campaign"), "fall"); assert.equal(linked.hash, "#/audition?token=proof");
  let navigation = ""; site.assign = (url) => { navigation = url; };
  switchStore("another-store");
  assert.equal(new URL(navigation).hash, "#/login");
  assert.equal(new URL(navigation).searchParams.get("store"), "another-store");
  assert.throws(() => switchStore("../../default"), /门店标识/);
});

test("legacy pending writes are adopted only by the original store", () => {
  const legacy = "xa:boss:web:intent:1:checkout"; values.set(legacy, '{"id":"original-id"}');
  select("another"); adoptLegacyStoreStorage(); assert.equal(values.get(legacy), '{"id":"original-id"}');
  select("shenglang"); adoptLegacyStoreStorage();
  assert.equal(values.get(storeStorageKey(legacy)), '{"id":"original-id"}'); assert.equal(values.has(legacy), false);
  assert.equal(getStoreSlug(), "shenglang");
});

test("legacy pending conflicts preserve both original requests", () => {
  select("shenglang");
  const legacy = "xa:boss:web:intent:1:checkout";
  values.set(legacy, '{"id":"legacy-id"}'); values.set(storeStorageKey(legacy), '{"id":"new-id"}');
  assert.throws(() => adoptLegacyStoreStorage(), /两份待核对交易/);
  assert.equal(values.get(legacy), '{"id":"legacy-id"}');
  assert.equal(values.get(storeStorageKey(legacy)), '{"id":"new-id"}');
});

test("private media renewal accepts tenant query URLs without including routing in the ticket", () => {
  assert.equal(privateMediaTicket("https://api.example/api/media/private/signed%3Aticket/?store=another"), "signed:ticket");
  assert.equal(privateMediaTicket("/api/media/private/signed-ticket/"), "signed-ticket");
  assert.equal(privateMediaTicket("/api/media/private/%ZZ/?store=another"), null);
});
