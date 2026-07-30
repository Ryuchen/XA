import { ApiResponse, request } from '@/utils/request';

// ---------------- 流水 ----------------
export interface TransactionRecord {
  id: number;
  tx_no: string;
  amount: number;
  tx_type: string;
  balance_before: number;
  balance_after: number;
  status: string;
  remark: string;
  created_at: string;
}

export interface TransactionsResponse {
  transactions: TransactionRecord[];
  total: number;
  page: number;
  page_size: number;
  balance: number;
  frozen_amount: number;
}

export const fetchTransactions = (params?: { tx_type?: string; page?: number; page_size?: number }) => {
  const parts: string[] = [];
  if (params?.tx_type) parts.push(`tx_type=${encodeURIComponent(params.tx_type)}`);
  if (params?.page) parts.push(`page=${params.page}`);
  if (params?.page_size) parts.push(`page_size=${params.page_size}`);
  const qs = parts.join('&');
  return request<ApiResponse<TransactionsResponse>>(`/wallet/transactions/${qs ? '?' + qs : ''}`, 'GET');
};

// ---------------- 提现 ----------------
export type PayeeMethod = 'WECHAT' | 'ALIPAY' | 'BANK';
export type WithdrawStatus = 'PENDING' | 'APPROVED' | 'REJECTED';

export interface WithdrawRecord {
  id: number;
  amount: number;
  tax_rate: number;
  tax_amount: number;
  actual_amount: number;
  payee_method: PayeeMethod;
  payee_method_display: string;
  payee_account: string;
  payee_name: string;
  status: WithdrawStatus;
  status_display: string;
  remark: string;
  audit_remark: string;
  created_at: string;
  audited_at: string | null;
}

export interface WithdrawOverview {
  balance: number;
  frozen_amount: number;
  min_amount: number;
  tax_rate: number;
  requests: WithdrawRecord[];
}

export interface WithdrawPayload {
  amount: number;
  payee_method: PayeeMethod;
  payee_account: string;
  payee_name: string;
  remark?: string;
}

export const fetchWithdrawOverview = () => {
  return request<ApiResponse<WithdrawOverview>>('/wallet/withdraw/', 'GET');
};

export const withdrawRequest = (payload: WithdrawPayload) => {
  return request<ApiResponse<{ balance: number; frozen_amount: number; request: WithdrawRecord }>>(
    '/wallet/withdraw/',
    'POST',
    payload,
  );
};

// ---------------- 押金 ----------------
export interface DepositOverview {
  deposit_required: number;
  deposit_paid: number;
  deposit_remaining: number;
  balance: number;
}

export const fetchDepositInfo = () => {
  return request<ApiResponse<DepositOverview>>('/wallet/deposit/', 'GET');
};

export const payDeposit = (amount: number) => {
  return request<ApiResponse<DepositOverview>>('/wallet/deposit/', 'POST', { amount });
};

// ---------------- 奖惩与礼物 ----------------
export interface DisposeRecord {
  id: number;
  type: 'REWARD' | 'PENALTY';
  type_display: string;
  amount: number;
  signed_amount: number;
  reason: string;
  created_at: string;
}

export interface GiftRecord {
  id: number;
  order_no: string;
  service_name: string;
  customer_name: string;
  amount: number;
  provider_income: number;
  completed_at: string;
}

export interface ProviderBenefits {
  summary: {
    total_reward: number;
    total_penalty: number;
    total_gift_amount: number;
    total_gift_income: number;
    gift_count: number;
  };
  dispose_records: DisposeRecord[];
  gift_records: GiftRecord[];
}

export const fetchProviderBenefits = () =>
  request<ApiResponse<ProviderBenefits>>('/wallet/provider-benefits/', 'GET');
