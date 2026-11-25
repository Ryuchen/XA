/// <reference types="node" />
import assert from "node:assert/strict"
import test from "node:test"
import { act, createElement } from "react"
import { clearSession, persistSession } from "../../shared/src/session.ts"
import { AddonRefundProposals as BossAddonRefundProposals } from "../src/business/addon-refund-proposals.tsx"

const { AddonRefundProposals: ProviderAddonRefundProposals } = await import("../../provider/src/components/AddonRefundProposals.tsx")

function memoryStorage() {
  const values = new Map<string, string>()
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) },
    removeItem: (key: string) => { values.delete(key) },
    get length() { return values.size },
    key: (index: number) => [...values.keys()][index] ?? null,
    clear: () => values.clear(),
  }
}

const localStorage = memoryStorage()
const sessionStorage = memoryStorage()
Object.defineProperty(globalThis, "localStorage", { configurable: true, value: localStorage })
Object.defineProperty(globalThis, "sessionStorage", { configurable: true, value: sessionStorage })
Object.defineProperty(globalThis, "location", {
  configurable: true,
  value: { hostname: "sl.esports.club", host: "sl.esports.club", protocol: "http:", search: "", hash: "" },
})

type DomListener = (event: DomEvent) => void
class DomEvent {
  type: string
  target: DomNode | null = null
  currentTarget: DomNode | null = null
  bubbles: boolean
  cancelable: boolean
  defaultPrevented = false
  timeStamp = Date.now()
  constructor(type: string, init: { bubbles?: boolean; cancelable?: boolean } = {}) {
    this.type = type
    this.bubbles = init.bubbles ?? false
    this.cancelable = init.cancelable ?? false
  }
  preventDefault() { if (this.cancelable) this.defaultPrevented = true }
  stopPropagation() { this.bubbles = false }
}

class DomNode {
  nodeType: number
  ownerDocument: DomDocument
  parentNode: DomNode | null = null
  childNodes: DomNode[] = []
  constructor(nodeType: number, ownerDocument: DomDocument) {
    this.nodeType = nodeType
    this.ownerDocument = ownerDocument
  }
  get firstChild() { return this.childNodes[0] ?? null }
  get lastChild() { return this.childNodes.at(-1) ?? null }
  get nextSibling() {
    if (!this.parentNode) return null
    return this.parentNode.childNodes[this.parentNode.childNodes.indexOf(this) + 1] ?? null
  }
  get parentElement() { return this.parentNode?.nodeType === 1 ? this.parentNode as DomElement : null }
  appendChild(child: DomNode) {
    if (child.parentNode) child.parentNode.removeChild(child)
    child.parentNode = this
    this.childNodes.push(child)
    return child
  }
  insertBefore(child: DomNode, before: DomNode | null) {
    if (!before) return this.appendChild(child)
    if (child.parentNode) child.parentNode.removeChild(child)
    const index = this.childNodes.indexOf(before)
    child.parentNode = this
    this.childNodes.splice(index < 0 ? this.childNodes.length : index, 0, child)
    return child
  }
  removeChild(child: DomNode) {
    const index = this.childNodes.indexOf(child)
    if (index >= 0) this.childNodes.splice(index, 1)
    child.parentNode = null
    return child
  }
  remove() { this.parentNode?.removeChild(this) }
  contains(node: DomNode): boolean { return this === node || this.childNodes.some(child => child.contains(node)) }
  getRootNode(): DomNode { return this.parentNode?.getRootNode() ?? this }
  get textContent(): string { return this.childNodes.map(child => child.textContent).join("") }
  set textContent(value: string) {
    this.childNodes.forEach(child => { child.parentNode = null })
    this.childNodes = []
    if (value) this.appendChild(new DomText(value, this.ownerDocument))
  }
}

class DomText extends DomNode {
  nodeValue: string
  constructor(value: string, ownerDocument: DomDocument) { super(3, ownerDocument); this.nodeValue = value }
  get textContent() { return this.nodeValue }
  set textContent(value: string) { this.nodeValue = value }
}

