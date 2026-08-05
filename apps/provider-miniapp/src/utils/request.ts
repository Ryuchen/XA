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

const REQUEST_TIMEOUT = 15000;
let refreshPromise: Promise<string> | null = null;

const handleUnauthorized = () => {
  clearStoredUser();
  Taro.showToast({ title: '登录已过期', icon: 'none' });
  setTimeout(() => {
    Taro.reLaunch({ url: '/pages/login/index' });
  }, 300);
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
  // 而本函数的泛型 T 是未约束的（调用方传入 ApiResponse<X>），故用局部类型接收。
  type RequestResult = { data: T; statusCode: number; header?: Record<string, unknown> };
  let res: RequestResult;
  try {
    res = await Taro.request<T>({
      url: `${BASE_URL}${url}`,
      method,
      data,
      header,
      timeout: REQUEST_TIMEOUT,
    });
  } catch (err) {
    // 网络失败 / 超时（request:fail timeout）在此归一处理，
    // 避免未捕获拒绝冒泡成全局 Error: timeout。
    const message = (err as { errMsg?: string })?.errMsg || 'request:fail';
    return Promise.reject(new Error(message));
  }

  if (res.statusCode === 401) {
    if (token && !retried) {
      try {
        await refreshAccessToken();
        return request<T>(url, method, data, true);
      } catch (error) {
        handleUnauthorized();
        return Promise.reject(error);
      }
    }
    handleUnauthorized();
    return Promise.reject(new Error('Unauthorized'));
  }

  return res.data;
};

/** 表单上传：用于报单凭证图片等 multipart 场景。 */
export const uploadFile = async <T = unknown>(
  url: string,
  filePath: string,
  formData: Record<string, string | number>,
  fileKey = 'proof_image',
  retried = false,
): Promise<T> => {
  const token = getStoredToken();
  const header: Record<string, string> = {};
  if (token) header.Authorization = `Bearer ${token}`;

  const res = await Taro.uploadFile({
    url: `${BASE_URL}${url}`,
    filePath,
    name: fileKey,
    formData,
    header,
    timeout: REQUEST_TIMEOUT,
    // 使用 Bearer token 鉴权，不依赖 cookie。H5 端 Taro.uploadFile 默认
    // withCredentials=true（带凭证），会与后端 Access-Control-Allow-Origin:*
    // 冲突导致预检后实际 POST 被浏览器拦截；显式关闭凭证模式即可正常上传。
    withCredentials: false,
  });

  if (res.statusCode === 401) {
    if (token && !retried) {
      try {
        await refreshAccessToken();
        return uploadFile<T>(url, filePath, formData, fileKey, true);
      } catch (error) {
        handleUnauthorized();
        return Promise.reject(error);
      }
    }
    handleUnauthorized();
    return Promise.reject(new Error('Unauthorized'));
  }

  try {
    return JSON.parse(res.data) as T;
  } catch (error) {
    throw new Error('响应解析失败');
  }
};

export { BASE_URL };
