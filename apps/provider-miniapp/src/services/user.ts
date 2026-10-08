import { ApiResponse, request, uploadFile } from '@/utils/request';
import { fundRequest } from '@/utils/fundRequest';

export type EscortStatus = 'AVAILABLE' | 'BUSY' | 'OFFLINE';

export interface ProviderQualification { id:number; game_category:number; game_category_name:string; provider_kind:string; platform:string; security_level:string; grade:string; grade_name:string; label:string; is_active:boolean }
export interface EscortMeProfile {
  qualifications: ProviderQualification[];
  dispatch_notification_preferences: Record<string,boolean>;
  dispatch_notification_options: Array<{key:string;label:string;eligible:boolean;enabled:boolean}>;
  active_pass_tier:string;
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
  game_category_ids: number[];
  service_item_ids: number[];
}

export interface UpdateEscortProfilePayload {
  dispatch_notification_preferences?: Record<string,boolean>;
  display_name?: string;
  gender?: 'MALE' | 'FEMALE' | 'UNKNOWN';
  bio?: string;
  city?: string;
  service_area?: string;
  remove_voice_card?: boolean;
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
  fileName?: string,
) => uploadFile<ApiResponse<EscortMeProfile>>(
  '/users/escorts/me/', filePath, {}, field, false, fileName,
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

export interface SkillOptionItem {
  qualified?:boolean;
  provider_requirement?: {label:string};
  id: number;
  name: string;
  price: number;
  price_unit: string;
  required_level_name: string;
  platform: string;
  platform_name: string;
  security_level: string;
  security_level_name: string;
}

export interface SkillOptionGroup {
  game_category_id: number;
  game_category_name: string;
  items: SkillOptionItem[];
}

export const fetchSkillOptions = () =>
  request<ApiResponse<SkillOptionGroup[]>>('/users/escorts/skill-options/', 'GET');

export const updateEscortSkills = (serviceItemIds: number[]) =>
  request<ApiResponse<EscortMeProfile>>('/users/escorts/me/', 'PATCH', {
    service_items: serviceItemIds,
  });

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

export const purchaseProviderPass = (tier: PassProduct['tier'], expectedDailyPrice: number) =>
  fundRequest<ProviderPassData>('/users/provider-pass/', { tier, days: 1, expected_daily_price: expectedDailyPrice });
