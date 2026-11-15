/**
 * XA 共享金额工具（纯 JS 实现，配套 index.d.ts 提供类型）。
 *
 * 为什么是 .js 而不是 .ts：
 * - 本包被 Taro（webpack5）与 Vite 同时消费。Taro 的 TS/babel loader 只覆盖各
 *   app 的 sourceRoot（`apps/*\/src`），包目录下的 `.ts` 源码会直接撞上没有
 *   loader 的 webpack，类型注解被当成语法错误（ModuleParseError）。
 * - 写成不带类型注解的 ESM `.js`，webpack / Vite / esbuild 都能原生解析，
 *   零构建步骤、零 loader 配置。类型信息由同目录的 `index.d.ts` 提供。
 *
 * 金额口径（与后端完全一致）：
 * - 后端全链路使用「内部账务单位」整数，10 个账务单位 = 1 浪花。
 * - 后端任何环节都**不**做 ÷10 / ×10 的换算；换算只在「展示 / 输入」边界发生。
 * - 因此前端也必须在统一的边界函数里做换算，禁止在业务代码里写裸的
 *   `/ 10`、`* 10`、`/ 100` 等，防止再次出现「真实值被缩小 10 倍」的 bug。
 *
 * 调用方约定：
 * - 从后端拿到 / 传给后端的金额一律是「内部账务单位」整数。
 * - 仅在给用户看的时候用 formatXaCoin / toCoin 转成浪花。
 * - 仅在读取用户输入（浪花）时用 toRawAmount 转回内部账务单位。
 * - 微信手续费接口另用人民币分；仅用 parseRmbFen / formatRmbFen 处理，
 *   不通过浪花或内部账务单位的汇率换算。
 */

/**
 * 1 浪花对应的内部账务单位数量。
 * @type {number}
 */
export const COIN_UNIT = 10;
export const RMB_TO_COIN_RATE = 10;

/** 人民币元文本 → 人民币分；与浪花/账务单位的汇率独立，不接受隐式舍入。 */
export function parseRmbFen(yuan) {
  const text = String(yuan == null ? '' : yuan);
  if (!/^(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(text)) return 0;
  const [whole, fraction = ''] = text.split('.');
  const fen = Number(whole + fraction.padEnd(2, '0'));
  return Number.isSafeInteger(fen) ? fen : 0;
}

/** 人民币分整数 → 人民币元，两位小数；不经过浪花换算。 */
export function formatRmbFen(fen) {
  const value = Number(fen == null ? 0 : fen);
  if (!Number.isSafeInteger(value)) return '0.00';
  const digits = String(Math.abs(value)).padStart(3, '0');
  return (value < 0 ? '-' : '') + digits.slice(0, -2) + '.' + digits.slice(-2);
}

/** 人民币元 → 内部账务单位；仅用于人工收款确认的输入边界。 */
export function rmbToRawAmount(yuan) {
  const value = Number(yuan);
  return Number.isFinite(value) ? Math.round(value * RMB_TO_COIN_RATE * COIN_UNIT) : 0;
}

/** 内部账务单位 → 人民币元展示，供人工收款、打款核对。 */
export function formatRmb(amount) {
  return (toCoin(amount) / RMB_TO_COIN_RATE).toFixed(2);
}

/**
 * 内部账务单位 → 浪花（浮点）。
 *
 * @param {number | null | undefined} amount 内部账务单位整数；非法输入按 0 处理。
 * @returns {number} 浪花数值。
 */
export function toCoin(amount) {
  const value = Number(amount == null ? 0 : amount);
  if (!Number.isFinite(value)) return 0;
  return value / COIN_UNIT;
}

/**
 * 浪花 → 内部账务单位（四舍五入取整）。
 *
 * @param {number | string | null | undefined} coins 用户输入的浪花数值（字符串或数字均可）；非法输入按 0 处理。
 * @returns {number} 内部账务单位整数。
 */
export function toRawAmount(coins) {
  const value = Number(coins);
  if (!Number.isFinite(value)) return 0;
  return Math.round(value * COIN_UNIT);
}

/**
 * 内部账务单位 → 浪花展示字符串。
 * 保留最多一位店币小数并使用千分位（如 13206 → "1,320.6"）。
 *
 * @param {number | null | undefined} amount 内部账务单位整数。
 * @returns {string} 用于展示的浪花字符串。
 */
export function formatXaCoin(amount) {
  const coins = toCoin(amount);
  return coins.toLocaleString('en-US', { maximumFractionDigits: 1 });
}
