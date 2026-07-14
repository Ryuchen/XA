import Taro from '@tarojs/taro';
import { clearStoredUser, getStoredToken } from './auth';
import { BASE_URL } from './env';

type RequestMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface ApiResponse<T = unknown> {
  code: number;
  msg?: string;
  data?: T;
}

export const request = async <T = unknown>(url: string, method: RequestMethod = 'GET', data?: unknown): Promise<T> => {
  const token = getStoredToken();
  const header: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) header.Authorization = `Bearer ${token}`;

  const res = await Taro.request<T>({ url: `${BASE_URL}${url}`, method, data, header });

  if (res.statusCode === 401) {
    // 仅当本地存有 token（会话真实过期）时才提示并清除登录态；
    // 匿名浏览（无 token）不弹提示，下单时再由 ensureLogin 弹出登录窗
    if (token) {
      clearStoredUser();
      Taro.showToast({ title: '登录已过期，请重新登录', icon: 'none' });
    }
    return Promise.reject(new Error('Unauthorized'));
  }

  return res.data;
};

export { BASE_URL };
