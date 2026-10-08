import { toRawAmount } from './format';
import type { WithdrawOverview } from '@/services/wallet';

export type WithdrawQuote = WithdrawOverview & { amount: number; tax_amount: number; actual_amount: number };

/** 输入边界保留已确认的 0.1 币粒度；不截断文本，也不将多余小数静默四舍五入。 */
export function parseWithdrawAmount(input: string): number {
  const text = input.trim();
  if (!/^\d+(\.\d)?$/.test(text)) return 0;
  const amount = toRawAmount(text);
  return Number.isSafeInteger(amount) && amount > 0 && amount <= 2147483647 ? amount : 0;
}

/** 只接受对应本次输入的完整服务端试算；不自行算税，也不把缺失字段当作零。 */
export function validWithdrawQuote(amount: number, data?: WithdrawOverview | null): data is WithdrawQuote {
  if (!data || amount <= 0 || data.amount !== amount) return false;
  const values = [data.amount, data.tax_amount, data.actual_amount, data.tax_rate, data.min_amount, data.balance];
  return values.every(value => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0)
    && data.tax_rate <= 100 && data.tax_amount! + data.actual_amount! === amount;
}
