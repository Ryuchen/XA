/**
 * XA 共享金额工具。
 *
 * 金额口径（与后端完全一致）：
 * - 后端全链路使用「内部账务单位」整数，10 个账务单位 = 1 兴安币。
 * - 后端任何环节都**不**做 ÷10 / ×10 的换算；换算只在「展示 / 输入」边界发生。
 * - 因此前端也必须在统一的边界函数里做换算，禁止在业务代码里写裸的
 *   `/ 10`、`* 10`、`/ 100` 等，防止再次出现「真实值被缩小 10 倍」的 bug。
 *
 * 调用方约定：
 * - 从后端拿到 / 传给后端的金额一律是「内部账务单位」整数。
 * - 仅在给用户看的时候用 {@link formatXaCoin} / {@link toCoin} 转成兴安币。
 * - 仅在读取用户输入（兴安币）时用 {@link toRawAmount} 转回内部账务单位。
 */

/** 1 兴安币对应的内部账务单位数量。 */
export const COIN_UNIT = 10;

/**
 * 内部账务单位 → 兴安币（浮点）。
 * @param amount 内部账务单位整数；非法输入按 0 处理。
 */
export function toCoin(amount: number | null | undefined): number {
  const value = Number(amount ?? 0);
  if (!Number.isFinite(value)) return 0;
  return value / COIN_UNIT;
}

/**
 * 兴安币 → 内部账务单位（四舍五入取整）。
 * @param coins 用户输入的兴安币数值（字符串或数字均可）；非法输入按 0 处理。
 */
export function toRawAmount(coins: number | string | null | undefined): number {
  const value = Number(coins);
  if (!Number.isFinite(value)) return 0;
  return Math.round(value * COIN_UNIT);
}

/**
 * 内部账务单位 → 兴安币展示字符串。
 * 整数不带小数，否则保留 1 位小数（如 125 → "12.5"）。
 */
export function formatXaCoin(amount: number | null | undefined): string {
  const coins = toCoin(amount);
  return Number.isInteger(coins) ? coins.toFixed(0) : coins.toFixed(1);
}
