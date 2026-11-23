import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { createApiClient, fundRequest, listPendingFundRequests, retryPendingFundRequest } from "../src/client.ts";
import { clearSession, getSession, persistSession } from "../src/session.ts";
import { subscribeRealtime } from "../src/realtime.ts";

const values = new Map<string, string>();
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: {
    get length() { return values.size; },
    key: (index: number) => Array.from(values.keys())[index] ?? null,
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  },
});
const originalFetch = globalThis.fetch;
const login = (token = "old-access", id = "provider-1") =>
  persistSession("provider", {
    token,
    refreshToken: "real-refresh",
    userInfo: { id, role: "provider" },
  });
const response = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const ok = (data: unknown) => response({ code: 0, data });
const deferred = <T>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
};
afterEach(() => {
  globalThis.fetch = originalFetch;
  clearSession("boss");
  clearSession("provider");
  clearSession("admin");
  values.clear();
});

test("role-specific logout cannot remove another role session", () => {
  login();
  persistSession("boss", {
    token: "boss-access",
    userInfo: { id: "boss-1", role: "customer" },
  });
  clearSession("boss");
  assert.equal(getSession("provider").token, "old-access");
  assert.equal(getSession("boss").token, "");
  assert.throws(() =>
    persistSession("boss", {
      token: "wrong",
      userInfo: { id: "provider-1", role: "provider" },
    }),
  );
});

test("two concurrent expired requests share exactly one refresh", async () => {
  login();
  const gate = deferred<Response>();
  let refreshCount = 0;
  globalThis.fetch = (async (url: string, options: RequestInit) => {
    if (url === "/api/users/refresh/") {
      refreshCount++;
      return gate.promise;
    }
    const token = (options.headers as Record<string, string>).Authorization;
    return token === "Bearer new-access"
      ? ok({ tokenUsed: "new-access" })
      : response({ code: 401 }, 401);
  }) as typeof fetch;
  const client = createApiClient("provider");
  const first = client.get("/orders/first/"),
    second = client.get("/orders/second/");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(refreshCount, 1);
  gate.resolve(ok({ token: "new-access" }));
  assert.equal((await first).tokenUsed, "new-access");
  assert.equal((await second).tokenUsed, "new-access");
});

test("a response from the previous account is rejected after account switch", async () => {
  login();
  const gate = deferred<Response>();
  globalThis.fetch = (() => gate.promise) as typeof fetch;
  const operation = createApiClient("provider").get("/wallet/balance/");
  login("another-access", "provider-2");
  gate.resolve(ok({ balance: 999999 }));
  await assert.rejects(
    operation,
    (error: any) => error.code === "SESSION_CHANGED",
  );
  assert.equal(getSession("provider").user?.id, "provider-2");
});

test("logout while refresh is in flight cannot resurrect a session", async () => {
  login();
  const gate = deferred<Response>();
  globalThis.fetch = (async (url: string) =>
    url === "/api/users/refresh/"
      ? gate.promise
      : response({ code: 401 }, 401)) as typeof fetch;
  const operation = createApiClient("provider").get("/users/me/");
  await new Promise((resolve) => setImmediate(resolve));
  clearSession("provider");
  gate.resolve(ok({ token: "late-access" }));
  await assert.rejects(
    operation,
    (error: any) => error.code === "SESSION_CHANGED",
  );
  assert.equal(getSession("provider").token, "");
});

test("unknown fund result preserves the same id when retrying reordered payload", async () => {
  login();
  const ids: string[] = [];
  globalThis.fetch = (async (_url: string, options: RequestInit) => {
    ids.push(JSON.parse(String(options.body)).client_request_id);
    if (ids.length === 1)
      throw new TypeError("connection dropped after submission");
    return ok({ withdrawal_id: 7 });
  }) as typeof fetch;
  await assert.rejects(
    fundRequest("provider", "/wallet/withdraw/", { amount: 1000, account: 9 }),
  );
  const result = await fundRequest("provider", "/wallet/withdraw/", {
    account: 9,
    amount: 1000,
  });
  assert.equal(result.withdrawal_id, 7);
  assert.equal(ids.length, 2);
  assert.equal(ids[0], ids[1]);
});

