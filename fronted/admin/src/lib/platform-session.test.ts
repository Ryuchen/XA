/// <reference types="node" />
import { afterEach, test } from "node:test"
import assert from "node:assert/strict"
import {
  PLATFORM_AUTHORIZATION_DENIED_EVENT,
  PlatformNetworkError,
  PlatformRequestError,
  PlatformSessionError,
  platformMutationResultUnknown,
  platformRequest,
  platformSession,
  savePlatformSession,
  type PlatformProfile,
} from "./platform-session.ts"

const originalFetch = globalThis.fetch
const originalLocation = Object.getOwnPropertyDescriptor(globalThis, "location")
const originalSessionStorage = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage")
const originalDispatchEvent = Object.getOwnPropertyDescriptor(globalThis, "dispatchEvent")
const sessionKey = "xa:fronted:platform:session:v1"

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } })
}

function raw(data: string, status: number) {
  return new Response(data, { status })
}

function profile(overrides: Record<string, unknown> = {}) {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    username: "reviewer",
    nickname: "评审员",
    identity_type: "platform",
    grants: [{ role: "SUPER_ADMIN", scope_slug: "*" }],
    capabilities: ["PLATFORM_ORDERS_V1"],
    ...overrides,
  }
}

function store(overrides: Record<string, unknown> = {}) {
  return {
    id: 2,
    slug: "shenglang",
    name: "声浪电竞",
    currency_name: "浪花",
    entity_prefix: "SL",
    provider_name_prefix: "SL",
    logo_url: "",
    background_url: "",
    introduction: "门店介绍",
    status: "ACTIVE",
    is_active: true,
    boss_url: "https://shenglang.esports.club/",
    provider_url: "https://provider-shenglang.esports.club/",
    admin_url: "https://admin-shenglang.esports.club/",
    ...overrides,
  }
}

function installSession() {
  Object.defineProperty(globalThis, "location", { configurable: true, value: { hostname: "admin.esports.club", port: "", origin: "https://admin.esports.club" } })
  const values = new Map<string, string>()
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value) },
      removeItem: (key: string) => { values.delete(key) },
    },
  })
  savePlatformSession({
    token: "access", refresh: "refresh",
    profile: profile() as unknown as PlatformProfile,
  })
  return values
}

async function failure(run: () => Promise<unknown>) {
  try {
    await run()
  } catch (error) {
    return error
  }
  assert.fail("请求应失败")
}

afterEach(() => {
  globalThis.fetch = originalFetch
  if (originalLocation) Object.defineProperty(globalThis, "location", originalLocation)
  else Reflect.deleteProperty(globalThis, "location")
  if (originalSessionStorage) Object.defineProperty(globalThis, "sessionStorage", originalSessionStorage)
  else Reflect.deleteProperty(globalThis, "sessionStorage")
  if (originalDispatchEvent) Object.defineProperty(globalThis, "dispatchEvent", originalDispatchEvent)
  else Reflect.deleteProperty(globalThis, "dispatchEvent")
})

test("只有已发送后的网络中断、408 和 5xx 被判为写结果未知", () => {
  assert.equal(platformMutationResultUnknown(new PlatformSessionError("expired")), false)
  assert.equal(platformMutationResultUnknown(new PlatformNetworkError("offline")), true)
  assert.equal(platformMutationResultUnknown(new PlatformRequestError("timeout", 408)), true)
  assert.equal(platformMutationResultUnknown(new PlatformRequestError("server", 503)), true)
  assert.equal(platformMutationResultUnknown(new PlatformRequestError("denied", 403)), false)
  assert.equal(platformMutationResultUnknown(new PlatformRequestError("invalid", 400)), false)
})

test("原请求 401 且刷新失败时不锁写，并清除平台会话", async () => {
  installSession()
  globalThis.fetch = (async (input) => String(input).endsWith("/auth/refresh")
    ? json({ code: 401, msg: "登录已失效" }, 401)
    : json({ code: 401, msg: "登录已失效" }, 401)) as typeof fetch
  const error = await failure(() => platformRequest("compliance-rules", { method: "POST", data: { rules: [] } }))
  assert.ok(error instanceof PlatformSessionError)
  assert.equal(platformMutationResultUnknown(error), false)
  assert.equal(platformSession(), null)
})

