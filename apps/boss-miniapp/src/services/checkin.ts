import { ApiResponse, request } from '@/utils/request';

export interface CheckinReward {
  seq: number;
  amount: number;
  name: string;
  description: string;
  icon: string;
}

export interface CheckinGiftInfo {
  name: string;
  description: string;
  icon: string;
}

export interface CheckinCalendar {
  year: number;
  month: number;
  days_in_month: number;
  today: number;
  today_checked: boolean;
  checked_days: number[];
  checked_count: number;
  continuous_days: number;
  rewards: CheckinReward[];
  next_reward: number;
  next_gift: CheckinGiftInfo;
  balance: number;
  today_spend: number;
  daily_spend_required: number;
  makeup_card_spend_required: number;
  today_eligible: boolean;
  makeup_cards: number;
  max_makeup_cards: number;
  card_earned_today: boolean;
  makeup_available_days: number[];
  full_attendance: boolean;
  full_attendance_reward: CheckinGiftInfo & { tag_code: string };
}

export interface CheckinResult {
  reward_amount: number;
  gift: CheckinGiftInfo;
  seq_in_month: number;
  checked_count: number;
  balance: number;
  makeup_cards: number;
  full_attendance_awarded: boolean;
}

export const fetchCheckinCalendar = () => {
  return request<ApiResponse<CheckinCalendar>>('/users/checkin/', 'GET');
};

export const doCheckin = () => {
  return request<ApiResponse<CheckinResult>>('/users/checkin/', 'POST');
};

export const doMakeupCheckin = (day: number) => {
  return request<ApiResponse<CheckinResult>>('/users/checkin/', 'POST', { day });
};
