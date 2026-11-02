import { ApiResponse, request, uploadFile } from '@/utils/request';
import type { OrderRefundSummary } from '@/types/order';

export interface ProviderReport {
  fulfillment?: string;
  id: number;
  order_id: number;
  order_no: string;
  game_name: string;
  service_name?: string;
  amount: number;
  commission_rate: number;
  commission_type?: 'PERCENT' | 'FIXED';
  commission_fixed?: number;
  payout_amount: number | null;
  expected_income: number | null;
  submitted_at: string | null;
  settled_at: string | null;
  settlement_status?: 'REVIEW' | 'WAITING' | 'SETTLED';
  order_status: string;
  payment_status?: string;
  aftersale_status?: 'OPEN' | 'RESOLVED' | 'REFUNDED' | 'PARTIALLY_REFUNDED' | '';
  refund_summary?: OrderRefundSummary | null;
  status: 'DRAFT' | 'PENDING' | 'APPROVED' | 'REJECTED';
  status_display: string;
  entry_image_url: string;
  completion_image_url: string;
  result_image_urls: string[];
  result_images: { id: number; url: string }[];
  audit_remark: string;
  can_edit: boolean;
  can_submit: boolean;
  edit_block_reason: string;
  evidence_upload_block_reasons?: Partial<Record<'ENTRY' | 'COMPLETION', string>>;
}

export const fetchReports = () => request<ApiResponse<ProviderReport[]>>('/wallet/reports/');
export const createReport = (orderId: number) => request<ApiResponse<ProviderReport>>('/wallet/reports/', 'POST', { order_id: orderId });
export const uploadReportImage = (id: number, path: string, kind: 'ENTRY' | 'COMPLETION' | 'RESULT') =>
  uploadFile<ApiResponse<ProviderReport>>(`/wallet/reports/${id}/images/`, path, { kind }, 'image');
export const submitReport = (id: number) => request<ApiResponse<ProviderReport>>(`/wallet/reports/${id}/submit/`, 'POST');
export const deleteReportImage = (id: number, imageId: number) => request<ApiResponse<ProviderReport>>(`/wallet/reports/${id}/images/${imageId}/`, 'DELETE');
