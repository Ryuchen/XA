import Taro from '@tarojs/taro';
import { clearStoredUser, getStoredToken } from './auth';
import { BASE_URL } from './env';

type RequestMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface ApiResponse<T = unknown> {
  code: number;
  msg?: string;
  data?: T;
}

const handleUnauthorized = () => {
  clearStoredUser();
  Taro.showToast({ title: '登录已过期', icon: 'none' });
  setTimeout(() => {
    Taro.reLaunch({ url: '/pages/login/index' });
  }, 300);
};

export const request = async <T = unknown>(url: string, method: RequestMethod = 'GET', data?: unknown): Promise<T> => {
  const token = getStoredToken();
  const header: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) header.Authorization = `Bearer ${token}`;

  const res = await Taro.request<T>({ url: `${BASE_URL}${url}`, method, data, header });

  if (res.statusCode === 401) {
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
  });

  if (res.statusCode === 401) {
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
