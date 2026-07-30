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
  voice_card_url?: string;
  game_category_ids: number[];
  service_item_ids: number[];
}

/**
 * 拉取陪玩列表。
 * @param game 按游戏名模糊筛选（旧逻辑，靠 service_area/历史订单）
 * @param gameCategoryId 按游戏类目 ID 精准筛选（仅展示可接该游戏的陪玩）
 * @param serviceItemId 按服务项 ID 精准筛选（最细粒度，仅展示可接该服务项的陪玩）
 */
export const fetchEscorts = (game?: string, gameCategoryId?: number, serviceItemId?: number) => {
  const params: string[] = [];
  if (serviceItemId != null) params.push(`service_item=${serviceItemId}`);
  else if (gameCategoryId != null) params.push(`game_category=${gameCategoryId}`);
  else if (game) params.push(`game=${encodeURIComponent(game)}`);
  const query = params.length ? `?${params.join('&')}` : '';
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
