import { ApiResponse, request } from '@/utils/request';

export interface AuditionInfo {
  id: number;
  title: string;
  remark: string;
  expire_at: string | null;
  is_active: boolean;
  role: 'provider';
}

export interface AuditionLoginData {
  token: string;
  userInfo: {
    id: string;
    username?: string;
    nickname?: string;
    role?: string;
    phone?: string | null;
  };
}

export interface AuditionSignup {
  id: number;
  link: number;
  link_title: string;
  contact: string;
  game: string;
  remark: string;
  status: 'PENDING' | 'APPROVED' | 'REJECTED';
  status_display: string;
  audit_remark: string;
  audited_at: string | null;
  created_at: string;
}

export const fetchAuditionInfo = (token: string) =>
  request<ApiResponse<AuditionInfo>>(`/audition/info?token=${encodeURIComponent(token)}&role=provider`, 'GET');

export const exchangeAuditionToken = (token: string) =>
  request<ApiResponse<AuditionLoginData>>('/audition/exchange', 'POST', { token, role: 'provider' });

export const submitAuditionSignup = (payload: { token: string; contact: string; game: string; remark: string }) =>
  request<ApiResponse<{ id: number; status: string }>>('/audition/signup', 'POST', payload);

export const fetchMyAuditionSignups = () =>
  request<ApiResponse<AuditionSignup[]>>('/audition/my-signups', 'GET');
