import { ApiResponse, request } from '@/utils/request';
import { ServiceInfo } from '@/types/order';

export const fetchFavorites = () => {
  return request<ApiResponse<ServiceInfo[]>>('/orders/favorites/', 'GET');
};

export const toggleFavorite = (serviceId: number) => {
  return request<ApiResponse<{ favorited: boolean }>>('/orders/favorites/toggle/', 'POST', {
    service_id: serviceId,
  });
};