test("刷新令牌网络失败不改变原 401 的确定性结果", async () => {
  installSession()
  globalThis.fetch = (async (input) => {
    if (String(input).endsWith("/auth/refresh")) throw new TypeError("offline")
    return json({ code: 401, msg: "登录已失效" }, 401)
  }) as typeof fetch
  const error = await failure(() => platformRequest("compliance-rules", { method: "POST", data: { rules: [] } }))
  assert.ok(error instanceof PlatformSessionError)
  assert.equal(platformMutationResultUnknown(error), false)
})

test("401 刷新成功后使用新令牌重试原请求", async () => {
  installSession()
  const authorizations: string[] = []
  globalThis.fetch = (async (input, init) => {
    const url = String(input)
    if (url.endsWith("/auth/refresh")) return json({ code: 0, data: { token: "renewed" } })
    authorizations.push(new Headers(init?.headers).get("Authorization") || "")
    return authorizations.length === 1
      ? json({ code: 401, msg: "过期" }, 401)
      : json({ code: 0, data: profile() })
  }) as typeof fetch
  const result = await platformRequest<PlatformProfile>("profile")
  assert.equal(result.username, "reviewer")
  assert.deepEqual(authorizations, ["Bearer access", "Bearer renewed"])
})

test("已发送请求的网络异常和 503 继续保持写锁", async () => {
  installSession()
  globalThis.fetch = (async () => { throw new TypeError("offline") }) as typeof fetch
  const offline = await failure(() => platformRequest("compliance-rules", { method: "POST", data: { rules: [] } }))
  assert.ok(offline instanceof PlatformNetworkError)
  assert.equal(platformMutationResultUnknown(offline), true)
  globalThis.fetch = (async () => json({ code: 503, msg: "暂不可用" }, 503)) as typeof fetch
  const unavailable = await failure(() => platformRequest("compliance-rules", { method: "POST", data: { rules: [] } }))
  assert.ok(unavailable instanceof PlatformRequestError)
  assert.equal(platformMutationResultUnknown(unavailable), true)
})

test("平台会话拒绝畸形 profile、授权和能力，且无效保存不覆盖原会话", () => {
  const values = installSession()
  const previous = platformSession()
  const malformed = [
    profile({ id: "1" }),
    profile({ grants: {} }),
    profile({ grants: [{ role: "UNKNOWN", scope_slug: "*" }] }),
    profile({ grants: [{ role: "SUPER_ADMIN", scope_slug: "bad_slug" }] }),
    profile({ grants: [{ role: "FINANCE", scope_slug: "*" }, { role: "FINANCE", scope_slug: "*" }] }),
    profile({ capabilities: "PLATFORM_ORDERS_V1" }),
    profile({ capabilities: ["UNKNOWN_V1"] }),
    profile({ capabilities: ["PLATFORM_ORDERS_V1", "PLATFORM_ORDERS_V1"] }),
    profile({ nickname: null }),
    profile({ password: "must-not-be-stored" }),
  ]
  for (const value of malformed) {
    values.set(sessionKey, JSON.stringify({ token: "access", refresh: "refresh", sessionId: "session", profile: value }))
    assert.equal(platformSession(), null)
  }
  values.set(sessionKey, JSON.stringify(previous))
  assert.throws(() => savePlatformSession({
    token: "new-access", refresh: "new-refresh", profile: malformed[0] as unknown as PlatformProfile,
  }), /平台登录身份无效/)
  assert.equal(platformSession()?.sessionId, previous?.sessionId)
})