class DomElement extends DomNode {
  tagName: string
  localName: string
  namespaceURI = "http://www.w3.org/1999/xhtml"
  attributes = new Map<string, string>()
  listeners = new Map<string, Set<DomListener>>()
  style = { setProperty: (_name: string, _value: string) => {} }
  private checkedValue = false
  private inputValue = ""
  private inputType = ""
  get checked() { return this.checkedValue }
  set checked(value: boolean) { this.checkedValue = value }
  get value() { return this.inputValue }
  set value(value: string) { this.inputValue = value }
  get type() { return this.inputType }
  set type(value: string) { this.inputType = value }
  constructor(tagName: string, ownerDocument: DomDocument) {
    super(1, ownerDocument)
    this.localName = tagName
    this.tagName = tagName.toUpperCase()
  }
  get children() { return this.childNodes.filter((child): child is DomElement => child.nodeType === 1) }
  get className() { return this.getAttribute("class") ?? "" }
  set className(value: string) { this.setAttribute("class", value) }
  setAttribute(name: string, value: unknown) { this.attributes.set(name, String(value)) }
  setAttributeNS(_namespace: string, name: string, value: unknown) { this.setAttribute(name, value) }
  getAttribute(name: string) { return this.attributes.get(name) ?? null }
  removeAttribute(name: string) { this.attributes.delete(name) }
  removeAttributeNS(_namespace: string, name: string) { this.removeAttribute(name) }
  hasAttribute(name: string) { return this.attributes.has(name) }
  addEventListener(type: string, listener: DomListener) {
    const listeners = this.listeners.get(type) ?? new Set<DomListener>()
    listeners.add(listener)
    this.listeners.set(type, listeners)
  }
  removeEventListener(type: string, listener: DomListener) { this.listeners.get(type)?.delete(listener) }
  dispatchEvent(event: DomEvent) {
    event.target = this
    let current: DomNode | null = this
    while (current) {
      event.currentTarget = current
      current.listeners?.get(event.type)?.forEach(listener => listener(event))
      if (!event.bubbles) break
      current = current.parentNode
    }
    return !event.defaultPrevented
  }
  focus() { this.ownerDocument.activeElement = this }
  blur() { if (this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = this.ownerDocument.body }
}

class DomComment extends DomNode {
  data: string
  constructor(data: string, ownerDocument: DomDocument) { super(8, ownerDocument); this.data = data }
  get textContent() { return this.data }
  set textContent(value: string) { this.data = value }
}

class DomDocument {
  hidden = true
  defaultView: Record<string, unknown>
  documentElement: DomElement
  body: DomElement
  activeElement: DomElement
  listeners = new Map<string, Set<DomListener>>()
  constructor(defaultView: Record<string, unknown>) {
    this.defaultView = defaultView
    this.documentElement = new DomElement("html", this)
    this.body = new DomElement("body", this)
    this.documentElement.appendChild(this.body)
    this.activeElement = this.body
  }
  createElement(tagName: string) { return new DomElement(tagName, this) }
  createElementNS(_namespace: string, tagName: string) { return new DomElement(tagName, this) }
  createTextNode(value: string) { return new DomText(value, this) }
  createComment(value: string) { return new DomComment(value, this) }
  addEventListener(type: string, listener: DomListener) {
    const listeners = this.listeners.get(type) ?? new Set<DomListener>()
    listeners.add(listener)
    this.listeners.set(type, listeners)
  }
  removeEventListener(type: string, listener: DomListener) { this.listeners.get(type)?.delete(listener) }
}

function installDom() {
  const window: Record<string, unknown> = { location, addEventListener() {}, removeEventListener() {}, getComputedStyle: () => ({ getPropertyValue: () => "" }) }
  const document = new DomDocument(window)
  Object.assign(window, { window, self: window, document, Node: DomNode, Element: DomElement, HTMLElement: DomElement, HTMLIFrameElement: class extends DomElement {}, Event: DomEvent })
  Object.defineProperties(globalThis, {
    window: { configurable: true, value: window },
    document: { configurable: true, value: document },
    Node: { configurable: true, value: DomNode },
    Element: { configurable: true, value: DomElement },
    HTMLElement: { configurable: true, value: DomElement },
    Event: { configurable: true, value: DomEvent },
    IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: true },
  })
}

