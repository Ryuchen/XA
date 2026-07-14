import { ApiResponse, request } from '@/utils/request';

export type BannerLinkType = 'NONE' | 'PRODUCT' | 'ANNOUNCEMENT' | 'URL';

export interface Banner {
  id: number;
  image_url: string;
  title: string;
  link_type: BannerLinkType;
  link_value: string;
}

export const fetchBanners = () => {
  return request<ApiResponse<Banner[]>>('/banners/', 'GET');
};
