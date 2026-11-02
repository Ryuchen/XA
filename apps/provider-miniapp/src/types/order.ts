export interface OrderRefundRecord {
  id: number;
  amount: number;
  created_at: string;
  reason: string;
  client_request_id?: string;
  recovered_amount?: number | null;
  referral_recovered_amount?: number | null;
  gift_naming_result?: { expires_at: string; remaining_paid_grants: number };
}
export interface OrderRefundSummary {
  amount: number;
  original_amount?: number;
  remaining_amount?: number;
  refund_count?: number;
  recovered_amount?: number | null;
  net_income?: number | null;
  referral_recovered_amount?: number | null;
  referral_net_income?: number | null;
  income_breakdown_unavailable?: boolean;
  remaining_frozen_amount?: number;
  records?: OrderRefundRecord[];
}
export interface ProviderOrder {
  reservation_origin?: { id: number; reservation_no: string; start_time: string; end_time: string; can_open: boolean } | null;
  dispatch_route?: 'PUBLIC' | 'STAFF';
  provider_requirement?: {game_category_id:number;provider_kind:string;platform:string;security_level:string;grade:string;label:string};
  dispatch_notification_enabled?:boolean;
  reject_count?: number;
  requires_boss_selection?: boolean;
  /** Required attendance from the saved sale snapshot, resolved by the API. */
  required_team_size?: 1 | 2;
  can_team_apply?: boolean;
  escort_mode?: 'SINGLE' | 'DOUBLE';
  participants?: Array<{ id: number; nickname: string }>;
  team_progress?: { stage: 'ENTRY' | 'COMPLETION'; submitted_count: number; total_count: number; members: Array<{ id: number; nickname: string; submitted: boolean }> } | null;
  /** Whether this provider's own assignment has been handed off. */
  my_assignment_released?: boolean | null;
  my_assignment_settled?: boolean | null;
  settlement_mode?: 'COMPLETION' | 'REPORT_APPROVAL';
  my_applications?: Array<{ id: number; team_size: number; status: string; ready: boolean; is_partner: boolean; partner_confirmed: boolean; members: Array<{ id: number; nickname: string; level: string }> }>;
  id: number;
  order_no: string;
  status: 'PENDING' | 'GRABBED' | 'IN_SERVICE' | 'COMPLETED' | 'CANCELLED';
  payment_status: string;
  aftersale_status?: 'OPEN' | 'RESOLVED' | 'REFUNDED' | 'PARTIALLY_REFUNDED' | '';
  cancel_reason?: string;
  refund_summary?: OrderRefundSummary | null;
  settled_at?: string | null;
  settlement_status?: 'REVIEW' | 'WAITING' | 'SETTLED';
  amount: number;
  expected_income: number | null;
  commission_rate: number;
  provider_gender?: 'ANY' | 'MALE' | 'FEMALE';
  game_rounds: number;
  /** Sale-time unit; old records without a saved unit use a neutral fallback. */
  quantity_unit?: string;
  game_region: string;
  game_nickname: string;
  game_uid: string;
  remark: string;
  fulfillment?: string;
  source_order?: number | null;
  /** Service/gift type frozen when the order was placed. */
  is_gift_snapshot?: boolean;
  /** Name saved when the customer placed the order. */
  service_name_snapshot?: string;
  service?: { id: number; name: string; is_gift?: boolean };
  customer?: {
    id: number;
    nickname: string;
    phone: string;
    boss_type?: { name: string; color: string; discount_rate: number } | null;
  };
  provider?: { id: number; nickname: string } | null;
  can_operate?: boolean;
  can_reject?: boolean;
  allowed_actions?: Array<'GRAB' | 'START' | 'COMPLETE' | 'REJECT'>;
  /** The server's current eligibility for creating or opening this order's report. */
  report_entry?: { available: boolean; reason: string } | null;
  created_at: string;
}

/** Older orders may not have a saved name. */
export const orderServiceName = (order: ProviderOrder): string =>
  order.service_name_snapshot || order.service?.name || '陪玩服务';

/** Older API versions do not return the sale-time snapshot. */
export const isGiftOrder = (order: ProviderOrder): boolean =>
  Boolean(order.source_order || (order.is_gift_snapshot ?? order.service?.is_gift));

export interface ProviderStats {
  today_orders: number;
  serving: number;
  active_orders: number;
  max_concurrent_orders: number;
  completion_rate: number;
  total_income: number;
}

export interface Evaluation {
  id: number;
  order: number;
  order_no: string;
  score: number;
  skill_score: number;
  attitude_score: number;
  communication_score: number;
  avg_score: number;
  content: string;
  is_anonymous: boolean;
  customer_name: string;
  customer_avatar: string;
  provider_name: string;
  service_name: string;
  reply_content: string;
  reply_author: 'PROVIDER' | 'STAFF' | '';
  replied_at: string | null;
  reply_version: number;
  created_at: string;
}

export interface EvaluationListData {
  list: Evaluation[];
  total: number;
  avg_score: number;
}

export const statusTextMap: Record<string, string> = {
  PENDING: '待接单',
  GRABBED: '已接单',
  IN_SERVICE: '服务中',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
};

export const REJECT_REASONS = ['时间冲突', '暂时无法服务', '订单信息不完整', '其他'] as const;
