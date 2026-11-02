import { ApiResponse, request } from '@/utils/request';

export interface Reservation {
  id: number; reservation_no: string; status: string; start_time: string; end_time: string;
  duration_minutes: number; game_rounds: number; game_region: string; game_nickname: string;
  game_uid: string; remark: string; cancel_reason: string; reject_reason: string;
  customer: { name: string }; service: { name: string }; order: number | null; order_no: string;
  allowed_actions: string[];
}
export interface ReservationCursor { start_time: string; id: number }
export interface ReservationPage { items: Reservation[]; total: number; page: number; page_size: number; next_cursor: ReservationCursor | null }
export const fetchReservations = (cursor: ReservationCursor | null = null) => request<ApiResponse<ReservationPage>>(
  `/orders/reservations/?role=provider&cursor=1&page_size=20${cursor ? `&before_time=${encodeURIComponent(cursor.start_time)}&before_id=${cursor.id}` : ''}`);
export const fetchReservation = (id: number) => request<ApiResponse<Reservation>>(`/orders/reservations/${id}/`);
export const reservationAction = (id: number, action: 'confirm' | 'reject' | 'cancel', reason = '') =>
  request<ApiResponse<Reservation>>(`/orders/reservations/${id}/${action}/`, 'POST', { reason });
