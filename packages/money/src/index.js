/**
 * XA 共享金额工具（纯 JS 实现，配套 index.d.ts 提供类型）。
 *
 * 为什么是 .js 而不是 .ts：
 * - 本包被 Taro（webpack5）与 Vite 同时消费。Taro 的 TS/babel loader 只覆盖各
 *   app 的 sourceRoot（`apps/*\/src`），包目录下的 `.ts` 源码会直接撞上没有
 *   loader 的 webpack，类型注解被当成语法错误（ModuleParseError）。
 * - 写成不带类型注解的 ESM `.js`，webpack / Vite / esbuild 都能原生解析，
 *   零构建步骤、零 loader 配置，与 `packages/design-system` 的「纯资源包」
 *   定位保持一致。类型信息由同目录的 `index.d.ts` 提供给 tsc / vue-tsc。
 *
 * 金额口径（与后端完全一致）：
 * - 后端全链路使用「内部账务单位」整数，10 个账务单位 = 1 兴安币。
 * - 后端任何环节都**不**做 ÷10 / ×10 的换算；换算只在「展示 / 输入」边界发生。
 * - 因此前端也必须在统一的边界函数里做换算，禁止在业务代码里写裸的
 *   `/ 10`、`* 10`、`/ 100` 等，防止再次出现「真实值被缩小 10 倍」的 bug。
 *
 * 调用方约定：
 * - 从后端拿到 / 传给后端的金额一律是「内部账务单位」整数。
 * - 仅在给用户看的时候用 formatXaCoin / toCoin 转成兴安币。
 * - 仅在读取用户输入（兴安币）时用 toRawAmount 转回内部账务单位。
 */

/**
 * 1 兴安币对应的内部账务单位数量。
 * @type {number}
 */
export const COIN_UNIT = 10;

/**
 * 内部账务单位 → 兴安币（浮点）。
 *
 * @param {number | null | undefined} amount 内部账务单位整数；非法输入按 0 处理。
 * @returns {number} 兴安币数值。
 */
export function toCoin(amount) {
  const value = Number(amount == null ? 0 : amount);
  if (!Number.isFinite(value)) return 0;
  return value / COIN_UNIT;
}

/**
 * 兴安币 → 内部账务单位（四舍五入取整）。
 *
 * @param {number | string | null | undefined} coins 用户输入的兴安币数值（字符串或数字均可）；非法输入按 0 处理。
 * @returns {number} 内部账务单位整数。
 */
export function toRawAmount(coins) {
  const value = Number(coins);
  if (!Number.isFinite(value)) return 0;
  return Math.round(value * COIN_UNIT);
}

/**
 * 内部账务单位 → 兴安币展示字符串。
 * 整数不带小数，否则保留 1 位小数（如 125 → "12.5"）。
 *
 * @param {number | null | undefined} amount 内部账务单位整数。
 * @returns {string} 用于展示的兴安币字符串。
 */
export function formatXaCoin(amount) {
  const coins = toCoin(amount);
  return Number.isInteger(coins) ? coins.toFixed(0) : coins.toFixed(1);
}
