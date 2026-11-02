import { getSessionRevision } from './auth';

// switchTab cannot carry a query. Consume this intent only in the same login.
let pending: { orderId: number; revision: number } | null = null;
export function queueReportOrder(orderId: number) {
  pending = Number.isSafeInteger(orderId) && orderId > 0 ? { orderId, revision: getSessionRevision() } : null;
}
export function takeReportOrder(): number | null {
  const value = pending;
  pending = null;
  return value && value.revision === getSessionRevision() ? value.orderId : null;
}
export function clearReportOrder() { pending = null; }
