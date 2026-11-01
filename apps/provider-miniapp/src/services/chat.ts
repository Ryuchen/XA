import { ApiResponse, request, uploadFile } from '@/utils/request';

export interface ChatMessage {
  id: number; content: string; content_type: 'TEXT' | 'IMAGE'; image_url: string;
  is_from_support: boolean; is_read: boolean; created_at: string; client_message_id: string | null;
}
export interface ChatHistory {
  session: { id: number; unread_user: number }; messages: ChatMessage[];
  has_more: boolean; next_before_id: number | null;
}
export const fetchChat = (before?: number) => request<ApiResponse<ChatHistory>>(`/chat/session/${before ? `?before_id=${before}` : ''}`);
export const fetchChatUnread = () => request<ApiResponse<{ unread: number }>>('/chat/unread/');
export const sendChatText = (content: string, client_message_id: string) =>
  request<ApiResponse<ChatMessage>>('/chat/send/', 'POST', { content, client_message_id });
export const sendChatImage = (path: string, client_message_id: string, content: string) =>
  uploadFile<ApiResponse<ChatMessage>>('/chat/send/', path, { client_message_id, content }, 'image');
export const checkChatMessage = (id: string) =>
  request<ApiResponse<{ status: 'FOUND' | 'UNKNOWN'; message: ChatMessage | null }>>(`/chat/message-status/?client_message_id=${encodeURIComponent(id)}`);
export const readChatMessages = (ids: number[]) => request<ApiResponse<{ unread_user: number }>>('/chat/read/', 'POST', { message_ids: ids });