installDom()
const { renderToStaticMarkup } = await import("react-dom/server")
const { createRoot } = await import("react-dom/client")
const originalFetch = globalThis.fetch
const settle = () => new Promise<void>(resolve => setTimeout(resolve, 0))
async function flush() {
  await act(async () => { await settle(); await settle(); await settle() })
}
function nodeText(node: DomNode): string { return node.textContent.trim() }
function allElements(node: DomNode): DomElement[] {
  return node.nodeType === 1 ? [node as DomElement, ...node.childNodes.flatMap(allElements)] : node.childNodes.flatMap(allElements)
}
function button(container: DomNode, label: string) {
  const found = allElements(container).find(node => node.tagName === "BUTTON" && nodeText(node) === label)
  assert.ok(found, `missing button: ${label}\n${nodeText(container)}`)
  return found
}
function disabled(node: DomElement) { return node.hasAttribute("disabled") }
async function click(node: DomElement) {
  await act(async () => { node.dispatchEvent(new DomEvent("click", { bubbles: true, cancelable: true })); await settle() })
  await flush()
}
async function change(node: DomElement, checked: boolean) {
  const key = Object.keys(node).find(name => name.startsWith("__reactProps$"))
  assert.ok(key, "mounted input must expose its React props")
  const props = (node as unknown as Record<string, { onChange?: (event: unknown) => void }>)[key!]
  assert.equal(typeof props.onChange, "function")
  await act(async () => { props.onChange?.({ target: { checked } }); await settle() })
  await flush()
}
async function submitForm(container: DomNode) {
  const form = allElements(container).find(node => node.tagName === "FORM")
  assert.ok(form, "mounted decision form must exist")
  await act(async () => { form.dispatchEvent(new DomEvent("submit", { bubbles: true, cancelable: true })); await settle() })
  await flush()
}
async function mount(Component: (typeof implementations)[number]["Component"], role: "boss" | "provider", accountId: number) {
  const container = document.createElement("div")
  document.body.appendChild(container)
  const root = createRoot(container)
  await act(async () => { root.render(createElement(Component, { role, orderId: 5 })) })
  await flush()
  return { container, root, async close() {
    await act(async () => root.unmount())
    container.remove()
  } }
}
function proposalFor(role: "boss" | "provider", accountId: number, overrides: Record<string, unknown> = {}) {
  return {
    id: 21, order_id: 5, payment_id: 11, case_id: 31, addon_name: "测试加购", amount_raw: 50,
    provider_burden_raw: 50, provider_income_retained_raw: 50, explanation: "测试方案", status: "PENDING", refund_executed: false,
    decisions: [
      { account_id: accountId, role: role.toUpperCase(), accepted: null, decided_at: null },
      { account_id: role === "boss" ? 7 : 1, role: role === "boss" ? "PROVIDER" : "BOSS", accepted: null, decided_at: null },
    ],
    participants: [
      { account_id: role === "boss" ? accountId : 7, share_plan: [{ segment_id: 101, basis: 100, refund_basis: 50, burden: 50 }] },
      { account_id: role === "boss" ? 7 : 8, share_plan: [{ segment_id: 102, basis: 80, refund_basis: 0, burden: 0 }] },
    ],
    ...overrides,
  }
}
function acceptedProposal(role: "boss" | "provider", accountId: number, accepted = true, overrides: Record<string, unknown> = {}) {
  const proposal = proposalFor(role, accountId)
  proposal.decisions[0] = { account_id: accountId, role: role.toUpperCase(), accepted, decided_at: "2026-11-24T10:00:00Z" }
  return { ...proposal, ...overrides }
}
function installFetch(role: "boss" | "provider", getProposal: () => unknown, postProposal: (payload: unknown) => unknown | Promise<unknown>) {
  const posts: { path: string; payload: unknown }[] = []
  globalThis.fetch = async (input, init) => {
    const path = String(input).replace(/^\/api/, "")
    const method = init?.method ?? "GET"
    if (method === "GET" && path === "/orders/orders/5/addons/refund-proposals/")
      return new Response(JSON.stringify({ code: 0, data: [{ payment_id: 11, proposal_id: 21, status: "PENDING", my_accepted: null }] }), { status: 200 })
    if (method === "GET" && path === "/orders/orders/5/addons/payments/11/refund-proposals/21/")
      return new Response(JSON.stringify({ code: 0, data: getProposal() }), { status: 200 })
    if (method === "POST" && path === "/orders/orders/5/addons/payments/11/refund-proposals/21/") {
      const payload = JSON.parse(String(init?.body))
      posts.push({ path, payload })
      return new Response(JSON.stringify({ code: 0, data: await postProposal(payload) }), { status: 200 })
    }
    throw new Error(`unexpected request: ${method} ${path}`)
  }
  return posts
}

