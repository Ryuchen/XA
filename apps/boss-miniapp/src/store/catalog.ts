import { create } from 'zustand';
import { GameCategoryInfo, fetchGameCategories, fetchServices } from '@/services/order';
import { SupportContactCard, fetchContactCards } from '@/services/support';
import { ServiceInfo } from '@/types/order';
import { TTL, dedupe, isFresh } from './shared';

/**
 * 平台目录数据缓存（服务列表 / 游戏分类 / 客服名片）。
 *
 * 这几类数据基本静态，却被「服务」「陪玩」「自助下单」「结算」「订单」
 * 等多个页面各自重复拉取。统一收敛到这里，5 分钟内跨页面复用。
 */
const ALL = '__ALL__';

interface CatalogState {
  /** key 为服务分类，未传分类时用 __ALL__ */
  services: Record<string, ServiceInfo[]>;
  servicesAt: Record<string, number>;
  gameCategories: GameCategoryInfo[];
  gameCategoriesAt: number;
  contactCards: SupportContactCard[];
  contactCardsAt: number;

  loadServices: (category?: string, force?: boolean) => Promise<ServiceInfo[]>;
  loadGameCategories: (force?: boolean) => Promise<GameCategoryInfo[]>;
  loadContactCards: (force?: boolean) => Promise<SupportContactCard[]>;
  /** 后台改了服务/价格时可整体失效 */
  invalidate: () => void;
}

export const useCatalogStore = create<CatalogState>((set, get) => ({
  services: {},
  servicesAt: {},
  gameCategories: [],
  gameCategoriesAt: 0,
  contactCards: [],
  contactCardsAt: 0,

  loadServices: async (category, force = false) => {
    const key = category || ALL;
    const { services, servicesAt } = get();
    if (!force && isFresh(servicesAt[key] || 0, TTL.CATALOG)) return services[key] || [];

    try {
      const res = await dedupe(`catalog:services:${key}`, () => fetchServices(category));
      if (res.code === 0 && Array.isArray(res.data)) {
        set(state => ({
          services: { ...state.services, [key]: res.data as ServiceInfo[] },
          servicesAt: { ...state.servicesAt, [key]: Date.now() },
        }));
        return res.data;
      }
      return get().services[key] || [];
    } catch {
      return get().services[key] || [];
    }
  },

  loadGameCategories: async (force = false) => {
    if (!force && isFresh(get().gameCategoriesAt, TTL.CATALOG)) return get().gameCategories;
    try {
      const res = await dedupe('catalog:gameCategories', fetchGameCategories);
      if (res.code === 0 && Array.isArray(res.data)) {
        set({ gameCategories: res.data, gameCategoriesAt: Date.now() });
        return res.data;
      }
      return get().gameCategories;
    } catch {
      return get().gameCategories;
    }
  },

  loadContactCards: async (force = false) => {
    if (!force && isFresh(get().contactCardsAt, TTL.CATALOG)) return get().contactCards;
    try {
      const res = await dedupe('catalog:contactCards', fetchContactCards);
      if (res.code === 0 && Array.isArray(res.data)) {
        set({ contactCards: res.data, contactCardsAt: Date.now() });
        return res.data;
      }
      return get().contactCards;
    } catch {
      return get().contactCards;
    }
  },

  invalidate: () => set({ servicesAt: {}, gameCategoriesAt: 0, contactCardsAt: 0 }),
}));

// 稳定的空数组引用，避免选择器每次返回新数组触发无谓重渲染
const EMPTY_SERVICES: ServiceInfo[] = [];

/** 订阅某个分类的服务列表（配合 loadServices 使用） */
export const useServices = (category?: string): ServiceInfo[] =>
  useCatalogStore(state => state.services[category || ALL] || EMPTY_SERVICES);
