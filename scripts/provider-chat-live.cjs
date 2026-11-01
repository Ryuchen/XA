// Exercise the actual provider page and staff API against an isolated live server.
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const args = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/provider-miniapp');
const ts = createRequire(path.join(root, 'package.json'))('typescript');
const compile = file => ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React } }).outputText;
const pending = new Set(), storage = {}, events = new Map(), wsEvents = new Map(), toasts = [];
let current, loseSend = false, transport = Promise.resolve();
const taro = {
  getStorageSync: key => storage[key], setStorageSync: (key, value) => { storage[key] = value; }, removeStorageSync: key => { delete storage[key]; },
  eventCenter: { on: (name, fn) => { if (!events.has(name)) events.set(name, new Set()); events.get(name).add(fn); }, off: (name, fn) => events.get(name)?.delete(fn), trigger: name => events.get(name)?.forEach(fn => fn()) },
  showToast: value => toasts.push(value.title), showModal: async () => ({ confirm: true }),
  request: options => {
    const task = transport.then(async () => {
      const response = await fetch(options.url, { method: options.method, headers: options.header, ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) });
      return { statusCode: response.status, data: await response.json(), header: {} };
    });
    transport = task.then(() => {}, () => {});
    const result = task.then(value => {
      if (loseSend && options.url.endsWith('/chat/send/') && value.data.code === 0) { loseSend = false; throw { errMsg: 'request:fail response lost' }; }
      return value;
    });
    pending.add(result); result.then(() => pending.delete(result), () => pending.delete(result)); return result;
  },
};
const react = {
  createElement: (type, props, ...children) => ({ type, props: { ...props, children } }),
  useState: initial => { const i = current.cursor++, owner = current; if (!(i in owner.values)) owner.values[i] = typeof initial === 'function' ? initial() : initial; return [owner.values[i], next => { owner.values[i] = typeof next === 'function' ? next(owner.values[i]) : next; owner.dirty = true; }]; },
  useRef: initial => { const i = current.cursor++; if (!(i in current.values)) current.values[i] = { current: initial }; return current.values[i]; },
  useEffect: (fn, deps) => { const i = current.cursor++, old = current.effects[i]; if (!old || deps.some((value, j) => value !== old[j])) { current.effects[i] = deps; current.queue.push(fn); } },
};
const deps = {
  react: { __esModule: true, default: react, ...react }, '@tarojs/components': { Button: 'Button', ScrollView: 'ScrollView', Text: 'Text', Textarea: 'Textarea', View: 'View' },
  '@tarojs/taro': { __esModule: true, default: taro, useDidShow: fn => { current.didShow = fn; }, useDidHide: fn => { current.didHide = fn; } },
  './env': { BASE_URL: args.base + '/api' }, './orderNetwork': { scopeFor: () => '', cooldownRemaining: () => 0, recordOrderRequest() {} },
  '@/components/PageNav': { default: 'PageNav' }, '@/components/PlatformLayout': { default: 'PlatformLayout' }, '@/components/PrivateImage': { default: 'PrivateImage' },
  '@/utils/privateMedia': { previewPrivateImages() {} }, '@/services/websocket': { wsService: { on: (type, fn) => { if (!wsEvents.has(type)) wsEvents.set(type, new Set()); wsEvents.get(type).add(fn); return () => wsEvents.get(type).delete(fn); } } },
  './index.module.scss': { default: new Proxy({}, { get: (_, name) => name }) },
};
function load(name, file) { const m = { exports: {} }; vm.runInNewContext(compile(file), { module: m, exports: m.exports, require: id => { if (!(id in deps)) throw Error('Missing ' + id); return deps[id]; }, Number, Date, Math, Error, Promise, Set, Map, setTimeout, clearTimeout }); deps[name] = m.exports; return m.exports; }
const auth = load('@/utils/auth', 'src/utils/auth.ts'); deps['./auth'] = auth;
load('@/utils/request', 'src/utils/request.ts'); load('@/services/chat', 'src/services/chat.ts');
const Page = load('page', 'src/pages/support-chat/index.tsx').default;
const page = { values: [], effects: [], queue: [], cursor: 0, dirty: true };
function render() { current = page; page.cursor = 0; page.dirty = false; page.tree = Page(); for (const effect of page.queue.splice(0)) effect(); }
async function settle() { for (let i = 0; i < 150; i++) { if (page.dirty) render(); if (pending.size) await Promise.allSettled([...pending]); await new Promise(resolve => setImmediate(resolve)); if (!page.dirty && !pending.size) return; } throw Error('page did not settle'); }
function nodes(value, out = []) { if (Array.isArray(value)) value.forEach(x => nodes(x, out)); else if (value && typeof value === 'object') { out.push(value); nodes(value.props?.children, out); } return out; }
function text(value) { return Array.isArray(value) ? value.map(text).join('') : value && typeof value === 'object' ? text(value.props.children) : value == null || value === false ? '' : String(value); }
function button(label) { return nodes(page.tree).find(node => node.type === 'Button' && text(node) === label); }
async function staffReply(content) {
  const response = await fetch(`${args.base}/api/admin/chat-sessions/${args.sessionId}/reply/`, { method: 'POST', headers: { Authorization: 'Bearer ' + args.staffToken, 'Content-Type': 'application/json' }, body: JSON.stringify({ content }) });
  const body = await response.json(); assert.equal(body.code, 0, JSON.stringify(body));
}
async function main() {
  auth.persistLoginData({ token: args.providerToken, userInfo: { id: String(args.providerId), username: 'provider' } });
  await settle();
  assert.match(text(page.tree), /历史客服消息 50/); assert.doesNotMatch(text(page.tree), /历史客服消息 0/);
  assert.ok(button('加载更早消息')); await button('加载更早消息').props.onClick(); await settle();
  assert.match(text(page.tree), /历史客服消息 0/); assert.ok(button('回到最新消息'));
  await button('回到最新消息').props.onClick(); await settle();
  nodes(page.tree).find(node => node.type === 'Textarea').props.onInput({ detail: { value: '陪玩咨询排班' } }); await settle();
  await button('发送').props.onClick(); await settle(); assert.match(text(page.tree), /陪玩咨询排班/);
  await staffReply('客服已收到，稍后核对'); wsEvents.get('chat_message').forEach(fn => fn({})); await settle();
  assert.match(text(page.tree), /客服已收到，稍后核对/);
  nodes(page.tree).find(node => node.type === 'Textarea').props.onInput({ detail: { value: '网络中断后核对' } }); await settle();
  loseSend = true; await button('发送').props.onClick(); await settle();
  assert.ok(button('核对结果')); assert.match(text(page.tree), /网络中断后核对/);
  await button('核对结果').props.onClick(); await settle(); assert.equal(button('核对结果'), undefined);
  assert.equal(nodes(page.tree).find(node => node.type === 'Textarea').props.value, '');
  nodes(page.tree).find(node => node.type === 'Textarea').props.onInput({ detail: { value: '网络中断后重试' } }); await settle();
  loseSend = true; await button('发送').props.onClick(); await settle();
  await button('重试原消息').props.onClick(); await settle(); assert.equal(button('核对结果'), undefined);
  console.log('Provider page and staff exchanged real messages; history and receipt recovery passed');
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
