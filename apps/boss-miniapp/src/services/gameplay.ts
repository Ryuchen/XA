import { request, ApiResponse } from '@/utils/request';
import { ServiceInfo } from '@/types/order';

export interface Gameplay {
  id: number; name: string; code: string; kind: 'STANDARD' | 'ROULETTE';
  subtitle: string; cover_url: string; theme: string; rules: string;
  service_count: number; starting_price: number | null;
}
export interface RouletteTarget { name: string; description: string; weight: number; probability: number }
export const fetchGameplays = () => request<ApiResponse<Gameplay[]>>('/orders/gameplays/');
export const fetchGameplayServices = (id: number, platform: string, level: string) =>
  request<ApiResponse<ServiceInfo[]>>(`/orders/services/?gameplay=${id}&platform=${platform}&security_level=${level}`);
