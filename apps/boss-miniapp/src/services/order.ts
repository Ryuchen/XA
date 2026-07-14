import { ApiResponse, request } from '@/utils/request';
import { EscortOrder, ServiceInfo } from '@/types/order';

export interface CreateOrderPayload {
  service_id?: number;
  product_id?: number;
  provider_id?: number;
  support_contact_id?: number;
  user_coupon_id?: number;
  game_rounds?: number;
  game_region?: string;
  game_nickname?: string;
  game_uid?: string;
  remark?: string;
}

export const fetchServices = (category?: string) => {
  const query = category ? `?category=${encodeURIComponent(category)}` : '';
  return request<ApiResponse<ServiceInfo[]>>(`/orders/services/${query}`, 'GET');
};

export interface GameCategoryInfo {
  id: number;
  name: string;
  remark: string;
}

export const fetchGameCategories = () => {
  return request<ApiResponse<GameCategoryInfo[]>>('/orders/game-categories/', 'GET');
};

export const fetchServiceDetail = (serviceId: number | string) => {
  return request<ApiResponse<ServiceInfo>>(`/orders/services/${serviceId}/`, 'GET');
};

export const createOrder = (payload: CreateOrderPayload) => {
  return request<ApiResponse<EscortOrder>>('/orders/create/', 'POST', payload);
};

export interface OrderQuote {
  original_amount: number;
  boss_discount: number;
  promo_discount: number;
  coupon_discount: number;
  amount: number;
  promotion: { id: number; title: string } | null;
  wallet_balance: number;
  balance_sufficient: boolean;
}

export const quoteOrder = (payload: CreateOrderPayload) => {
  return request<ApiResponse<OrderQuote>>('/orders/quote/', 'POST', payload);
};

export const fetchOrders = (params?: { status?: string; role?: string }) => {
  const parts: string[] = [];
  if (params?.status) parts.push(`status=${encodeURIComponent(params.status)}`);
  if (params?.role) parts.push(`role=${encodeURIComponent(params.role)}`);
  const qs = parts.join('&');
  return request<ApiResponse<EscortOrder[]>>(`/orders/orders/${qs ? '?' + qs : ''}`, 'GET');
};

export const cancelOrder = (orderId: number | string, reason?: string) => {
  return request<ApiResponse<EscortOrder>>(`/orders/orders/${orderId}/cancel/`, 'POST', { reason });
};

export const tipOrder = (orderId: number | string, giftServiceId: number) => {
  return request<ApiResponse<EscortOrder>>(`/orders/orders/${orderId}/tip/`, 'POST', {
    gift_service_id: giftServiceId,
  });
};

export const evaluateOrder = (payload: { order_id: number; score: number; content?: string; is_anonymous?: boolean }) => {
  return request<ApiResponse<unknown>>('/orders/evaluate/', 'POST', payload);
};

export interface Evaluation {
  id: number;
  order: number;
  order_no: string;
  score: number;
  content: string;
  is_anonymous: boolean;
  customer_name: string;
  customer_avatar: string;
  provider_name: string;
  service_name: string;
  reply_content: string;
  replied_at: string | null;
  created_at: string;
}

export interface EvaluationListData {
  list: Evaluation[];
  total: number;
  avg_score: number;
}

export const fetchServiceEvaluations = (serviceId: number | string) => {
  return request<ApiResponse<EvaluationListData>>(`/orders/services/${serviceId}/evaluations/`, 'GET');
};

export const fetchOrderStats = () => {
  return request<ApiResponse<{ total: number; pending: number; grabbed: number; in_service: number; completed: number; cancelled: number }>>('/orders/stats/', 'GET');
};
