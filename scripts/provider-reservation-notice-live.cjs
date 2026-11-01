// Render the provider's real message page with the committed API notice.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const args = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/provider-miniapp');
const ts = createRequire(path.join(root, 'package.json'))('typescript');
const compile = file => ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React}}).outputText;
let current, navigation = '';
const pending = new Set();
const react = {
  createElement: (type, props, ...children) => ({type, props: {...props, children}}),
  useState: initial => {const i = current.cursor++, owner = current; if (!(i in owner.values)) owner.values[i] = initial; return [owner.values[i], next => {owner.values[i] = next; owner.dirty = true;}];},
  useRef: initial => {const i = current.cursor++; if (!(i in current.values)) current.values[i] = {current: initial}; return current.values[i];},
  useEffect: (fn, deps) => {const i = current.cursor++; if (!current.effects[i]) {current.effects[i] = deps; current.queue.push(fn);}},
};
const taro = {
  eventCenter: {on() {}, off() {}, trigger() {}},
  getCurrentInstance: () => ({router: {params: {id: String(args.message)}}}),
  request: options => {const p = fetch(options.url, {method: options.method, headers: options.header}).then(async r => ({statusCode: r.status, data: await r.json(), header: {}})); pending.add(p); p.finally(() => pending.delete(p)); return p;},
  navigateTo: async ({url}) => {navigation = url;}, showToast() {},
};
const auth = {getSessionRevision: () => 1, getStoredToken: () => args.token, getStoredRefreshToken: () => '', clearStoredUser() {}, sessionChangedError: () => Error('session changed'), setStoredToken() {}};
const deps = {
  react: {__esModule: true, default: react, ...react}, '@tarojs/components': {Button: 'Button', Text: 'Text', View: 'View'},
  '@tarojs/taro': {__esModule: true, default: taro}, '@/utils/auth': auth, './auth': auth,
  './env': {BASE_URL: args.base + '/api'}, '@/utils/env': {BASE_URL: args.base + '/api'},
  './orderNetwork': {scopeFor: () => '', cooldownRemaining: () => 0, recordOrderRequest() {}},
  './orderAlerts': {orderAlerts: {}}, '@/utils/orderNavigation': {clearOrderFocus() {}, queueOrderFocus() {}},
  '@/utils/reportNavigation': {queueReportOrder() {}, clearReportOrder() {}},
  '@/components/PageNav': {default: 'PageNav'}, '@/components/PlatformLayout': {default: 'PlatformLayout'}, './index.module.scss': {default: {}},
};
function load(name, file) {const module = {exports: {}}; vm.runInNewContext(compile(file), {module, exports: module.exports, require: id => {if (!(id in deps)) throw Error('Missing import ' + id); return deps[id];}, Number, Date, Error, Promise}); deps[name] = module.exports; return module.exports;}
load('@/utils/request', 'src/utils/request.ts');
load('@/services/message', 'src/services/message.ts');
const Message = load('message', 'src/pages/message-detail/index.tsx').default;
function visit(node, out = []) {if (Array.isArray(node)) node.forEach(n => visit(n, out)); else if (node && typeof node === 'object') {out.push(node); visit(node.props?.children, out);} return out;}
function text(node) {return Array.isArray(node) ? node.map(text).join('') : node && typeof node === 'object' ? text(node.props.children) : node == null || node === false ? '' : String(node);}
async function main() {
  const page = {values: [], effects: [], queue: [], dirty: true};
  for (let i = 0; i < 30; i++) {
    if (page.dirty) {current = page; page.cursor = 0; page.dirty = false; page.tree = Message(); for (const fn of page.queue.splice(0)) fn();}
    if (pending.size) await Promise.all([...pending]);
    await new Promise(resolve => setImmediate(resolve));
    if (!page.dirty && !pending.size) break;
  }
  const button = visit(page.tree).find(n => n.type === 'Button' && text(n) === '查看预约');
  assert.ok(button, 'reservation link missing');
  await button.props.onClick();
  assert.equal(navigation, `/pages/reservations/index?id=${args.reservation}`);
}
main().catch(error => {console.error(error); process.exitCode = 1;});
