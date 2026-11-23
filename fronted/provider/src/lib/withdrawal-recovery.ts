import { fundRequest, listPendingFundRequests, retryPendingFundRequest, type PendingFundRequest } from "@xa/shared"

const applyPath = "/wallet/withdraw/"
const cancelPath = /^\/wallet\/withdraw\/([1-9]\d*)\/cancel\/$/

type WithdrawalResult = {
  id?: number
  status?: string
  request?: { id: number; amount: number; actual_amount: number }
}

export type PendingWithdrawal = { original: PendingFundRequest } & (
  { kind: "apply"; amount: number; actualAmount: number }
  | { kind: "cancel"; rowId: number }
)

const positiveInteger = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value > 0

export function submitWithdrawal(payload: Record<string, unknown>) {
  return fundRequest<WithdrawalResult>("provider", applyPath, payload, { keepOriginalOnFailure: true })
}

export function cancelWithdrawal(rowId: number) {
  return fundRequest<WithdrawalResult>("provider", `${applyPath}${rowId}/cancel/`, {}, { keepOriginalOnFailure: true })
}

export function readPendingWithdrawals(): PendingWithdrawal[] {
  return listPendingFundRequests("provider", path => path === applyPath || cancelPath.test(path))
    .flatMap((original): PendingWithdrawal[] => {
      const { path, payload } = original
      const fields = Object.keys(payload)
      if (path === applyPath) {
        const { amount, withdrawal_account_id, expected_payee_version, expected_tax_rate,
          expected_tax_amount, expected_actual_amount, expected_config_version } = payload
        if (fields.length !== 7 || !positiveInteger(amount) || amount > 2147483647
          || !positiveInteger(withdrawal_account_id) || !positiveInteger(expected_payee_version)
          || typeof expected_tax_rate !== "number" || !Number.isInteger(expected_tax_rate) || expected_tax_rate < 0 || expected_tax_rate > 100
          || typeof expected_tax_amount !== "number" || !Number.isInteger(expected_tax_amount) || expected_tax_amount < 0
          || !positiveInteger(expected_actual_amount) || expected_tax_amount + expected_actual_amount !== amount
          || typeof expected_config_version !== "string" || !/^[a-f0-9]{64}$/.test(expected_config_version)) return []
        return [{ kind: "apply", amount, actualAmount: expected_actual_amount, original }]
      }
      const rowId = Number(cancelPath.exec(path)?.[1])
      return fields.length === 0 && positiveInteger(rowId) ? [{ kind: "cancel", rowId, original }] : []
    })
}

export async function retryPendingWithdrawal(pending: PendingWithdrawal): Promise<number> {
  const result = await retryPendingFundRequest<WithdrawalResult>("provider", pending.original, data =>
    pending.kind === "apply"
      ? positiveInteger(data?.request?.id) && data.request.amount === pending.amount && data.request.actual_amount === pending.actualAmount
      : data?.id === pending.rowId && data.status === "CANCELLED")
  return pending.kind === "apply" ? result.request!.id : pending.rowId
}
