import { ApiResponse, request } from '@/utils/request';
import { EvaluationListData, ProviderOrder, ProviderStats } from '@/types/order';

/** 订单列表。status=pending 且 role=provider 时返回待接单池。 */
export const fetchOrders = (params?: { status?: string; role?: string }) => {
  const parts: string[] = [];
  if (params?.status) parts.push(`status=${encodeURIComponent(params.status)}`);
  if (params?.role) parts.push(`role=${encodeURIComponent(params.role)}`);
  const qs = parts.join('&');
  return request<ApiResponse<ProviderOrder[]>>(`/orders/orders/${qs ? '?' + qs : ''}`, 'GET');
};

export const grabOrder = (orderId: number | string) => {
  return request<ApiResponse<ProviderOrder>>(`/orders/orders/${orderId}/grab/`, 'POST');
};

export const startOrder = (orderId: number | string) => {
  return request<ApiResponse<ProviderOrder>>(`/orders/orders/${orderId}/start-service/`, 'POST');
};

export const completeOrder = (orderId: number | string) => {
  return request<ApiResponse<ProviderOrder>>(`/orders/orders/${orderId}/complete/`, 'POST');
};

export const rejectOrder = (orderId: number | string, reason?: string) => {
  return request<ApiResponse<ProviderOrder>>(`/orders/orders/${orderId}/reject/`, 'POST', { reason });
};

export const fetchProviderStats = () => {
  return request<ApiResponse<ProviderStats>>('/users/provider-stats/', 'GET');
};

export const fetchMyEvaluations = () => {
  return request<ApiResponse<EvaluationListData>>('/orders/evaluations/mine/', 'GET');
};

export const replyEvaluation = (evaluationId: number | string, replyContent: string) => {
  return request<ApiResponse<unknown>>(`/orders/evaluations/${evaluationId}/reply/`, 'POST', {
    reply_content: replyContent,
  });
};
