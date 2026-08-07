/** store 层公共工具：TTL 判定 + 并发请求去重。 */

/** 缓存是否仍然新鲜 */
export const isFresh = (fetchedAt: number, ttl: number): boolean =>
  fetchedAt > 0 && Date.now() - fetchedAt < ttl;

const inflight = new Map<string, Promise<unknown>>();

/**
 * 同一 key 的请求在飞行期间复用同一个 Promise，
 * 避免多个页面在同一帧同时 load 造成重复网络请求。
 */
export const dedupe = <T>(key: string, factory: () => Promise<T>): Promise<T> => {
  const running = inflight.get(key);
  if (running) return running as Promise<T>;
  const task = factory().finally(() => inflight.delete(key));
  inflight.set(key, task);
  return task;
};

/** 常用 TTL（毫秒） */
export const TTL = {
  /** 账务类：切 tab 回来 30s 内不重拉 */
  ACCOUNT: 30 * 1000,
  /** 目录类（服务、游戏分类、客服卡）：基本静态，5 分钟 */
  CATALOG: 5 * 60 * 1000,
} as const;
