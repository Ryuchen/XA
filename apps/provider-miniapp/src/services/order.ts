import { ApiResponse, request, uploadFile } from '@/utils/request';
import { Evaluation, EvaluationListData, ProviderOrder, ProviderStats } from '@/types/order';
import { orderAlerts } from './orderAlerts';

/** 订单列表。status=pending 且 role=provider 时返回待接单池。 */
export const fetchOrders = (params?: { status?: string; role?: string }) => {
  if (params?.status === 'pending' && params.role === 'provider') return orderAlerts.loadPending();
  const parts: string[] = [];
  if (params?.status) parts.push(`status=${encodeURIComponent(params.status)}`);
  if (params?.role) parts.push(`role=${encodeURIComponent(params.role)}`);
  const qs = parts.join('&');
  return request<ApiResponse<ProviderOrder[]>>(`/orders/orders/${qs ? '?' + qs : ''}`, 'GET');
};

export const grabOrder = (orderId: number | string, teamSize: number = 1, partnerId?: number) => {
  return request<ApiResponse<ProviderOrder>>(`/orders/orders/${orderId}/grab/`, 'POST', { team_size: teamSize, ...(partnerId ? { partner_id: partnerId } : {}) });
};

/** 开始服务：必须上传入队截图。 */
export const startOrder = (orderId: number | string, entryImagePath: string) => {
  return uploadFile<ApiResponse<ProviderOrder>>(
    `/orders/orders/${orderId}/start-service/`, entryImagePath, {}, 'entry_image',
  );
};

/** 完成订单：必须上传结单截图。 */
export const completeOrder = (orderId: number | string, completionImagePath: string) => {
  return uploadFile<ApiResponse<ProviderOrder>>(
    `/orders/orders/${orderId}/complete/`, completionImagePath, {}, 'completion_image',
  );
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
  return request<ApiResponse<Evaluation>>(`/orders/evaluations/${evaluationId}/reply/`, 'POST', {
    reply_content: replyContent,
  });
};

export interface Teammate { id: number; nickname: string; level: string; pass_tier: string }
export const fetchTeammates = (orderId: number) => request<ApiResponse<Teammate[]>>(`/orders/orders/${orderId}/teammates/`);
export const applicationAction = (orderId: number, applicationId: number, action: 'CONFIRM_TEAM' | 'WITHDRAW') => request<ApiResponse<ProviderOrder>>(`/orders/orders/${orderId}/applications/${applicationId}/action/`, 'POST', { action });
