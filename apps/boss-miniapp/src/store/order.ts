import { create } from 'zustand';
import { fetchOrderStats } from '@/services/order';
import { getStoredToken } from '@/utils/auth';
import { TTL, dedupe, isFresh } from './shared';

export interface OrderStats {
  pending: number;
  grabbed: number;
  in_service: number;
  completed: number;
}

const EMPTY_STATS: OrderStats = { pending: 0, grabbed: 0, in_service: 0, completed: 0 };

/**
 * 订单状态统计缓存（「我的」页四宫格）。
 *
 * 下单、取消、WebSocket 推送订单状态变更时调用 invalidate()，
 * 其余情况沿用 TTL 缓存，避免每次回到「我的」都重拉。
 */
interface OrderState {
  stats: OrderStats;
  fetchedAt: number;
  load: (force?: boolean) => Promise<OrderStats>;
  invalidate: () => void;
  reset: () => void;
}

export const useOrderStore = create<OrderState>((set, get) => ({
  stats: EMPTY_STATS,
  fetchedAt: 0,

  load: async (force = false) => {
    if (!getStoredToken()) return EMPTY_STATS;
    const { stats, fetchedAt } = get();
    if (!force && isFresh(fetchedAt, TTL.ACCOUNT)) return stats;

    try {
      const res = await dedupe('order:stats', fetchOrderStats);
      if (res.code === 0 && res.data) {
        const next: OrderStats = {
          pending: res.data.pending || 0,
          grabbed: res.data.grabbed || 0,
          in_service: res.data.in_service || 0,
          completed: res.data.completed || 0,
        };
        set({ stats: next, fetchedAt: Date.now() });
        return next;
      }
      return get().stats;
    } catch {
      return get().stats;
    }
  },

  invalidate: () => set({ fetchedAt: 0 }),
  reset: () => set({ stats: EMPTY_STATS, fetchedAt: 0 }),
}));
