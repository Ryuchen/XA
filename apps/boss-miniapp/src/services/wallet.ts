import { ApiResponse, request } from '@/utils/request';

export interface WalletInfo {
  balance: number;
}

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

export const fetchWalletInfo = () => {
  return request<ApiResponse<WalletInfo>>('/wallet/info/', 'GET');
};

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
