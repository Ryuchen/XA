import Taro from '@tarojs/taro';
import {
  clearStoredUser,
  getStoredRefreshToken,
  getStoredToken,
  setStoredToken,
} from './auth';
import { BASE_URL } from './env';

type RequestMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface ApiResponse<T = unknown> {
  code: number;
  msg?: string;
  data?: T;
}

/** 请求超时（毫秒）：避免连不上时长时间挂起，并将超时归一为可捕获的错误 */
const REQUEST_TIMEOUT = 15000;
let refreshPromise: Promise<string> | null = null;

/** 登录态失效时的处理钩子。
 *
 * 由 store/user 在模块求值时注册为 logout()，使 401 能同时清掉
 * 内存里的登录态与账务缓存。这里用回调槽而非直接 import store，
 * 是为了避免 request → store → services → request 的循环依赖。
 * 未注册时退化为只清本地存储，行为与改造前一致。
 */
let onUnauthorized: (() => void) | null = null;
export const setUnauthorizedHandler = (handler: () => void) => {
  onUnauthorized = handler;
};

const refreshAccessToken = async (): Promise<string> => {
  const refresh = getStoredRefreshToken();
  if (!refresh) throw new Error('Missing refresh token');
  if (!refreshPromise) {
    refreshPromise = Taro.request<ApiResponse<{ token: string }>>({
      url: `${BASE_URL}/users/refresh/`,
      method: 'POST',
      data: { refresh },
      header: { 'Content-Type': 'application/json' },
      timeout: REQUEST_TIMEOUT,
    }).then((response) => {
      const token = response.data?.data?.token;
      if (response.statusCode !== 200 || response.data?.code !== 0 || !token) {
        throw new Error(response.data?.msg || 'Refresh failed');
      }
      setStoredToken(token);
      return token;
    }).finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
};

export const request = async <T = unknown>(
  url: string,
  method: RequestMethod = 'GET',
  data?: unknown,
  retried = false,
): Promise<T> => {
  const token = getStoredToken();
  const header: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) header.Authorization = `Bearer ${token}`;

  // Taro.request<T> 对 T 有 `string | IAnyObject | ArrayBuffer` 约束，
  // 而本函数的泛型 T 是未约束的（调用方传入 ApiResponse<X>）。
  // 因此内部用 any 接收响应，再断言回 SuccessCallbackResult<T>，避免把
  // 未约束泛型透传给带约束的 Taro 类型导致 TS2344。
  type RequestResult = { data: T; statusCode: number; header?: Record<string, unknown> };
  let res: RequestResult;
  try {
    const response = await Taro.request({ url: `${BASE_URL}${url}`, method, data, header, timeout: REQUEST_TIMEOUT });
    res = response as unknown as RequestResult;
  } catch (err) {
    // 网络失败/超时（request:fail timeout）在此归一处理，避免未捕获拒绝冒泡成全局 Error: timeout
    const message = (err as { errMsg?: string })?.errMsg || 'request:fail';
    return Promise.reject(new Error(message));
  }

  if (res.statusCode === 401) {
    if (token && !retried) {
      try {
        await refreshAccessToken();
        return request<T>(url, method, data, true);
      } catch (error) {
        // 优先走 store logout：清存储 + 清钱包/订单缓存 + 断开 WS，
        // 否则页面读到的仍是过期登录态。
        if (onUnauthorized) onUnauthorized();
        else clearStoredUser();
        Taro.showToast({ title: '登录已过期，请重新登录', icon: 'none' });
        return Promise.reject(error);
      }
    }
    return Promise.reject(new Error('Unauthorized'));
  }

  return res.data;
};

export { BASE_URL };