test("explicit fund intent id is retained and processed receipt is recovered", async () => {
  login();
  let submitted: any;
  globalThis.fetch = (async (_url: string, options: RequestInit) => {
    submitted = JSON.parse(String(options.body));
    return response(
      {
        code: 409,
        errorCode: "FUND_ALREADY_PROCESSED",
        fundResult: { code: 0, data: { id: 42 } },
      },
      409,
    );
  }) as typeof fetch;
  assert.equal(
    (
      await fundRequest("provider", "/wallet/withdraw/", {
        client_request_id: "original-intent",
        amount: 800,
      })
    ).id,
    42,
  );
  assert.equal(submitted.client_request_id, "original-intent");
});

test("explicit recovery reuses the saved payload and only clears a verified result", async () => {
  login();
  const path = "/wallet/withdraw/";
  const payload = { amount: 1200, withdrawal_account_id: 7 };
  const attempts: unknown[] = [];
  const accepts = (data: { id?: number }) => data?.id === 51;
  globalThis.fetch = (async (url: string, options: RequestInit) => {
    assert.equal(url, "/api" + path);
    attempts.push(JSON.parse(String(options.body)));
    if (attempts.length === 1) throw new TypeError("lost reply");
    return response({ code: 409, errorCode: "FUND_ALREADY_PROCESSED",
      fundResult: { code: 0, data: { id: attempts.length === 2 ? 52 : 51 } } });
  }) as typeof fetch;

  await assert.rejects(fundRequest("provider", path, payload));
  const list = () => listPendingFundRequests("provider", candidate => candidate === path);
  const [pending] = list();
  assert.equal(attempts.length, 1);
  assert.deepEqual(pending.payload, payload);
  await assert.rejects(retryPendingFundRequest("provider", pending, accepts), /保留原编号/);
  assert.equal(list()[0].requestId, pending.requestId);
  assert.deepEqual(await retryPendingFundRequest("provider", pending, accepts), { id: 51 });
  assert.deepEqual(attempts, Array(3).fill({ ...payload, client_request_id: pending.requestId }));
  assert.deepEqual(list(), []);
});

test("explicit recovery fails before POST when the caller, contents, or saved marker changed", async () => {
  login();
  const path = "/wallet/withdraw/14/cancel/";
  let requests = 0;
  globalThis.fetch = (async () => {
    requests++;
    throw new TypeError("lost reply");
  }) as typeof fetch;
  await assert.rejects(fundRequest("provider", path, {}));
  const [pending] = listPendingFundRequests("provider", candidate => candidate === path);
  await assert.rejects(retryPendingFundRequest("provider", { ...pending, payload: { amount: 11 } }, () => true), /已变化/);
  login("another-access", "another-provider");
  assert.deepEqual(listPendingFundRequests("provider", () => true), []);
  await assert.rejects(retryPendingFundRequest("provider", pending, () => true), /已变化/);
  login("renewed-access");
  localStorage.setItem(pending.key, "replaced-request");
  await assert.rejects(retryPendingFundRequest("provider", pending, () => true), /已变化/);
  assert.equal(requests, 1);
});

test("a late receipt only removes the marker for the request actually sent", async () => {
  login();
  const path = "/wallet/withdraw/14/cancel/";
  globalThis.fetch = (async () => { throw new TypeError("lost reply"); }) as typeof fetch;
  await assert.rejects(fundRequest("provider", path, {}));
  const [pending] = listPendingFundRequests("provider", () => true);
  const gate = deferred<Response>();
  globalThis.fetch = (() => gate.promise) as typeof fetch;
  const recovery = retryPendingFundRequest("provider", pending, (data: { id: number }) => data.id === 14);
  localStorage.setItem(pending.key, "different-request");
  gate.resolve(ok({ id: 14 }));
  assert.deepEqual(await recovery, { id: 14 });
  assert.equal(localStorage.getItem(pending.key), "different-request");
});

