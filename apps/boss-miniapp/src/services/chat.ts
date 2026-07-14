import Taro from '@tarojs/taro';
import { ApiResponse, request } from '@/utils/request';
import { getStoredToken } from '@/utils/auth';
import { BASE_URL } from '@/utils/env';

export type ChatContentType = 'TEXT' | 'IMAGE';

export interface ChatMessage {
  id: number;
  is_from_support: boolean;
  content_type: ChatContentType;
  content_type_display: string;
  content: string;
  image_url: string;
  is_read: boolean;
  created_at: string;
}

export interface ChatSession {
  id: number;
  last_message: string;
  last_message_at: string | null;
  unread_user: number;
  created_at: string;
}

export interface ChatSessionData {
  session: ChatSession;
  messages: ChatMessage[];
}

/** WS 推送 chat_message 事件载荷。 */
export interface ChatMessagePush {
  session_id: number;
  user_id: number;
  message: ChatMessage;
}

export const fetchChatSession = () => {
  return request<ApiResponse<ChatSessionData>>('/chat/session/', 'GET');
};

export const sendChatText = (content: string) => {
  return request<ApiResponse<ChatMessage>>('/chat/send/', 'POST', { content });
};

export const markChatRead = () => {
  return request<ApiResponse<{ unread_user: number }>>('/chat/read/', 'POST');
};

/** 发送图片消息：用 multipart 上传。 */
export const sendChatImage = (filePath: string): Promise<ApiResponse<ChatMessage>> => {
  const token = getStoredToken();
  return new Promise((resolve, reject) => {
    Taro.uploadFile({
      url: `${BASE_URL}/chat/send/`,
      filePath,
      name: 'image',
      header: token ? { Authorization: `Bearer ${token}` } : {},
      success: (res) => {
        try {
          const data = typeof res.data === 'string' ? JSON.parse(res.data) : res.data;
          resolve(data as ApiResponse<ChatMessage>);
        } catch (e) {
          reject(new Error('返回数据解析失败'));
        }
      },
      fail: () => reject(new Error('图片上传失败')),
    });
  });
};
