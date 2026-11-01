// Real HTTP and actual TSX/service/request modules; only the Taro/React host is adapted.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const args = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/provider-miniapp');
const ts = createRequire(path.join(root, 'package.json'))('typescript');
const compile = file => ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React } }).outputText;
const pending = new Set();
let current, params = {}, navigation = '', token = args.partnerToken;
const request = async (url, method = 'GET', data, bearer = token) => {
  const response = await fetch(args.base + '/api' + url, { method, headers: { Authorization: 'Bearer ' + bearer, 'Content-Type': 'application/json' }, ...(data === undefined ? {} : { body: JSON.stringify(data) }) });
  return { status: response.status, data: await response.json() };
};
const taro = {
  eventCenter: {on() {}, off() {}, trigger() {}},
  getCurrentInstance: () => ({ router: { params } }),
  request: options => {
    const promise = fetch(options.url, { method: options.method, headers: options.header, ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
      .then(async response => ({ statusCode: response.status, data: await response.json(), header: {} }));
    pending.add(promise); promise.finally(() => pending.delete(promise)); return promise;
  },
  navigateTo: async ({url}) => { navigation = url; }, switchTab: async ({url}) => { navigation = url; },
  showModal: async () => ({confirm: true}), showToast() {},
};
const react = {
  createElement: (type, props, ...children) => ({type, props: {...props, children}}),
  useState: initial => { const i = current.cursor++; if (!(i in current.values)) current.values[i] = typeof initial === 'function' ? initial() : initial; const owner = current; return [owner.values[i], next => { owner.values[i] = typeof next === 'function' ? next(owner.values[i]) : next; owner.dirty = true; }]; },
  useRef: initial => { const i = current.cursor++; if (!(i in current.values)) current.values[i] = {current: initial}; return current.values[i]; },
  useEffect: (fn, deps) => { const i = current.cursor++; const old = current.effects[i]; if (!old || deps.some((v, j) => v !== old[j])) { current.effects[i] = deps; current.queue.push(fn); } },
};
const auth = { getSessionRevision: () => 1, getStoredToken: () => token, getStoredRefreshToken: () => '', clearStoredUser() {}, sessionChangedError: () => Error('session changed'), setStoredToken() {} };
const deps = {
  react: { __esModule: true, default: react, ...react },
  '@tarojs/components': {Button: 'Button', Text: 'Text', View: 'View'},
  '@tarojs/taro': {__esModule: true, default: taro, useDidShow: fn => { current.didShow = fn; }},
  '@/utils/auth': auth,
  '@/utils/env': { BASE_URL: args.base + '/api' },
  '@/utils/orderNetwork': {scopeFor: () => '', cooldownRemaining: () => 0, recordOrderRequest() {}},
  '@/services/orderAlerts': {orderAlerts: {}},
  '@/utils/orderNavigation': {clearOrderFocus() {}, queueOrderFocus() {}},
  '@/utils/reportNavigation': {queueReportOrder() {}, clearReportOrder() {}},
  '@/components/PageNav': {default: 'PageNav'}, '@/components/PlatformLayout': {default: 'PlatformLayout'},
  './index.module.scss': {default: {}},
};
deps['./auth'] = deps['@/utils/auth'];
deps['./env'] = deps['@/utils/env'];
deps['./orderNetwork'] = deps['@/utils/orderNetwork'];
deps['./orderAlerts'] = deps['@/services/orderAlerts'];
function load(name, file) { const module = {exports: {}}; vm.runInNewContext(compile(file), {module, exports: module.exports, require: id => { if (!(id in deps)) throw Error('Missing import ' + id); return deps[id]; }, Number, Date, Error, Promise, setTimeout, clearTimeout}); deps[name] = module.exports; return module.exports; }
load('@/utils/request', 'src/utils/request.ts');
load('@/services/order', 'src/services/order.ts');
load('@/services/message', 'src/services/message.ts');
const Message = load('message', 'src/pages/message-detail/index.tsx').default;
const Application = load('application', 'src/pages/application-detail/index.tsx').default;
function mount(component, inputParams) { params = inputParams; const page = {component, values: [], effects: [], queue: [], cursor: 0, dirty: true}; return page; }
function render(page) { current = page; page.cursor = 0; page.dirty = false; page.tree = page.component(); for (const effect of page.queue.splice(0)) effect(); }
async function settle(page) { for (let i=0; i<30; i++) { if (page.dirty) render(page); if (pending.size) await Promise.all([...pending]); await new Promise(resolve => setImmediate(resolve)); if (!page.dirty && !pending.size) return; } throw Error('page did not settle'); }
function visit(value, out=[]) { if (Array.isArray(value)) value.forEach(item => visit(item,out)); else if (value && typeof value === 'object') { out.push(value); visit(value.props?.children,out); } return out; }
function text(value) { return Array.isArray(value) ? value.map(text).join('') : value && typeof value === 'object' ? text(value.props.children) : value == null || value === false ? '' : String(value); }
function button(page, label) { return visit(page.tree).find(node => node.type === 'Button' && text(node) === label); }
function allText(page) { return visit(page.tree).filter(node => node.type === 'Text').map(text).join(' '); }
async function show(page) { current = page; page.didShow(); await settle(page); }
async function post(url, body, bearer = token) { const result = await request(url, 'POST', body, bearer); assert.equal(result.data.code, 0, JSON.stringify(result.data)); return result.data.data; }

async function main() {
  await post('/users/escorts/status/', {status: 'OFFLINE'});
  const oldMessage = mount(Message, {id: String(args.oldMessage)}); await settle(oldMessage);
  await button(oldMessage, '查看组队报名').props.onClick();
  assert.match(navigation, new RegExp(`orderId=${args.order}$`));
  const legacy = mount(Application, {orderId: String(args.order)}); await settle(legacy);
  assert.equal(visit(legacy.tree).filter(node => node.type === 'Button' && text(node).includes('待确认')).length, 2);
  const chosen = visit(legacy.tree).find(node => node.type === 'Button' && text(node).includes(args.firstApplicant));
  assert.ok(chosen); chosen.props.onClick(); await settle(legacy);
  assert.match(allText(legacy), /离线/); assert.equal(button(legacy, '确认组队').props.disabled, true);
  await button(legacy, '拒绝邀请').props.onClick(); await settle(legacy);
  assert.match(allText(legacy), /已拒绝邀请/);

  const exactMessage = mount(Message, {id: String(args.newMessage)}); await settle(exactMessage);
  await button(exactMessage, '查看组队报名').props.onClick();
  assert.match(navigation, new RegExp(`orderId=${args.order}&applicationId=${args.secondApplication}$`));
  const exact = mount(Application, {orderId: String(args.order), applicationId: String(args.secondApplication)}); await settle(exact);
  assert.match(allText(exact), /离线/);
  await post('/users/escorts/status/', {status: 'AVAILABLE'});
  const assigned = [];
  for (let i=0; i<3; i++) {
    const order = await post('/orders/create/', {service_id: args.singleService, dispatch_mode:'AUTO_GRAB', support_contact_id:args.support, client_request_id:'ui-capacity-' + i}, args.bossToken);
    await post(`/orders/orders/${order.id}/grab/`, {}); assigned.push(order.id);
  }
  await show(exact); assert.match(allText(exact), /名额已满/); assert.equal(button(exact, '确认组队').props.disabled, true);
  const png = Uint8Array.from(Buffer.from(args.image, 'base64'));
  for (const [action, field] of [['start', 'entry_image'], ['complete', 'completion_image']]) {
    const form = new FormData(); form.append(field, new Blob([png], {type:'image/png'}), 'screen.png');
    const response = await fetch(`${args.base}/api/orders/orders/${assigned[0]}/${action}/`, {method:'POST', headers:{Authorization:'Bearer ' + token}, body:form});
    const body = await response.json(); assert.equal(body.code, 0, JSON.stringify(body));
  }
  await show(exact); assert.equal(button(exact, '确认组队').props.disabled, false);
  await button(exact, '确认组队').props.onClick(); await settle(exact);
  assert.match(allText(exact), /队伍已确认，等待老板选择/);
  await post(`/orders/orders/${args.order}/applications/${args.secondApplication}/select/`, {}, args.bossToken);
  console.log(JSON.stringify({legacyChoices: 2, withdrawn: args.firstApplication, confirmed: args.secondApplication, selected: args.order}));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
