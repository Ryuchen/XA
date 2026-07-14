import { ApiResponse, request } from '@/utils/request';

export type CouponDiscountType = 'THRESHOLD' | 'DIRECT';
export type UserCouponStatus = 'UNUSED' | 'USED' | 'EXPIRED';

export interface Coupon {
  id: number;
  name: string;
  discount_type: CouponDiscountType;
  threshold: number;
  amount: number;
  valid_to: string;
  is_claimed: boolean;
  claimable: boolean;
}

export interface UserCoupon {
  id: number;
  name: string;
  discount_type: CouponDiscountType;
  threshold: number;
  amount: number;
  valid_to: string;
  status: UserCouponStatus;
  claimed_at: string;
  used_at: string | null;
}

export const fetchCoupons = () => {
  return request<ApiResponse<Coupon[]>>('/coupons/', 'GET');
};

export const claimCoupon = (couponId: number) => {
  return request<ApiResponse<unknown>>(`/coupons/${couponId}/claim/`, 'POST');
};

export const fetchMyCoupons = (params?: { usable?: boolean; amount?: number }) => {
  const parts: string[] = [];
  if (params?.usable) parts.push('usable=1');
  if (params?.amount != null) parts.push(`amount=${params.amount}`);
  const qs = parts.length ? `?${parts.join('&')}` : '';
  return request<ApiResponse<UserCoupon[]>>(`/coupons/mine/${qs}`, 'GET');
};
