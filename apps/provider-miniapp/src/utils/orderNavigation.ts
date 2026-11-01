import { getSessionRevision } from './auth';

// Tab pages cannot receive query parameters through switchTab. Keep this
// one-shot intent in memory and bind it to the current login session.
let pending: { id: number; revision: number } | null = null;
export function queueOrderFocus(id: number) {
  pending = { id, revision: getSessionRevision() };
}
// Zero denotes the public pool, used for pending team applications which do
// not yet grant access to the individual order-detail endpoint.
export function queueOrderPool() { pending = { id: 0, revision: getSessionRevision() }; }
export function takeOrderFocus(): number | null {
  const value = pending;
  pending = null;
  return value && value.revision === getSessionRevision() ? value.id : null;
}
export function clearOrderFocus() { pending = null; }
