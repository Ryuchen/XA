export type EscortOrderStatus = 'pending' | 'grabbed' | 'in_progress' | 'completed' | 'cancelled';
export interface OrderRefundRecord {
  id: number;
  amount: number;
  created_at: string;
  reason: string;
  client_request_id?: string;
  recovered_amount?: number;
}
export interface OrderRefundSummary {
  id?: number;
  amount: number;
  original_amount?: number;
  remaining_amount?: number;
  refund_count?: number;
  recovered_amount?: number;
  created_at?: string;
  reason?: string;
  records?: OrderRefundRecord[];
}

export interface OrderUser {
  id: number;
  nickname: string;
  phone?: string;
}

export interface ServiceInfo {
  service_type?: string;
  gameplay?: number; gameplay_name?: string; gameplay_kind?: string;
  gameplay_theme?: string; gameplay_cover_url?: string;
  platform?: string; platform_name?: string; security_level?: string; security_level_name?: string; rules?: string;
  roulette_pool?: import('@/services/gameplay').RouletteTarget[];
  id: number;
  name: string;
  description: string;
  price: number;
  category?: string;
  cover_url?: string;
  images?: string[];
  introduction_images?: string[];
  details_html?: string;
  details_images?: string[];
  variants?: ServiceInfo[];
  catalog_family?: string;
  catalog_variant?: string;
  is_addon?: boolean;
  purchase_mode?: string;
  highlights?: string[];
  sales_count?: number;
  is_favorited?: boolean;
  game_category?: number | null;
  game_category_name?: string;
  service_category?: number | null;
  service_category_name?: string;
  is_gift?: boolean;
}

export interface EscortOrder {
  fulfillment?: string;
  gameplay_snapshot?: { name: string; platform_name: string; security_level_name: string; rules: string; pool?: import('@/services/gameplay').RouletteTarget[] };
  roulette_result?: { index: number; name: string; description: string };
  id: number;
  order_no: string;
  status: EscortOrderStatus;
  payment_status: string;
  amount: number;
  original_amount: number;
  boss_discount: number;
  promo_discount: number;
  coupon_discount: number;
  provider_gender?: 'ANY' | 'MALE' | 'FEMALE';
  game_rounds: number;
  game_region: string;
  game_nickname: string;
  game_uid: string;
  support_contact?: { id: number; name: string; avatar_url: string } | null;
  remark: string;
  service: ServiceInfo;
  customer: OrderUser;
  provider: OrderUser | null;
  is_evaluated: boolean;
  evaluation: {
    id: number;
    score: number;
    skill_score: number;
    attitude_score: number;
    communication_score: number;
    content: string;
    reply_content: string;
    replied_at: string | null;
    created_at: string;
  } | null;
  allowed_actions?: Array<'CANCEL' | 'EVALUATE' | 'TIP'>;
  source_order?: number | null;
  grabbed_at: string | null;
  in_service_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
  refunded_at: string | null;
  aftersale_deadline?: string | null;
  aftersale_status?: 'OPEN' | 'RESOLVED' | 'PARTIALLY_REFUNDED' | 'REFUNDED' | '';
  can_aftersale?: boolean;
  refund_summary?: OrderRefundSummary | null;
  consumption_confirmed_at?: string | null;
  cancel_reason: string;
  auto_cancel_at: string | null;
  reject_count: number;
  created_at: string;
  updated_at: string;
}

export type OrderPageRole = 'customer' | 'provider';

export const statusTextMap: Record<EscortOrderStatus, string> = {
  pending: '待接单',
  grabbed: '已接单',
  in_progress: '服务中',
  completed: '已完成',
  cancelled: '已取消',
};

export const statusBackendMap: Record<string, string> = {
  pending: 'PENDING',
  grabbed: 'GRABBED',
  in_progress: 'IN_SERVICE',
  completed: 'COMPLETED',
  cancelled: 'CANCELLED',
};

// 后端状态枚举 → 前端状态字符串
export const normalizeStatus = (raw: string): EscortOrderStatus => {
  const map: Record<string, EscortOrderStatus> = {
    PENDING: 'pending',
    GRABBED: 'grabbed',
    IN_SERVICE: 'in_progress',
    COMPLETED: 'completed',
    CANCELLED: 'cancelled',
  };
  return map[raw] || (raw as EscortOrderStatus) || 'pending';
};

// 陪玩师拒单预设原因（最后一项触发自由输入）
export const REJECT_REASONS = ['不熟悉该局', '时间冲突', '账号风险', '其他'] as const;
export type RejectReason = typeof REJECT_REASONS[number];

// 客户取消预设原因
export const CANCEL_REASONS = ['不想要了', '下单错误', '等待太久', '其他'] as const;
