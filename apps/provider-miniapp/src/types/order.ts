export interface ProviderOrder {
  id: number;
  order_no: string;
  status: 'PENDING' | 'GRABBED' | 'IN_SERVICE' | 'COMPLETED' | 'CANCELLED';
  payment_status: string;
  amount: number;
  expected_income: number;
  commission_rate: number;
  game_rounds: number;
  game_region: string;
  game_nickname: string;
  game_uid: string;
  remark: string;
  service?: { id: number; name: string };
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
  created_at: string;
}

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
  replied_at: string | null;
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
