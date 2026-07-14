import { ApiResponse, request } from '@/utils/request';

export interface Announcement {
  id: number;
  title: string;
  content: string;
  is_pinned: boolean;
  created_at: string;
  updated_at?: string;
}

export const fetchAnnouncements = (limit = 5) => {
  return request<ApiResponse<Announcement[]>>(`/announcements/?limit=${limit}`, 'GET');
};

export const fetchAnnouncementDetail = (id: number | string) => {
  return request<ApiResponse<Announcement>>(`/announcements/${id}/`, 'GET');
};
