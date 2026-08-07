import { create } from 'zustand';
import { fetchWalletInfo } from '@/services/wallet';
import { getStoredToken } from '@/utils/auth';
import { TTL, dedupe, isFresh } from './shared';

/**
 * 钱包余额缓存。
 *
 * 余额同时被「我的」「钱包」「结算」页读取，原先每次 useDidShow 都会重拉。
 * 这里用 TTL + 请求去重收敛：切 tab 回来 30s 内直接复用，
 * 下单/充值/签到等会改变余额的动作显式调用 invalidate() 或 setBalance()。
 */
interface WalletState {
  balance: number;
  loading: boolean;
  fetchedAt: number;
  /** 拉取余额；命中新鲜缓存时直接返回不发请求 */
  load: (force?: boolean) => Promise<number>;
  /** 已知最新余额时直接写入（例如交易列表接口顺带返回了 balance） */
  setBalance: (balance: number) => void;
  /** 标记缓存过期，下次 load 必然重拉 */
  invalidate: () => void;
  reset: () => void;
}

export const useWalletStore = create<WalletState>((set, get) => ({
  balance: 0,
  loading: false,
  fetchedAt: 0,

  load: async (force = false) => {
    if (!getStoredToken()) return 0;
    const { balance, fetchedAt } = get();
    if (!force && isFresh(fetchedAt, TTL.ACCOUNT)) return balance;

    set({ loading: true });
    try {
      const res = await dedupe('wallet:info', fetchWalletInfo);
      if (res.code === 0 && res.data) {
        set({ balance: res.data.balance, fetchedAt: Date.now() });
        return res.data.balance;
      }
      return get().balance;
    } catch {
      return get().balance;
    } finally {
      set({ loading: false });
    }
  },

  setBalance: (balance) => set({ balance, fetchedAt: Date.now() }),
  invalidate: () => set({ fetchedAt: 0 }),
  reset: () => set({ balance: 0, loading: false, fetchedAt: 0 }),
}));
