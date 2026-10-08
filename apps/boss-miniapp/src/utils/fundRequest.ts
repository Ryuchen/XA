import Taro from '@tarojs/taro';
import { getStoredUser } from './auth';
import { ApiResponse, request } from './request';

const pending = new Map<string, Promise<ApiResponse<unknown>>>();
type FundResponse<T> = ApiResponse<T> & { errorCode?: string; fundResult?: ApiResponse<T> };

/** 弱网重试保留编号；取得成功回执后才能结束交易，未知重复结果仍需核对。 */
export function fundRequest<T>(url: string, payload: object): Promise<ApiResponse<T>> {
  const owner = getStoredUser()?.id;
  if (!owner) return Promise.reject(new Error('请先登录'));
  const key = `xa-fund:${owner}:${url}:${JSON.stringify(payload)}`;
  const running = pending.get(key);
  if (running) return running as Promise<ApiResponse<T>>;
  let id: string;
  try {
    id = Taro.getStorageSync(key) || `fund-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    Taro.setStorageSync(key, id);
  } catch {
    return Promise.reject(new Error('无法保存交易请求，请检查设备存储后重试'));
  }
  const operation = request<FundResponse<T>>(url, 'POST', { ...payload, client_request_id: id })
    .then(result => {
      if (getStoredUser()?.id !== owner) throw new Error('账号已切换，请刷新页面');
      const confirmed = result.code === 409 && result.errorCode === 'FUND_ALREADY_PROCESSED'
        && result.fundResult?.code === 0 ? result.fundResult : result;
      if (confirmed.code === 0 || (confirmed.code >= 400 && confirmed.code < 500 && confirmed.code !== 409 && confirmed.code !== 429)) {
        try { Taro.removeStorageSync(key); } catch { /* 保留编号可防止重复扣款 */ }
      }
      return confirmed;
    })
    .finally(() => { pending.delete(key); });
  pending.set(key, operation);
  return operation;
}
