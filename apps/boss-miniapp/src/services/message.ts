import { ApiResponse, request } from '@/utils/request';

export type MessageType = 'SYSTEM' | 'ORDER' | 'SUPPORT' | 'PROMOTION';

export interface SiteMessage {
  id: number;
  type: MessageType;
  title: string;
  preview: string;
  detail?: string;
  action_url: string;
  is_read: boolean;
  related_order_id: number | null;
  created_at: string;
}

export const fetchMessages = (params?: { type?: MessageType; page?: number; page_size?: number }) => {
  const parts: string[] = [];
  if (params?.type) parts.push(`type=${encodeURIComponent(params.type)}`);
  if (params?.page) parts.push(`page=${params.page}`);
  if (params?.page_size) parts.push(`page_size=${params.page_size}`);
  const qs = parts.join('&');
  return request<ApiResponse<{ messages: SiteMessage[]; total: number; page: number; page_size: number; unread: number }>>(
    `/messages/${qs ? '?' + qs : ''}`,
    'GET',
  );
};

export const fetchMessageDetail = (messageId: number | string) => {
  return request<ApiResponse<SiteMessage>>(`/messages/${messageId}/`, 'GET');
};

export const markAllMessagesRead = () => {
  return request<ApiResponse<{ updated: number }>>('/messages/read-all/', 'POST');
};

export const fetchUnreadCount = () => {
  return request<ApiResponse<{ unread: number }>>('/messages/unread-count/', 'GET');
};
