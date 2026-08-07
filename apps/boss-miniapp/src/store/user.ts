import { create } from 'zustand';
import {
  LoginUser,
  clearStoredUser,
  getStoredToken,
  getStoredUser,
  setStoredUser,
} from '@/utils/auth';
import { setUnauthorizedHandler } from '@/utils/request';
import { wsService } from '@/services/websocket';
import { useWalletStore } from './wallet';
import { useOrderStore } from './order';

/**
 * 登录态单一真源。
 *
 * 本地存储仍然保留（冷启动、request 拦截器取 token 需要），
 * 但页面一律读 store，登录/登出可即时广播到所有已挂载页面，
 * 不再依赖 useDidShow 里重复 getStoredUser()。
 */
interface UserState {
  user: LoginUser | null;
  token: string;
  /** 登录成功或资料更新后调用：同步内存 + 落盘 */
  setUser: (user: LoginUser | null) => void;
  /** 从本地存储同步，用于冷启动与外部直接写过存储的兜底场景 */
  syncFromStorage: () => void;
  /** 退出登录：清存储 + 清账务类缓存 + 断开实时推送 */
  logout: () => void;
}

export const useUserStore = create<UserState>((set, get) => ({
  user: getStoredUser(),
  token: getStoredToken(),

  setUser: (user) => {
    const hadUser = !!get().user;
    if (user) setStoredUser(user);
    set({ user, token: getStoredToken() });
    // 由「未登录」变为「已登录」时补连实时推送：
    // app.onShow 只覆盖冷启动/切后台回来，登录后不切后台的话订单推送收不到。
    // 资料更新（已有 user 再 setUser）不触发，避免无谓重连。
    if (user && !hadUser) wsService.connect();
  },

  syncFromStorage: () => set({ user: getStoredUser(), token: getStoredToken() }),

  logout: () => {
    clearStoredUser();
    useWalletStore.getState().reset();
    useOrderStore.getState().reset();
    // 登出后必须断开：否则旧连接仍持有已失效 token，继续推送他人订单变更
    wsService.disconnect();
    set({ user: null, token: '' });
  },
}));

/** 非组件环境（service、工具函数）判断登录态 */
export const isLoggedIn = (): boolean => !!useUserStore.getState().token;

// 401 且 refresh 失败时，让请求层能够触发完整登出（而不只是清存储）。
// 依赖方向：store/user → utils/request（request 不反向 import store），不成环。
setUnauthorizedHandler(() => useUserStore.getState().logout());