const implementations = [
  { name: "boss", role: "boss" as const, accountId: 1, accountRole: "customer", Component: BossAddonRefundProposals },
  { name: "provider", role: "provider" as const, accountId: 7, accountRole: "provider", Component: ProviderAddonRefundProposals },
]

for (const implementation of implementations) {
  const { name, role, accountId, accountRole, Component } = implementation

  test(`${name}: 未登录组件不暴露方案或操作`, () => {
    clearSession(role)
    assert.equal(renderToStaticMarkup(createElement(Component, { role, orderId: 5 })), "")
  })

  test(`${name}: 已登录首屏显示加载状态并禁用刷新`, () => {
    persistSession(role, { token: "test-token", user: { id: accountId, role: accountRole } })
    const html = renderToStaticMarkup(createElement(Component, { role, orderId: 5 }))
    assert.match(html, /正在读取加购退款方案列表/)
    assert.match(html, /刷新方案列表/)
    assert.match(html, /disabled=""/)
    clearSession(role)
  })

  test(`${name}: 待核对凭证按门店和账号隔离`, () => {
    sessionStorage.setItem(`xa:addon-refund-decision:shenglang:${role}:${accountId}:5`, JSON.stringify({
      paymentId: 11, proposalId: 21, accepted: true, caseId: 31, scopeKey: `${accountId}:101:100:50:50`,
    }))
    persistSession(role, { token: "test-token", user: { id: accountId, role: accountRole } })
    assert.match(renderToStaticMarkup(createElement(Component, { role, orderId: 5 })), /结果待核对/)

    Object.assign(location, { hostname: "xa.esports.club", host: "xa.esports.club" })
    persistSession(role, { token: "test-token", user: { id: accountId, role: accountRole } })
    assert.doesNotMatch(renderToStaticMarkup(createElement(Component, { role, orderId: 5 })), /结果待核对/)

    persistSession(role, { token: "test-token", user: { id: accountId + 100, role: accountRole } })
    assert.doesNotMatch(renderToStaticMarkup(createElement(Component, { role, orderId: 5 })), /结果待核对/)
    Object.assign(location, { hostname: "sl.esports.club", host: "sl.esports.club" })
    clearSession(role)
  })

  test(`${name}: 错端身份不能建立会话`, () => {
    const wrongRole = role === "boss" ? "provider" : "customer"
    assert.throws(() => persistSession(role, { token: "test-token", user: { id: accountId, role: wrongRole } }), /不属于当前端/)
    assert.equal(renderToStaticMarkup(createElement(Component, { role, orderId: 5 })), "")
  })

  test(`${name}: 挂载后 GET 已确认原决定可清除凭证，不依赖失效履约段`, async t => {
    sessionStorage.clear()
    persistSession(role, { token: "test-token", user: { id: accountId, role: accountRole } })
    const key = `xa:addon-refund-decision:shenglang:${role}:${accountId}:5`
    sessionStorage.setItem(key, JSON.stringify({ paymentId: 11, proposalId: 21, accepted: true, caseId: 31, scopeKey: `${accountId}:999:100:50:50` }))
    const recorded = acceptedProposal(role, accountId, true, { participants: undefined })
    const posts = installFetch(role, () => recorded, () => { throw new Error("must not post") })
    const view = await mount(Component, role, accountId)
    t.after(async () => { await view.close(); globalThis.fetch = originalFetch; clearSession(role); sessionStorage.clear() })

    assert.equal(allElements(view.container).filter(node => node.tagName === "INPUT").length, 0)
    assert.equal(disabled(button(view.container, "核对原决定")), false)
    assert.match(nodeText(view.container), /只核对并清除原凭证/)
    await click(button(view.container, "核对原决定"))
    assert.equal(posts.length, 0)
    assert.equal(sessionStorage.getItem(key), null)
    assert.match(nodeText(view.container), /原决定已核对/)
    assert.match(nodeText(view.container), /你的同意已记录/)
  })

  test(`${name}: 挂载后逐段核对、提交中防重及成功回写`, async t => {
    sessionStorage.clear()
    persistSession(role, { token: "test-token", user: { id: accountId, role: accountRole } })
    let server = proposalFor(role, accountId)
    let release!: (value: unknown) => void
    const gate = new Promise<unknown>(resolve => { release = resolve })
    const posts = installFetch(role, () => server, () => gate)
    const view = await mount(Component, role, accountId)
    t.after(async () => { await view.close(); globalThis.fetch = originalFetch; clearSession(role); sessionStorage.clear() })
    const checkboxes = () => allElements(view.container).filter(node => node.tagName === "INPUT")

    await click(button(view.container, "查看方案"))
    assert.equal(checkboxes().length, role === "boss" ? 2 : 1, nodeText(view.container))
    assert.throws(() => button(view.container, "同意方案"))
    for (const index of [0, 1]) {
      if (index >= checkboxes().length) break
      await change(checkboxes()[index], true)
      assert.match(nodeText(view.container), new RegExp(`已核对 ${index + 1}/`))
    }
    await click(button(view.container, "同意方案"))
    await click(button(view.container, "确认提交"))
    await submitForm(view.container)
    assert.equal(posts.length, 1)
    assert.deepEqual(posts[0].payload, { accepted: true })
    assert.equal(disabled(button(view.container, "正在记录…")), true)
    await click(button(view.container, "正在记录…"))
    await submitForm(view.container)
    assert.equal(posts.length, 1)

    server = acceptedProposal(role, accountId)
    await act(async () => { release(server); await settle(); await settle(); await settle() })
    await flush()
    assert.equal(posts.length, 1)
    assert.equal(checkboxes().length, 0)
    assert.equal(sessionStorage.getItem(`xa:addon-refund-decision:shenglang:${role}:${accountId}:5`), null)
    assert.match(nodeText(view.container), /确认已记录，退款尚未执行/)
    assert.match(nodeText(view.container), /你的同意已记录/)
  })

  test(`${name}: 挂载后提交失败保留凭证，恢复服务端决定后清除`, async t => {
    sessionStorage.clear()
    persistSession(role, { token: "test-token", user: { id: accountId, role: accountRole } })
    let server = proposalFor(role, accountId)
    const posts = installFetch(role, () => server, () => { throw new TypeError("network down") })
    const view = await mount(Component, role, accountId)
    t.after(async () => { await view.close(); globalThis.fetch = originalFetch; clearSession(role); sessionStorage.clear() })
    const key = `xa:addon-refund-decision:shenglang:${role}:${accountId}:5`
    const checkboxes = () => allElements(view.container).filter(node => node.tagName === "INPUT")

    await click(button(view.container, "查看方案"))
    for (let index = 0; index < checkboxes().length; index++) {
      await change(checkboxes()[index], true)
      assert.match(nodeText(view.container), new RegExp(`已核对 ${index + 1}/`))
    }
    await click(button(view.container, "同意方案"))
    await click(button(view.container, "确认提交"))
    await submitForm(view.container)
    assert.equal(posts.length, 1)
    assert.match(nodeText(view.container), /网络中断/)
    assert.doesNotMatch(nodeText(view.container), /确认已记录/)
    assert.notEqual(sessionStorage.getItem(key), null)

    server = acceptedProposal(role, accountId, true, { participants: undefined })
    await click(button(view.container, "刷新所选方案"))
    assert.equal(checkboxes().length, 0)
    assert.equal(disabled(button(view.container, "核对原决定")), false)
    await click(button(view.container, "核对原决定"))
    assert.equal(posts.length, 1)
    assert.equal(sessionStorage.getItem(key), null)
    assert.match(nodeText(view.container), /原决定已核对/)
  })
}
