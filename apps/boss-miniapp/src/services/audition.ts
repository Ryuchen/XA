import { ApiResponse, request } from '@/utils/request';

export type AuditionRole = 'boss' | 'provider';

export interface AuditionInfo {
  title: string;
  remark: string;
  expire_at: string | null;
  is_active: boolean;
  role: AuditionRole;
}

export interface AuditionExchangeData {
  token: string;
  userInfo: {
    id: string;
    username?: string;
    nickname?: string;
    role?: string;
    backendRole?: string;
    openid?: string | null;
    phone?: string | null;
    bindCode?: string | null;
    bindStatus?: 'direct' | 'bound';
  };
}

/** 免登录拉取试音活动信息：凭后台分享 token + 角色。 */
export const fetchAuditionInfo = (token: string, role: AuditionRole) => {
  const query = `token=${encodeURIComponent(token)}&role=${encodeURIComponent(role)}`;
  return request<ApiResponse<AuditionInfo>>(`/audition/info?${query}`, 'GET');
};

/** 免登录换发真 JWT：凭 token + 角色换取链接绑定用户的登录态。 */
export const exchangeAuditionToken = (token: string, role: AuditionRole) => {
  return request<ApiResponse<AuditionExchangeData>>('/audition/exchange', 'POST', { token, role });
};
