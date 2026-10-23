import Taro from '@tarojs/taro';
import { scopeFor, cooldownRemaining, recordOrderRequest } from './orderNetwork';
import {
  clearStoredUser,
  getStoredRefreshToken,
  getStoredToken,
  getSessionRevision,
  sessionChangedError,
  setStoredToken,
} from './auth';
import { BASE_URL } from './env';

type RequestMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface ApiResponse<T = unknown> {
  code: number;
  msg?: string;
  errorCode?: string;
  retry_after?: number;
  traffic_mode?: string;
  data?: T;
}

const REQUEST_TIMEOUT = 15000;
let refreshing: { revision: number; operation: Promise<string> } | null = null;

const assertSession = (revision: number) => {
  if (getSessionRevision() !== revision) throw sessionChangedError();
};
const unauthorized = () => Object.assign(new Error('登录已过期，请重新登录'), { status: 401 });
const handleUnauthorized = (revision: number) => {
  if (getSessionRevision() !== revision) return;
  clearStoredUser();
  const clearedRevision = getSessionRevision();
  Taro.showToast({ title: '登录已过期', icon: 'none' });
  setTimeout(() => {
    if (getSessionRevision() === clearedRevision && !getStoredToken()) {
      Taro.reLaunch({ url: '/pages/login/index' });
    }
  }, 300);
};

const refreshAccessToken = async (revision: number): Promise<string> => {
  assertSession(revision);
  const refresh = getStoredRefreshToken();
  if (!refresh) throw unauthorized();
  if (!refreshing || refreshing.revision !== revision) {
    const operation = Taro.request<ApiResponse<{ token: string }>>({
      url: `${BASE_URL}/users/refresh/`,
      method: 'POST',
      data: { refresh },
      header: { 'Content-Type': 'application/json' },
      timeout: REQUEST_TIMEOUT,
    }).then((response) => {
      assertSession(revision);
      const token = response.data?.data?.token;
      if (response.statusCode !== 200 || response.data?.code !== 0 || !token) {
        throw Object.assign(new Error(response.data?.msg || '登录凭证刷新失败，请稍后重试'), { status: response.statusCode });
      }
      setStoredToken(token);
      return token;
    });
    refreshing = { revision, operation };
    operation.finally(() => { if (refreshing?.operation === operation) refreshing = null; }).catch(() => {});
  }
  return refreshing.operation;
};

const recoverToken = async (revision: number, token: string) => {
  assertSession(revision);
  try {
    if (getStoredToken() === token) await refreshAccessToken(revision);
    assertSession(revision);
  } catch (error) {
    assertSession(revision);
    // Only a definitive authentication failure ends the session. Timeouts and
    // 5xx responses may recover without forcing the user to log in again.
    if ((error as { status?: number })?.status === 401) handleUnauthorized(revision);
    throw error;
  }
};

export const request = async <T = unknown>(
  url: string,
  method: RequestMethod = 'GET',
  data?: unknown,
  retried = false,
): Promise<T> => {
  const revision = getSessionRevision();
  const token = getStoredToken();
  const header: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) header.Authorization = `Bearer ${token}`;

  // Taro.request<T> 对 T 有 `string | IAnyObject | ArrayBuffer` 约束，
  // 而本函数的泛型 T 是未约束的（调用方传入 ApiResponse<X>），故用局部类型接收。
  type RequestResult = { data: T; statusCode: number; header?: Record<string, unknown> };
  const wait = cooldownRemaining(scopeFor(url), revision);
  if (wait) throw Object.assign(new Error(`请求较多，请在 ${wait} 秒后重试`), { status: 429, retry_after: wait });
  const started = Date.now();
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
    assertSession(revision);
    // 网络失败 / 超时（request:fail timeout）在此归一处理，
    // 避免未捕获拒绝冒泡成全局 Error: timeout。
    recordOrderRequest(url, Date.now() - started, 0, revision);
    const message = (err as { errMsg?: string })?.errMsg || 'request:fail';
    return Promise.reject(new Error(message));
  }

  assertSession(revision);
  const retryAfter = Number((res.data as ApiResponse)?.retry_after || res.header?.['Retry-After'] || 1);
  recordOrderRequest(url, Date.now() - started, res.statusCode, revision, retryAfter);
  if (res.statusCode === 401) {
    if (token && !retried) {
      await recoverToken(revision, token);
      assertSession(revision);
      return request<T>(url, method, data, true);
    }
    handleUnauthorized(revision);
    throw unauthorized();
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
  fileName?: string,
): Promise<T> => {
  const revision = getSessionRevision();
  const token = getStoredToken();
  const header: Record<string, string> = {};
  if (token) header.Authorization = `Bearer ${token}`;

  // Taro H5 reads a chosen object URL back as Blob, losing File.name. Its
  // fallback `file-<timestamp>` has no extension and Django rejects otherwise
  // valid images. Preserve supplied names; recover the extension from the
  // browser's Blob MIME for anonymous local image uploads.
  if (!fileName && filePath.startsWith('blob:') && process.env.TARO_ENV === 'h5') {
    const response = await fetch(filePath, { credentials: 'omit' });
    if (!response.ok) throw new Error('图片读取失败，请重新选择');
    const blob = await response.blob();
    assertSession(revision);
    const extension = ({
      'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp',
      'image/gif': 'gif', 'image/bmp': 'bmp', 'image/tiff': 'tiff',
      'image/x-icon': 'ico', 'image/vnd.microsoft.icon': 'ico',
    } as Record<string, string>)[blob.type.toLowerCase()];
    if (extension) fileName = `upload.${extension}`;
  }

  const res = await Taro.uploadFile({
    url: `${BASE_URL}${url}`,
    filePath,
    fileName,
    name: fileKey,
    formData,
    header,
    timeout: REQUEST_TIMEOUT,
    // 使用 Bearer token 鉴权，不依赖 cookie。H5 端 Taro.uploadFile 默认
    // withCredentials=true（带凭证），会与后端 Access-Control-Allow-Origin:*
    // 冲突导致预检后实际 POST 被浏览器拦截；显式关闭凭证模式即可正常上传。
    withCredentials: false,
  });

  assertSession(revision);
  if (res.statusCode === 401) {
    if (token && !retried) {
      await recoverToken(revision, token);
      assertSession(revision);
      return uploadFile<T>(url, filePath, formData, fileKey, true, fileName);
    }
    handleUnauthorized(revision);
    throw unauthorized();
  }

  try {
    return JSON.parse(res.data) as T;
  } catch (error) {
    throw new Error('响应解析失败');
  }
};

export { BASE_URL };
