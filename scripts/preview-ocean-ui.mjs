// Local visual review only. Real Vue screens, synthetic read-only data, no backend.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const root = path.resolve(import.meta.dirname, '..');
const appRoot = path.join(root, 'apps/admin-web');
const require = createRequire(path.join(appRoot, 'package.json'));
const { createServer } = await import(pathToFileURL(path.join(path.dirname(require.resolve('vite/package.json')), 'dist/node/index.js')).href);
const permissions = new Set();
function scan(dir) { for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
  const file = path.join(dir, item.name);
  if (item.isDirectory()) scan(file);
  else if (/\.(vue|ts)$/.test(file)) for (const match of fs.readFileSync(file, 'utf8').matchAll(/['"]([a-z_]+:[a-z_]+)['"]/g)) permissions.add(match[1]);
} }
scan(path.join(appRoot, 'src'));
const profile = { id: 900001, username: 'visual_review', nickname: '本地演示', avatar: '', roles: [], role: null, is_superuser: true, permissions: [...permissions] };
const orders = ['PENDING', 'GRABBED', 'IN_SERVICE', 'COMPLETED'].map((status, i) => ({
  id: 900010 + i, order_no: `DEMO-1028-00${i + 1}`, customer_name: `示例老板 ${i + 1}`,
  provider_name: i ? `示例陪玩 ${i}` : '', service_name_snapshot: ['常规陪玩', '双人陪玩', '趣味玩法', '常规陪玩'][i],
  amount: 18800 + i * 1000, status, payment_status: 'PAID', created_at: '2026-10-28T08:30:00+08:00',
  dispatch_route: 'PUBLIC', provider_slots: [], participants: [], allowed_actions: [],
}));
const requestModule = `export async function request(config) {
  const feePreview = config.method?.toLowerCase() === 'post' && config.url === '/config/payment-fee/preview/';
  if (config.method && config.method.toLowerCase() !== 'get' && !feePreview) throw new Error('本地视觉预览不提交业务数据');
  const url = config.url || ''; let data = { list: [], total: 0, page: 1, page_size: 10 };
  if (feePreview) {
    if (config.data?.service_fen !== 1234) throw new Error('本地固定示例仅验收 12.34 元，不进行实际费率计算');
    data = { revision:1, service_fen:1234, fee_fen:7, total_fen:1241 };
  }
  else if (url === '/config/payment-fee/') data = { configured:true, revision:1, merchant_id:'12345678', appid:'wx_local_preview', rate_percent:'0.6000', fee_basis:'SERVICE_AMOUNT', rounding:'HALF_UP' };
  else if (url.includes('/auth/profile')) data = ${JSON.stringify(profile)};
  else if (url.includes('/dashboard')) data = { total_orders: 128, today_orders: 12, total_revenue: 268800, today_revenue: 18800, total_customers: 68, total_providers: 24, pending_orders: 3, order_status: { PENDING: 3, GRABBED: 12, IN_SERVICE: 8, COMPLETED: 105 }, recent_orders: ${JSON.stringify(orders)} };
  else if (/^\\/orders\\/?$/.test(url)) data = { list: ${JSON.stringify(orders)}, total: 4 };
  else if (url.includes('traffic')) data = { mode: 'normal', self_order_enabled: true };
  return { code: 0, data };
} export default {};`;
const main = `import { createApp } from 'vue'; import { createPinia } from 'pinia';
import ElementPlus from 'element-plus'; import zhCn from 'element-plus/es/locale/lang/zh-cn';
import * as icons from '@element-plus/icons-vue'; import 'element-plus/dist/index.css';
import '/src/styles/index.scss'; import App from '/src/App.vue'; import router from '/src/router/index.ts';
import { useAuthStore } from '/src/stores/auth';
const app = createApp(App); const pinia = createPinia(); app.use(pinia);
const auth = useAuthStore(pinia); auth.token = 'synthetic-visual-review'; auth.profile = ${JSON.stringify(profile)};
for (const [name, icon] of Object.entries(icons)) app.component(name, icon);
app.use(router); app.use(ElementPlus, { locale: zhCn }); app.mount('#app');`;
const server = await createServer({ root: appRoot, configFile: path.join(appRoot, 'vite.config.ts'),
  server: { host: '127.0.0.1', port: 5190, strictPort: true },
  plugins: [{ name: 'ocean-local-review', enforce: 'pre',
    resolveId(id) { if (id === '/visual-review-main') return '\0visual-review-main'; },
    load(id) { if (id === '\0visual-review-main') return main; },
    transform(code, id) { if (id.replaceAll('\\', '/').endsWith('/src/api/request.ts')) return requestModule; },
    transformIndexHtml(html) { return html.replace(/src="\/src\/main\.ts(?:\?[^"\s]*)?"/, 'src="/visual-review-main"'); },
  }],
});
await server.listen(); console.log('Synthetic read-only UI review: http://127.0.0.1:5190/orders');
