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

  let res: Taro.request.SuccessCallbackResult<T>;
  try {
    res = await Taro.request<T>({ url: `${BASE_URL}${url}`, method, data, header, timeout: REQUEST_TIMEOUT });
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
        clearStoredUser();
        Taro.showToast({ title: '登录已过期，请重新登录', icon: 'none' });
        return Promise.reject(error);
      }
    }
    return Promise.reject(new Error('Unauthorized'));
  }

  return res.data;
};

export { BASE_URL };
