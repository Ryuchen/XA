import { ApiResponse, request } from '@/utils/request';

export type MessageType = 'SYSTEM' | 'ORDER' | 'SUPPORT' | 'PROMOTION';
export interface SiteMessage {
  id: number; type: MessageType; title: string; preview: string; detail?: string;
  action_url: string; is_read: boolean; related_order_id: number | null; created_at: string;
}
export interface MessageListData { messages: SiteMessage[]; total: number; unread: number; page: number; page_size: number; }

export const fetchMessages = (page = 1, pageSize = 50) =>
  request<ApiResponse<MessageListData>>(`/messages/?page=${page}&page_size=${pageSize}`, 'GET');
export const fetchMessageDetail = (id: number | string) =>
  request<ApiResponse<SiteMessage>>(`/messages/${id}/`, 'GET');
export const markAllMessagesRead = () =>
  request<ApiResponse<unknown>>('/messages/read-all/', 'POST');
export const fetchUnreadCount = () =>
  request<ApiResponse<{ unread: number }>>('/messages/unread-count/', 'GET');