test("a first withdrawal rejection leaves the shared number for another tab with a lost reply", async () => {
  login();
  const otherTab = await import(new URL("../src/client.ts?tab=withdrawal-replay", import.meta.url).href);
  const path = "/wallet/withdraw/";
  const payload = { amount: 1500 };
  const firstResponse = deferred<Response>();
  const concurrentResponse = deferred<Response>();
  const requests: unknown[] = [];
  globalThis.fetch = ((_url: string, options: RequestInit) => {
    requests.push(JSON.parse(String(options.body)));
    return requests.length === 1 ? firstResponse.promise : concurrentResponse.promise;
  }) as typeof fetch;

  const first = fundRequest("provider", path, payload, { keepOriginalOnFailure: true });
  const [pending] = otherTab.listPendingFundRequests("provider", (candidate: string) => candidate === path);
  const concurrent = otherTab.retryPendingFundRequest("provider", pending, () => true);
  assert.deepEqual(requests, Array(2).fill({ ...payload, client_request_id: pending.requestId }));
  firstResponse.resolve(response({ code: 400, msg: "可用余额不足" }));
  await assert.rejects(first, /可用余额不足/);
  concurrentResponse.resolve(response({ code: 503, msg: "本次结果暂不可核验" }, 503));
  await assert.rejects(concurrent, /暂不可核验/);
  assert.equal(listPendingFundRequests("provider", () => true)[0].requestId, pending.requestId);
});

test("multipart upload leaves boundary selection to the browser transport", async () => {
  login();
  const body = new FormData();
  body.set(
    "image",
    new Blob(["fixture"], { type: "image/png" }),
    "fixture.png",
  );
  globalThis.fetch = (async (_url: string, options: RequestInit) => {
    assert.equal(options.body, body);
    assert.equal(
      (options.headers as Record<string, string>)["Content-Type"],
      undefined,
    );
    return ok({ image_id: 1 });
  }) as typeof fetch;
  assert.equal(
    (await createApiClient("provider").upload("/orders/evidence/", body))
      .image_id,
    1,
  );
});

test("role realtime subscriptions reuse a socket until the final subscriber leaves", () => {
  login();
  const previous = {
    window: globalThis.window,
    document: globalThis.document,
    WebSocket: globalThis.WebSocket,
    location: globalThis.location,
  };
  const sockets: FakeSocket[] = [];
  class FakeSocket {
    static OPEN = 1;
    readyState = 1;
    onopen: (() => void) | null = null;
    onclose: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onmessage: ((event: { data: string }) => void) | null = null;
    closed = false;
    constructor() {
      sockets.push(this);
    }
    send() {
      this.onmessage?.({ data: '{"type":"pong"}' });
    }
    close() {
      this.closed = true;
    }
  }
  Object.assign(globalThis, {
    window: new EventTarget(),
    document: Object.assign(new EventTarget(), { hidden: false }),
    location: { protocol: "http:", host: "127.0.0.1:13102" },
    WebSocket: FakeSocket,
  });
  let first = 0,
    second = 0;
  const stopFirst = subscribeRealtime("provider", () => {
    first++;
  });
  const stopSecond = subscribeRealtime("provider", () => {
    second++;
  });
  try {
    assert.equal(sockets.length, 1);
    sockets[0].onopen?.();
    sockets[0].onmessage?.({ data: '{"type":"order_update"}' });
    assert.equal(first, 2);
    assert.equal(second, 2);
    stopFirst();
    assert.equal(sockets[0].closed, false);
    stopSecond();
    assert.equal(sockets[0].closed, true);
  } finally {
    stopFirst();
    stopSecond();
    Object.assign(globalThis, previous);
  }
});
