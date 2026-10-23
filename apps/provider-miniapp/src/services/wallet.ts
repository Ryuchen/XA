import { ApiResponse, request } from '@/utils/request';
import { fundRequest } from '@/utils/fundRequest';

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
  reversal_of?: number | null;
  is_reversal?: boolean;
}

export interface TransactionsResponse {
  total_income: number;
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
  payout_reference: string;
  paid_at: string | null;
}

export interface WithdrawOverview {
  balance: number;
  frozen_amount: number;
  min_amount: number;
  balance_threshold?: number | null;
  can_withdraw?: boolean;
  tax_rate: number;
  requests: WithdrawRecord[];
  /** T8-a 试算字段：仅当调用时传入 amount 才返回。 */
  amount?: number;
  tax_amount?: number;
  actual_amount?: number;
}

export interface SavedWithdrawalAccount {
  id: number;
  payee_method: PayeeMethod;
  payee_account: string;
  payee_name: string;
  version: number;
  updated_at: string;
}

export const fetchWithdrawalAccounts = () => request<ApiResponse<SavedWithdrawalAccount[]>>('/wallet/withdraw-accounts/');
export const saveWithdrawalAccount = (method: PayeeMethod, payload: {
  payee_account: string; payee_name: string; expected_id: number; expected_version: number;
}) => request<ApiResponse<SavedWithdrawalAccount>>(`/wallet/withdraw-accounts/${method}/`, 'PUT', payload);
export const deleteWithdrawalAccount = (account: SavedWithdrawalAccount) => request<ApiResponse<null>>(
  `/wallet/withdraw-accounts/${account.payee_method}/`, 'DELETE', { expected_id: account.id, expected_version: account.version },
);

export interface WithdrawPayload {
  amount: number;
  withdrawal_account_id: number;
  expected_payee_version: number;
  remark?: string;
  expected_tax_rate?: number;
  expected_tax_amount?: number;
  expected_actual_amount?: number;
}

/**
 * 拉取提现概览；传入 amount（内部账务单位整数）时，后端会额外回显
 * 该金额的试算税额与实际到账（T8-a 提现试算接口），用于前端预览。
 */
export const fetchWithdrawOverview = (amount?: number) => {
  const query = amount != null && Number.isFinite(amount)
    ? `?amount=${Math.round(amount)}`
    : '';
  return request<ApiResponse<WithdrawOverview>>(`/wallet/withdraw/${query}`, 'GET');
};

export const withdrawRequest = (payload: WithdrawPayload) => {
  return fundRequest<{ balance: number; frozen_amount: number; request: WithdrawRecord }>(
    '/wallet/withdraw/',
    payload,
  );
};

// ---------------- 押金 ----------------
export interface DepositOverview {
  deposit_mode?: 'CASH' | 'EARNINGS';
  deposit_percent?: number;
  hold_hours?: number;
  deposit_required: number;
  deposit_paid: number;
  deposit_remaining: number;
  balance: number;
}

export const fetchDepositInfo = () => {
  return request<ApiResponse<DepositOverview>>('/wallet/deposit/', 'GET');
};

export const payDeposit = (amount: number) => {
  return fundRequest<DepositOverview>('/wallet/deposit/', { amount });
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
  received_income: number;
  settlement_status: 'SETTLED' | 'PENDING' | 'CHECK_REQUIRED' | 'PARTIALLY_REFUNDED' | 'REFUNDED';
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
  pagination: {
    page_size: number;
    dispose: { page: number; total: number };
    gift: { page: number; total: number };
  };
}

export const fetchProviderBenefits = (params?: { dispose_page?: number; gift_page?: number; page_size?: number }) =>
  request<ApiResponse<ProviderBenefits>>('/wallet/provider-benefits/', 'GET', params);
