/**
 * 金额换算 —— 与后端账务单位保持统一的唯一入口。
 *
 * 后端所有金额字段（wallet.balance、order.amount / original_amount / *_discount、
 * coupon.amount、service.price、recharge.amount ...）均为「内部账务单位」整数：
 *
 *     10 内部账务单位 = 1 兴安币
 *
 * 参见 services/backend/wallet/models.py 与 orders/models.py 的字段注释。
 *
 * 约定：
 *  - 前端只在「展示层」换算成兴安币，业务计算一律保持内部账务单位；
 *  - 任何提交给后端的金额必须是内部账务单位（需要时用 toRawAmount 转换）；
 *  - 禁止在页面里出现裸的 /10、*10。
 */

/** 1 兴安币 = 多少内部账务单位 */
export const COIN_UNIT = 10;

/** 内部账务单位 -> 兴安币数值 */
export const toCoin = (raw?: number | null): number => (raw || 0) / COIN_UNIT;

/** 兴安币 -> 内部账务单位（提交后端用，四舍五入取整避免浮点误差） */
export const toRawAmount = (coin?: number | null): number => Math.round((coin || 0) * COIN_UNIT);

/** 内部账务单位 -> 兴安币展示文案：整数不带小数，非整数保留 1 位 */
export const formatXaCoin = (raw?: number | null): string => {
  const coins = toCoin(raw);
  return Number.isInteger(coins) ? coins.toFixed(0) : coins.toFixed(1);
};

/** 内部账务单位 -> 带单位的展示文案，例如 "12.5兴安币" */
export const formatXaCoinText = (raw?: number | null, unit = '兴安币'): string =>
  `${formatXaCoin(raw)}${unit}`;
