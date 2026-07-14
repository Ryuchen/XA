import { ApiResponse, request } from '@/utils/request';

export interface ConsumeRankItem {
  rank: number;
  user_id: number;
  nickname: string;
  avatar: string;
  total_amount: number;
  order_count: number;
  title?: string;
}

export interface OrderRankItem {
  rank: number;
  user_id: number;
  nickname: string;
  avatar: string;
  order_count: number;
  total_amount: number;
  title?: string;
}

export interface RankingData {
  period?: string;
  consume_rank: ConsumeRankItem[];
  order_rank: OrderRankItem[];
}

export type RankingPeriod = 'day' | 'week' | 'month';

export const fetchRankings = (period: RankingPeriod = 'month') => {
  return request<ApiResponse<RankingData>>(`/orders/rankings/?period=${period}`, 'GET');
};