test("profile 请求严格校验身份、授权和能力，并拒绝身份漂移", async () => {
  installSession()
  globalThis.fetch = (async () => json({ code: 0, data: profile() })) as typeof fetch
  assert.deepEqual(await platformRequest<PlatformProfile>("profile"), profile())
  const refreshed = profile({ grants: [{ role: "FINANCE", scope_slug: "*" }], capabilities: ["SAAS_MODULES_V1"] })
  globalThis.fetch = (async () => json({ code: 0, data: refreshed })) as typeof fetch
  await platformRequest<PlatformProfile>("profile")
  assert.deepEqual(platformSession()?.profile, refreshed)
  const malformed = [
    null,
    profile({ grants: null }),
    profile({ grants: [{ role: "FINANCE" }] }),
    profile({ capabilities: {} }),
    profile({ capabilities: undefined }),
    profile({ capabilities: ["NOT_GRANTED"] }),
    profile({ unexpected: true }),
    profile({ id: "22222222-2222-4222-8222-222222222222" }),
  ]
  for (const value of malformed) {
    globalThis.fetch = (async () => json({ code: 0, data: value })) as typeof fetch
    const error = await failure(() => platformRequest("profile"))
    assert.ok(error instanceof Error)
    assert.match(error.message, /平台身份/)
  }
})

test("stores 请求严格校验结果、门店字段和唯一性", async () => {
  installSession()
  globalThis.fetch = (async () => json({ code: 0, data: { results: [store()] } })) as typeof fetch
  assert.deepEqual(await platformRequest("stores"), {
    results: [{ id: 2, slug: "shenglang", name: "声浪电竞", status: "ACTIVE", currency_name: "浪花" }],
  })
  globalThis.fetch = (async () => json({ code: 0, data: { results: [store({ introduction: "第一行\n第二行" })] } })) as typeof fetch
  assert.equal((await platformRequest<{ results: { slug: string }[] }>("stores")).results[0].slug, "shenglang")
  const malformed = [
    { results: {} },
    { results: [{ ...store(), id: "2" }] },
    { results: [{ ...store(), status: "UNKNOWN" }] },
    { results: [{ ...store(), is_active: false }] },
    { results: [{ ...store(), admin_url: "javascript:alert(1)" }] },
    { results: [store(), store()] },
    { results: [store(), store({ id: 3 })] },
    { results: [{ ...store(), currency_name: undefined }] },
    { results: [store()], unexpected: true },
  ]
  for (const value of malformed) {
    globalThis.fetch = (async () => json({ code: 0, data: value })) as typeof fetch
    const error = await failure(() => platformRequest("stores"))
    assert.ok(error instanceof Error)
    assert.match(error.message, /平台门店目录/)
  }
})

test("写请求 403 触发身份刷新事件且不按未知结果锁定，GET 403 不触发", async () => {
  installSession()
  let deniedEvents = 0
  Object.defineProperty(globalThis, "dispatchEvent", {
    configurable: true,
    value: (event: Event) => {
      if (event.type === PLATFORM_AUTHORIZATION_DENIED_EVENT) deniedEvents += 1
      return true
    },
  })
  globalThis.fetch = (async () => json({ code: 403, msg: "权限已撤销" }, 403)) as typeof fetch
  const denied = await failure(() => platformRequest("compliance-rules", { method: "POST", data: { rules: [] } }))
  assert.ok(denied instanceof PlatformRequestError)
  assert.equal(denied.status, 403)
  assert.equal(platformMutationResultUnknown(denied), false)
  assert.equal(deniedEvents, 1)

  globalThis.fetch = (async () => raw("forbidden", 403)) as typeof fetch
  const malformed = await failure(() => platformRequest("compliance-rules", { method: "POST", data: {} }))
  assert.ok(malformed instanceof PlatformRequestError)
  assert.equal(malformed.status, 403)
  assert.equal(platformMutationResultUnknown(malformed), false)
  assert.equal(deniedEvents, 2)

  globalThis.fetch = (async () => json({ code: 403, msg: "无权读取" }, 403)) as typeof fetch
  const read = await failure(() => platformRequest("compliance-rules"))
  assert.ok(read instanceof PlatformRequestError)
  assert.equal(read.status, 403)
  assert.equal(deniedEvents, 2)
})
