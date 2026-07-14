import { ApiResponse, request, uploadFile } from '@/utils/request';

export type EscortStatus = 'AVAILABLE' | 'BUSY' | 'OFFLINE';

export interface EscortMeProfile {
  display_name: string;
  gender: string;
  bio: string;
  city: string;
  service_area: string;
  price_per_hour: number;
  rank_tier: string;
  status: string;
  is_verified: boolean;
  rating_avg: number;
  rating_count: number;
  completed_order_count: number;
  avatar_url: string;
  intro_video_url: string;
  voice_card_url: string;
  cheat_proof_url: string;
}

export interface UpdateEscortProfilePayload {
  display_name?: string;
  gender?: 'MALE' | 'FEMALE' | 'UNKNOWN';
  bio?: string;
  city?: string;
  service_area?: string;
}

export interface EscortScheduleSlot {
  weekday: number;
  start_minute: number;
  end_minute: number;
}

export const fetchEscortMe = () => {
  return request<ApiResponse<EscortMeProfile>>('/users/escorts/me/', 'GET');
};

export const updateEscortProfile = (payload: UpdateEscortProfilePayload) => {
  return request<ApiResponse<EscortMeProfile>>('/users/escorts/me/', 'PATCH', payload);
};

export const uploadEscortAsset = (
  field: 'avatar' | 'intro_video' | 'voice_card' | 'cheat_proof',
  filePath: string,
) => uploadFile<ApiResponse<EscortMeProfile>>(
  '/users/escorts/me/', filePath, {}, field,
);

export const updateEscortStatus = (status: EscortStatus) => {
  return request<ApiResponse<unknown>>('/users/escorts/status/', 'POST', { status });
};

export const fetchEscortSchedule = () => {
  return request<ApiResponse<EscortScheduleSlot[]>>('/users/escorts/schedule/', 'GET');
};

export const saveEscortSchedule = (slots: EscortScheduleSlot[]) => {
  return request<ApiResponse<EscortScheduleSlot[]>>('/users/escorts/schedule/', 'PUT', { slots });
};

export interface PassProduct {
  tier: 'BLACK' | 'GOLD' | 'SILVER' | 'BRONZE';
  name: string;
  daily_price: number;
  delay_seconds: number;
}

export interface ProviderPassData {
  balance: number;
  active_tier: string;
  active_tier_display: string;
  expires_at: string | null;
  current_delay_seconds: number;
  no_pass_delay_seconds: number;
  products: PassProduct[];
  history: Array<{
    id: number; tier: string; tier_display: string; days: number;
    paid_amount: number; starts_at: string; expires_at: string; created_at: string;
  }>;
}

export const fetchProviderPass = () =>
  request<ApiResponse<ProviderPassData>>('/users/provider-pass/', 'GET');

export const purchaseProviderPass = (tier: PassProduct['tier']) =>
  request<ApiResponse<ProviderPassData>>('/users/provider-pass/', 'POST', { tier, days: 1 });
