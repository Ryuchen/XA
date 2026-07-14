import { ApiResponse, request } from '@/utils/request';

export interface EscortProfile {
  id: number;
  nickname: string;
  avatar: string;
  rank: string;
  rating: number;
  ratingCount: number;
  favorableRate: number;
  orderCount: number;
  winRate: number;
  pricePerHour: number;
  bio: string;
  city: string;
  status: string;
  is_verified: boolean;
}

export const fetchEscorts = (game?: string) => {
  const query = game ? `?game=${encodeURIComponent(game)}` : '';
  return request<ApiResponse<EscortProfile[]>>(`/users/escorts/${query}`, 'GET');
};

export interface CustomerAchievement {
  code: string;
  title: string;
  desc: string;
  icon: string;
  unlocked: boolean;
  current: number;
  target: number;
}

export const fetchCustomerAchievements = () => {
  return request<ApiResponse<CustomerAchievement[]>>('/users/achievements/', 'GET');
};

export interface MeProfile {
  id: string;
  username: string;
  nickname: string;
  role: string;
  phone: string;
  avatar: string;
  game_region: string;
  game_nickname: string;
  game_uid: string;
  game_profiles: CustomerGameProfile[];
}

export interface CustomerGameProfile {
  game_category: number;
  game_category_name: string;
  region: string;
  nickname: string;
  uid: string;
  is_default: boolean;
}

export interface UpdateMePayload {
  nickname?: string;
  phone?: string;
  game_region?: string;
  game_nickname?: string;
  game_uid?: string;
  game_profiles?: Array<{
    game_category: number;
    region: string;
    nickname: string;
    uid: string;
    is_default?: boolean;
  }>;
}

export const fetchMe = () => {
  return request<ApiResponse<MeProfile>>('/users/me/', 'GET');
};

export const updateMe = (payload: UpdateMePayload) => {
  return request<ApiResponse<MeProfile>>('/users/me/', 'PATCH', payload);
};
