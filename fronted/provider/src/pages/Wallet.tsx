import { coinText, coinUnit } from "@xa/shared"
import {
  walletTransactionPresentation,
  giftBenefitPresentation,
  loadProviderBenefits,
  withdrawalPresentation,
  useCurrentStore,
  useSession,
} from "@xa/shared"
import { useEffect, useId, useRef, useState } from "react"
import { Link, useSearchParams } from "react-router"
import {
  ArrowDownLeft,
  ArrowUpRight,
  Banknote,
  CircleDollarSign,
  ChevronRight,
  Headphones,
  History,
  RefreshCw,
  TicketCheck,
  WalletCards,
} from "lucide-react"
import { formatRmb, toRawAmount, useRealtime } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
  Panel,
  PageHeader,
  Notice,
  LoadState,
  SnapshotGate,
  EmptyState,
  Status,
  DetailGrid,
  FilterBar,
  Pager,
  Confirm,
  Field,
  selectClass,
} from "@/components/provider-ui"
import {
  api,
  fund,
  useLoad,
  useRefresh,
  money,
  date,
  message,
  uncertain,
  type Failure,
  useViewGuard,
} from "@/lib/provider"
import {
  cancelWithdrawal as cancelOriginalWithdrawal, readPendingWithdrawals, retryPendingWithdrawal, submitWithdrawal,
  type PendingWithdrawal,
} from "@/lib/withdrawal-recovery"

type Account = {
  id: number
  version: number
  payee_method: string
  payee_name: string
  payee_account: string
}
type Withdrawal = {
  id: number
  amount: number
  actual_amount: number
  tax_amount: number
  tax_rate: number
  payee_method_display: string
  payee_name: string
  payee_account: string
  status: string
  status_display: string
  workflow_version?: number
  can_cancel?: boolean
  processing?: boolean
  audit_remark: string
  payout_reference: string
  created_at: string
  paid_at: string | null
}
type Overview = {
  balance: number
  frozen_amount: number
  min_amount: number
  balance_threshold?: number | null
  can_withdraw?: boolean
  tax_rate: number
  config_version?: string
  payout_eligible?: boolean
  requests: Withdrawal[]
  requested_withdrawal?: Withdrawal | null
  requests_pagination?: { page: number; page_size: number; total: number }
  amount?: number
  tax_amount?: number
  actual_amount?: number
}
type Transaction = {
  id: number
  tx_no: string
  tx_type: string
  amount: number
  status: string
  remark: string
  created_at: string
  is_referral_income?: boolean
  is_reversal?: boolean
  gift_source?: string
}
type Benefits = {
  summary: {
    total_reward: number
    total_penalty: number
    total_gift_income: number
  }
  dispose_records: {
    id: number
    type_display: string
    signed_amount: number
    reason: string
    created_at: string
  }[]
  gift_records: {
    id: number
    order_no: string
    source_order_no?: string
    service_name: string
    customer_name: string
    provider_income: number
    received_income: number
    settlement_status: string
    frozen_income: number
    completed_at: string
  }[]
  pagination: {
    page_size: number
    dispose: { page: number; total: number }
    gift: { page: number; total: number }
  }
}
const methodLabel = (method: string) =>
  ({ WECHAT: "微信", ALIPAY: "支付宝", BANK: "银行卡" })[method] || method
const transactionLabel = (row: Transaction) =>
  walletTransactionPresentation(row).label
export function WalletPage() {
  const store = useCurrentStore()
  const session = useSession("provider")
  const guardView = useViewGuard()
  const recoveryTitle = useId()
  const chosenVersion = useRef<{ id: number; version: number } | null>(null)
  const [params, setParams] = useSearchParams(),
    tab = params.get("tab") || "transactions",
    focus = params.get("withdraw_id"),
    [page, setPage] = useState(1),
    [open, setOpen] = useState(false),
    [cancelWithdrawal, setCancelWithdrawal] = useState<number | null>(null),
    [amount, setAmount] = useState(""),
    [accountId, setAccountId] = useState(""),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [issue, setIssue] = useState("")
  const [pending, setPending] = useState<PendingWithdrawal[]>([])
  const [recovery, setRecovery] = useState<PendingWithdrawal | null>(null)
  const [recoveryIssue, setRecoveryIssue] = useState("")

  function reloadPending() {
    try { setPending(readPendingWithdrawals()) }
    catch { setPending([]) }
  }
  useEffect(() => {
    reloadPending()
    window.addEventListener("storage", reloadPending)
    return () => window.removeEventListener("storage", reloadPending)
  }, [store.slug, session.user?.id])

  const data = useLoad(async () => {
    const [overview, transactions, benefits, accounts] = await Promise.all([
      api<Overview>(
        `/wallet/withdraw/?requests_page=${tab === "withdraw" ? page : 1}${focus ? `&request_id=${focus}` : ""}`
      ),
      api<{
        transactions: Transaction[]
        total: number
        total_income: number
        page: number
      }>(
        `/wallet/transactions/?page=${tab === "transactions" ? page : 1}&page_size=20`
      ),
      loadProviderBenefits<Benefits>(tab, page),
      api<Account[]>("/wallet/withdraw-accounts/"),
    ])
    return { overview, transactions, benefits, accounts }
  }, [page, tab, focus])
  const parsedAmount = /^(?:0|[1-9]\d*)(?:\.\d)?$/.test(amount.trim())
    ? toRawAmount(amount)
    : 0
  const rawAmount =
    Number.isSafeInteger(parsedAmount) &&
    parsedAmount > 0 &&
    parsedAmount <= 2147483647
      ? parsedAmount
      : 0
  const quote = useLoad(
    () =>
      open && rawAmount > 0
        ? api<Overview>(`/wallet/withdraw/?requests_page=1&amount=${rawAmount}`)
        : Promise.resolve(null),
    [open, rawAmount]
  )
  useRefresh(async () => {
    if (!busy) {
      await data.reload()
      if (open) await quote.reload()
    }
  }, ["wallet_update", "withdraw_account_update", "withdraw_status_update"])
  useRealtime("provider", (event) => {
    if (document.hidden || busy || !open) return
    if (event.type === "withdraw_config_update") {
      setIssue("提现规则已更新，请重新试算并核对。")
      void quote.reload().catch(() => {})
    }
    if (event.type === "withdraw_account_update") {
      chosenVersion.current = null
      setAccountId("")
      setIssue("收款账户已变化，请重新选择并核对。")
      void data.reload().catch(() => {})
    }
  })
  useEffect(() => {
    setPage(1)
  }, [tab])
  const overview = data.data?.overview,
    accounts = data.data?.accounts || [],
    account = accounts.find((row) => String(row.id) === accountId),
    q = quote.data
  useEffect(() => {
    const chosen = chosenVersion.current
    if (
      open &&
      chosen &&
      (!account ||
        account.id !== chosen.id ||
        account.version !== chosen.version)
    ) {
      chosenVersion.current = null
      setAccountId("")
      setIssue("收款账户已变化，请重新选择并核对。")
    }
  }, [data.data, open, accountId])
  const validQuote =
    !!q &&
    q.amount === rawAmount &&
    [
      q.amount,
      q.tax_amount,
      q.actual_amount,
      q.tax_rate,
      q.min_amount,
      q.balance,
    ].every(
      (value) =>
        typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ) &&
    q.tax_rate <= 100 &&
    typeof q.config_version === "string" && q.config_version.length === 64 &&
    q.tax_amount! + q.actual_amount! === rawAmount
  const validation =
    data.loading || !!data.error
      ? "请先重新读取钱包信息"
      : !rawAmount
        ? "请输入有效金额，最多一位小数"
        : quote.loading || !validQuote
          ? "请等待完整试算后再提交"
          : q?.can_withdraw === false
            ? "可用余额未达到提现条件"
            : rawAmount < q!.min_amount
              ? `最低提现 ${money(q!.min_amount)} ${coinUnit()}`
              : q!.payout_eligible === false
                ? "预计实际打款为零，请调整金额或核对费率"
              : rawAmount > q!.balance
                ? "提现金额超出可用余额"
                : !account
                  ? "请选择并核对收款账户"
                  : ""
  async function submit() {
    if (busy || validation || !q || !account) return
    const currentView = guardView()
    setBusy(true)
    setIssue("")
    try {
      await submitWithdrawal({
        amount: rawAmount,
        withdrawal_account_id: account.id,
        expected_payee_version: account.version,
        expected_tax_rate: q.tax_rate,
        expected_tax_amount: q.tax_amount,
        expected_actual_amount: q.actual_amount,
        expected_config_version: q.config_version,
      })
      if (!currentView()) return
      reloadPending()
      setOpen(false)
      setAmount("")
      setParams({ tab: "withdraw" })
      setNotice("提现申请已提交")
      void data.reload().catch(() => {})
    } catch (cause) {
      if (!currentView()) return
      const failure = cause as Failure
      if (failure.errorCode === "WITHDRAW_QUOTE_CHANGED") {
        setIssue("提现规则已更新，请重新试算并核对。")
        await quote.reload().catch(() => {})
      } else if (failure.errorCode === "PAYEE_CHANGED") {
        setAccountId("")
        setIssue("收款账户已变化，请重新选择并核对。")
        await data.reload().catch(() => {})
      } else
        setIssue(
          uncertain(cause)
            ? "提现结果未确认，请在待核对提现操作中按原编号重试。"
            : message(cause)
        )
    } finally {
      if (currentView()) reloadPending()
      setBusy(false)
    }
  }

  async function cancel() {
    if (cancelWithdrawal == null || busy) return
    const currentView = guardView()
    setBusy(true)
    try {
      await cancelOriginalWithdrawal(cancelWithdrawal)
      if (!currentView()) return
      setCancelWithdrawal(null)
      setNotice("提现已取消")
    } catch (cause) {
      if (currentView()) setIssue(uncertain(cause) ? "取消结果未确认，请在待核对提现操作中按原编号重试。" : message(cause))
    } finally {
      if (currentView()) {
        reloadPending()
        void data.reload().catch(() => {})
      }
      setBusy(false)
    }
  }

  async function retryOriginal() {
    if (!recovery || busy) return
    const currentView = guardView()
    setRecoveryIssue("")
    setBusy(true)
    try {
      const id = await retryPendingWithdrawal(recovery)
      if (!currentView()) return
      reloadPending()
      setRecovery(null)
      setNotice(recovery.kind === "apply" ? "已核对原提现申请" : "已核对原取消操作")
      if (tab === "withdraw" && focus === String(id)) void data.reload().catch(() => {})
      else setParams({ tab: "withdraw", withdraw_id: String(id) })
    } catch (cause) {
      if (currentView()) setRecoveryIssue(message(cause))
    } finally {
      if (currentView()) reloadPending()
      setBusy(false)
    }
  }
  const withdrawals = overview?.requested_withdrawal
    ? [
        overview.requested_withdrawal,
        ...overview.requests.filter(
          (row) => row.id !== overview.requested_withdrawal!.id
        ),
      ]
    : overview?.requests || []
  const total =
    tab === "transactions"
      ? data.data?.transactions.total
      : tab === "dispose"
        ? data.data?.benefits.pagination.dispose.total
        : tab === "gift"
          ? data.data?.benefits.pagination.gift.total
          : overview?.requests_pagination?.total
  return (
    <div className="space-y-6">
      <PageHeader title="我的钱包">
        <Button
          disabled={
            data.loading || !!data.error || !overview || overview.balance <= 0
          }
          onClick={() => {
            setIssue("")
            chosenVersion.current = null
            setAccountId("")
            setOpen(true)
          }}
        >
          <Banknote className="size-4" />
          提现
        </Button>
      </PageHeader>
      <Notice tone={["提现申请已提交", "收款账户已保存", "收款账户已保存，已重新核对。", "已解绑", "采购成功"].includes(notice) ? "success" : "info"}>{notice}</Notice>
      {pending.length > 0 && (
        <section aria-labelledby={recoveryTitle} className="space-y-3 rounded-[12px] border bg-card p-4">
          <h2 id={recoveryTitle} className="text-sm font-semibold">待核对提现操作</h2>
          <p className="text-sm text-muted-foreground">请按原编号核对结果，避免重复发起同一笔提现。</p>
          <ul className="divide-y">
            {pending.map(item => (
              <li key={item.original.key} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <div className="min-w-0">
                  <p className="text-sm">{item.kind === "apply" ? `提现申请 ${money(item.amount)} ${coinUnit()}` : `取消申请 #${item.rowId}`}</p>
                  <p className="break-all text-xs text-muted-foreground">原编号 {item.original.requestId}</p>
                </div>
                <Button type="button" variant="outline" disabled={busy} onClick={() => {
                  setRecoveryIssue("")
                  setRecovery(item)
                }}>{item.kind === "apply" ? "重试原申请" : "重试原取消"}</Button>
              </li>
            ))}
          </ul>
        </section>
      )}
      <SnapshotGate
        loading={data.loading}
        error={data.error}
        hasData={data.data != null}
        reload={() => data.reload().catch(() => {})}
      >
        <section className="provider-wallet-balance overflow-hidden rounded-[12px] border border-slate-700 text-white shadow-sm">
          <div className="flex items-start justify-between gap-3 p-5">
            <div className="min-w-0">
            <p className="flex items-center gap-2 text-sm text-slate-300">
              <WalletCards className="size-4 text-sky-300" />
              {coinText("可提现浪花")}</p>
            <p className="mt-4 text-[32px] leading-none font-semibold tabular-nums">
              {money(overview?.balance)}
              <span className="ml-2 text-sm font-normal text-slate-300">
                {coinUnit()}</span>
            </p>
            </div>
            <Badge className="max-w-[45%] border-white/15 bg-white/10 text-white">
              <span className="truncate" title={store.name}>{store.name}</span>
            </Badge>
          </div>
          <div className="grid grid-cols-2 border-t border-white/10">
            <div className="min-w-0 border-r border-white/10 p-4">
              <p className="text-xs text-slate-400">冻结金额</p>
              <p className="mt-1 text-lg font-semibold tabular-nums">
                {money(overview?.frozen_amount)}
                <span className="ml-1 text-xs font-normal text-slate-300">
                  {coinUnit()}</span>
              </p>
            </div>
            <div className="min-w-0 p-4">
              <p className="text-xs text-slate-400">服务及推荐净收益</p>
              <p className="mt-1 text-lg font-semibold tabular-nums">
                {money(data.data?.transactions.total_income)}
                <span className="ml-1 text-xs font-normal text-slate-300">
                  {coinUnit()}</span>
              </p>
            </div>
          </div>
        </section>
        <section className="space-y-4" aria-label="钱包记录">
          <FilterBar
            ariaLabel="钱包明细分类"
            columns={4}
            items={[
              { value: "transactions", label: "资金流水" },
              { value: "dispose", label: "奖惩记录" },
              { value: "gift", label: "礼物收益" },
              { value: "withdraw", label: "提现记录" },
            ]}
            value={tab}
            onChange={(key) => {
              setPage(1)
              setParams(key === "transactions" ? {} : { tab: key })
            }}
          />
          <LoadState
            hasData={data.data != null}
            loading={data.loading}
            error={data.error}
            reload={() => data.reload().catch(() => {})}
            empty={total === 0}
            emptyTitle="暂无钱包记录"
            emptyDescription="完成服务并结算后，资金流水会在这里显示。"
            emptyContent={tab === "withdraw" ? <section className="rounded-[12px] border bg-card p-8 text-center"><History className="mx-auto size-7 text-muted-foreground" /><h2 className="mt-3 text-sm font-medium">暂无提现记录</h2><p className="mt-2 text-xs text-muted-foreground">{overview ? `最低提现 ${money(overview.min_amount)} ${coinUnit()}，提交前会核对税费与到账金额。` : "提交前会核对提现规则与到账金额。"}</p><Button variant="outline" className="mt-5 h-11" disabled={data.loading || !!data.error || !overview || overview.balance <= 0} onClick={() => { setIssue(""); chosenVersion.current = null; setAccountId(""); setOpen(true) }}>申请提现</Button></section> : undefined}
          >
            {data.data && (
              <section className="overflow-hidden rounded-[12px] border bg-card">
                {tab === "transactions" &&
                  data.data.transactions.transactions.map((row) => (
                    <div
                      key={row.id}
                      className="provider-ledger-row flex items-center gap-3 border-b p-4 last:border-0"
                    >
                      <span
                        className={`grid size-10 shrink-0 place-items-center rounded-lg ${row.amount > 0 ? "bg-success/10 text-success" : "bg-muted text-muted-foreground"}`}
                      >
                        {row.amount > 0 ? (
                          <ArrowDownLeft className="size-4" />
                        ) : (
                          <ArrowUpRight className="size-4" />
                        )}
                      </span>
                      <div className="min-w-0 flex-1">
                        <h2 className="text-sm font-medium">
                          {transactionLabel(row)}
                        </h2>
                        <p className="text-xs text-muted-foreground">
                          {row.remark || row.tx_no}
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {date(row.created_at)}
                          {row.status !== "SUCCESS" &&
                            ` · ${row.status === "PENDING" ? "处理中" : row.tx_type === "WITHDRAW" ? "已退回余额 · 原申请金额" : "未成功 · 原流水金额"}`}
                        </p>
                      </div>
                      <strong
                        className={`shrink-0 text-right text-base font-semibold whitespace-nowrap tabular-nums ${row.amount > 0 ? "text-success" : ""}`}
                      >
                        {row.amount > 0 ? "+" : ""}
                        {money(row.amount)} {coinText(" 浪花")}</strong>
                    </div>
                  ))}
                {tab === "dispose" &&
                  data.data.benefits.dispose_records.map((row) => (
                    <div
                      key={row.id}
                      className="flex items-center gap-3 border-b p-4 last:border-0"
                    >
                      <CircleDollarSign className="size-5 shrink-0 text-primary" />
                      <div className="min-w-0 flex-1">
                        <h2 className="text-sm font-medium">
                          {row.type_display}
                        </h2>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {row.reason} · {date(row.created_at)}
                        </p>
                      </div>
                      <strong className={`shrink-0 text-right text-sm font-semibold whitespace-nowrap tabular-nums ${row.signed_amount > 0 ? "text-success" : ""}`}>
                        {row.signed_amount > 0 ? "+" : ""}
                        {money(row.signed_amount)} {coinText(" 浪花")}</strong>
                    </div>
                  ))}
                {tab === "gift" &&
                  data.data.benefits.gift_records.map((row) => (
                    <div
                      key={row.id}
                      className="flex items-start gap-3 border-b p-4 last:border-0"
                    >
                      <CircleDollarSign className="mt-0.5 size-5 shrink-0 text-primary" />
                      <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex justify-between gap-3">
                        <h2 className="text-sm font-medium">
                          {row.service_name}
                        </h2>
                        <strong className="shrink-0 text-right text-sm font-semibold whitespace-nowrap text-success tabular-nums">
                          {money(giftBenefitPresentation(row).receivedIncome)}{" "}
                          {coinUnit()}</strong>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        {row.customer_name} · {row.order_no} ·{" "}
                        {date(row.completed_at)}
                      </p>
                      {typeof row.source_order_no === "string" && (
                        <p className="text-xs text-muted-foreground">
                          {row.source_order_no
                            ? `服务订单 ${row.source_order_no}`
                            : "直接赠送"}
                        </p>
                      )}
                      <p className="text-xs text-muted-foreground">
                        {giftBenefitPresentation(row).settlementLabel ||
                          `应得 ${money(row.provider_income)} ${coinUnit()}，请联系客服核对`}
                      </p>
      {row.frozen_income > 0 && <p className="text-xs text-muted-foreground">冻结 {money(row.frozen_income)} {coinUnit()}，到期后仍须等待未结售后处理。</p>}
                      </div>
                    </div>
                  ))}
                {tab === "withdraw" && (
                  <>
                    {focus && overview?.requested_withdrawal === null && (
                      <Notice>未找到该提现申请，请核对消息或联系客服。</Notice>
                    )}
                    {withdrawals.map((row) => (
                      <div
                        key={row.id}
                        className={`space-y-1 border-b p-4 last:border-0 ${String(row.id) === focus ? "bg-primary/5" : ""}`}
                      >
                        <div className="flex justify-between gap-3">
                          <h2 className="text-sm font-semibold">
                            {withdrawalPresentation(row).title}
                          </h2>
                          <Status
                            value={row.status}
                            label={row.workflow_version === 1 && row.status === "PENDING" ? "历史申请待核实" : withdrawalPresentation(row).statusLabel || row.status_display}
                          />
                        </div>
                        <p className="text-xs text-muted-foreground">
                          申请 #{row.id} · {row.payee_account}
                        </p>
                        <p className="text-sm">
                          原申请 {money(row.amount)} {coinText(" 浪花 ·")}{" "}
                          {withdrawalPresentation(row).payoutText}
                        </p>
                        {row.tax_amount > 0 && row.status !== "REJECTED" && (
                          <p className="text-xs text-muted-foreground">
                            扣除 {money(row.tax_amount)} {coinText(" 浪花 · 费率")}{" "}
                            {row.tax_rate}%
                          </p>
                        )}
                        {withdrawalPresentation(row).auditRemark && (
                          <p className="text-xs">
                            审核备注：{withdrawalPresentation(row).auditRemark}
                          </p>
                        )}
                        {withdrawalPresentation(row).payoutReference && (
                          <p className="text-xs">
                            打款凭证：
                            {withdrawalPresentation(row).payoutReference}
                          </p>
                        )}
                        <p className="text-xs text-muted-foreground">
                          {date(row.paid_at || row.created_at)}
                        </p>
                        {row.can_cancel && !pending.some(item => item.kind === "cancel" && item.rowId === row.id) && <Button type="button" variant="outline" disabled={busy || data.loading}
                          onClick={() => { setIssue(""); setCancelWithdrawal(row.id) }}>取消申请</Button>}
                      </div>
                    ))}
                  </>
                )}
              </section>
            )}
          </LoadState>
          <Pager
            page={page}
            total={total || 0}
            change={setPage}
            disabled={data.loading}
          />
        </section>
        <section
          className="overflow-hidden rounded-[12px] border bg-card"
          aria-label="钱包账户服务"
        >
          <h2 className="border-b px-4 py-3 text-sm font-semibold">账户服务</h2>
          <Link
            to="/withdraw-accounts"
            className="group flex min-h-14 w-full items-center gap-3 border-b px-4 text-left hover:bg-accent/30"
          >
            <WalletCards className="size-4 shrink-0 text-muted-foreground group-hover:text-primary" />
            <span className="flex-1 text-sm font-medium">管理收款账户</span>
            <ChevronRight className="size-4 text-muted-foreground" />
          </Link>
          <Link
            to="/support"
            className="group flex min-h-14 w-full items-center gap-3 px-4 text-left hover:bg-accent/30"
          >
            <Headphones className="size-4 shrink-0 text-muted-foreground group-hover:text-primary" />
            <span className="flex-1 text-sm font-medium">充值与钱包咨询</span>
            <ChevronRight className="size-4 text-muted-foreground" />
          </Link>
        </section>
      </SnapshotGate>
      <Confirm
        open={open}
        title="申请提现"
        description="请核对金额、扣除费用和收款资料。"
        onClose={() => setOpen(false)}
        onConfirm={() => void submit()}
        busy={busy}
        disabled={!!validation || !!issue}
        label="确认提现"
        contentClassName="sm:max-w-lg"
      >
        <Notice error>
          {issue}
          {issue.includes("规则已更新") && (
            <Button
              variant="link"
              onClick={() => {
                setIssue("")
                void quote.reload().catch(() => {})
              }}
            >
              重新核对试算
            </Button>
          )}
          {issue.includes("账户已变化") && (
            <Button variant="link" onClick={() => setIssue("")}>
              已读取，重新选择账户
            </Button>
          )}
          {issue.includes("未确认") && (
            <Button
              variant="link"
              onClick={() => {
                setOpen(false)
                setParams({ tab: "withdraw" })
                void data.reload().catch(() => {})
              }}
            >
              查看提现记录
            </Button>
          )}
        </Notice>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="min-w-0 space-y-2">
            <Field label={coinText("提现浪花")}>
              <Input
                inputMode="decimal"
                value={amount}
                maxLength={12}
                onChange={(e) => setAmount(e.target.value)}
                disabled={busy}
              />
            </Field>
            <p className="text-xs leading-5 text-muted-foreground">
              可用 {money(overview?.balance)} {coinUnit()} · 单笔最低 {money(overview?.min_amount)} {coinUnit()}
              {overview?.balance_threshold != null && ` · 余额须大于 ¥${formatRmb(overview.balance_threshold)}`}
            </p>
          </div>
          <div className="min-w-0 space-y-2">
            <Field label="收款账户">
              <select
                className={`${selectClass} min-h-11`}
                value={accountId}
                onChange={(e) => {
                  const value = e.target.value
                  const row = accounts.find((item) => String(item.id) === value)
                  chosenVersion.current = row
                    ? { id: row.id, version: row.version }
                    : null
                  setAccountId(value)
                  if (row && issue.includes("账户已变化")) setIssue("")
                }}
                disabled={busy}
              >
                <option value="">请选择收款账户</option>
                {accounts.map((row) => (
                  <option key={row.id} value={row.id}>
                    {methodLabel(row.payee_method)} · {row.payee_name} ·{" "}
                    {row.payee_account}
                  </option>
                ))}
              </select>
            </Field>
            <Button asChild variant="outline" className="w-full rounded-[6px]">
              <Link to="/withdraw-accounts">管理收款账户</Link>
            </Button>
          </div>
        </div>
        <Notice error>
          {quote.error}
          <Button
            variant="link"
            onClick={() => void quote.reload().catch(() => {})}
          >
            重新试算
          </Button>
        </Notice>
        {validQuote ? (
          <DetailGrid
            items={[
              { label: "申请金额", value: `${amount} ${coinUnit()}` },
              { label: "扣除费率", value: `${q!.tax_rate}%` },
              { label: "扣除费用", value: `${money(q!.tax_amount)} ${coinUnit()}` },
              { label: "预计打款", value: `¥${formatRmb(q!.actual_amount!)}` },
            ]}
          />
        ) : (
          <p className="text-sm text-muted-foreground">
            {quote.loading && rawAmount ? "正在试算…" : "填写金额后核对试算"}
          </p>
        )}
        {amount && (
          <p className="text-xs text-muted-foreground">{validation}</p>
        )}
      </Confirm>
      <Confirm open={cancelWithdrawal != null} title="取消提现申请"
        description="取消后申请金额将解冻并退回可用余额。"
        onClose={() => setCancelWithdrawal(null)} busy={busy} disabled={!!issue}
        label="确认取消" onConfirm={() => void cancel()}>
        <Notice error>{issue}</Notice>
      </Confirm>
      <Confirm open={recovery != null} title="核对原提现操作"
        description={recovery?.kind === "cancel" ? "系统会核对或重试原取消操作。取消成功后，原申请金额退回可用余额。"
          : "系统会按原金额和收款资料核对或重试。申请成功后，资金进入冻结。"}
        onClose={() => setRecovery(null)} busy={busy} label="按原编号重试" onConfirm={() => void retryOriginal()}>
        {recovery && <DetailGrid items={[
          { label: "原操作编号", value: <span className="break-all">{recovery.original.requestId}</span> },
          ...(recovery.kind === "apply" ? [
            { label: "原申请金额", value: `${money(recovery.amount)} ${coinUnit()}` },
            { label: "原预计打款", value: `¥${formatRmb(recovery.actualAmount)}` },
          ] : [{ label: "原提现申请", value: `#${recovery.rowId}` }]),
        ]} />}
        <Notice error>{recoveryIssue}</Notice>
      </Confirm>
    </div>
  )
}

export function WithdrawalAccountsPage() {
  const [params] = useSearchParams(),
    data = useLoad(() => api<Account[]>("/wallet/withdraw-accounts/")),
    [editor, setEditor] = useState<{
      method: string
      id: number
      version: number
    } | null>(null),
    [removing, setRemoving] = useState<Account | null>(null),
    [name, setName] = useState(""),
    [number, setNumber] = useState(""),
    [busy, setBusy] = useState(false),
    [conflict, setConflict] = useState(false),
    [issue, setIssue] = useState(""),
    [notice, setNotice] = useState("")
  function edit(method: string, accounts = data.data || []) {
    const row = accounts.find((item) => item.payee_method === method)
    setEditor({ method, id: row?.id || 0, version: row?.version || 0 })
    setName(row?.payee_name || "")
    setNumber(row?.payee_account || "")
    setIssue("")
    setConflict(false)
  }
  useEffect(() => {
    const method = params.get("method")
    if (data.data && ["WECHAT", "ALIPAY", "BANK"].includes(method || ""))
      edit(method!)
  }, [!!data.data])
  useEffect(() => {
    if (!editor || !data.data) return
    const latest = data.data.find((row) => row.payee_method === editor.method)
    if (
      (latest?.id || 0) !== editor.id ||
      (latest?.version || 0) !== editor.version
    ) {
      setConflict(true)
      setIssue("账户已在其他位置更新，草稿已保留，请核对后重新载入。")
    }
  }, [data.data])
  useRefresh(async () => {
    if (!busy) await data.reload()
  }, ["withdraw_account_update"])
  async function save() {
    if (!editor || conflict || busy) return
    setBusy(true)
    try {
      await api(`/wallet/withdraw-accounts/${editor.method}/`, {
        method: "PUT",
        data: {
          payee_name: name.trim(),
          payee_account: number.trim(),
          expected_id: editor.id,
          expected_version: editor.version,
        },
      })
      setEditor(null)
      setNotice("收款账户已保存")
      await data.reload()
    } catch (cause) {
      const latest = await data.reload().catch(() => null),
        saved = latest?.find((row) => row.payee_method === editor.method)
      if (
        uncertain(cause) &&
        saved?.payee_name === name.trim() &&
        saved.payee_account === number.trim() &&
        (!editor.id ||
          (saved.id === editor.id && saved.version > editor.version))
      ) {
        setEditor(null)
        setNotice("收款账户已保存，已重新核对。")
      } else {
        setConflict(
          uncertain(cause) || (cause as Failure).errorCode === "PAYEE_CHANGED"
        )
        setIssue(
          uncertain(cause)
            ? "保存结果未确认，请重新载入最新账户核对。"
            : message(cause)
        )
      }
    } finally {
      setBusy(false)
    }
  }
  async function unbind() {
    if (!removing) return
    setBusy(true)
    try {
      await api(`/wallet/withdraw-accounts/${removing.payee_method}/`, {
        method: "DELETE",
        data: { expected_id: removing.id, expected_version: removing.version },
      })
      setNotice("已解绑")
    } catch (cause) {
      const latest = await data.reload().catch(() => null)
      setNotice(
        uncertain(cause) &&
          latest &&
          !latest.some((row) => row.id === removing.id)
          ? "原账户已解绑，已重新核对。"
          : uncertain(cause)
            ? "解绑结果未确认，请刷新核对。"
            : message(cause)
      )
    } finally {
      setRemoving(null)
      await data.reload().catch(() => {})
      setBusy(false)
    }
  }
  return (
    <div className="space-y-6">
      <PageHeader
        title="收款账户"
        description="微信、支付宝、银行卡各可绑定一个账户。"
        back="/wallet"
      />
      <Notice tone={["提现申请已提交", "收款账户已保存", "收款账户已保存，已重新核对。", "已解绑", "采购成功"].includes(notice) ? "success" : "info"}>{notice}</Notice>
      <LoadState
        hasData={data.data != null}
        loading={data.loading}
        error={data.error}
        reload={() => data.reload().catch(() => {})}
      >
        <div className="grid items-start gap-4 md:grid-cols-2 lg:grid-cols-3">
          {["WECHAT", "ALIPAY", "BANK"].map((method) => {
            const row = data.data?.find((item) => item.payee_method === method)
            return (
              <Panel key={method}>
                <div className="flex justify-between gap-3">
                  <h2 className="flex items-center gap-2 font-semibold">
                    <WalletCards className="size-5 text-primary" />
                    {methodLabel(method)}
                  </h2>
                  <Status
                    value={row ? "ACTIVE" : "UNBOUND"}
                    label={row ? "已绑定" : "未绑定"}
                  />
                </div>
                {row ? (
                  <DetailGrid
                    items={[
                      { label: "收款人", value: row.payee_name },
                      { label: "收款账号", value: row.payee_account },
                    ]}
                  />
                ) : (
                  <p className="text-sm text-muted-foreground">
                    绑定后可在提现时选择。
                  </p>
                )}
                <div className="mt-auto flex flex-wrap justify-end gap-2 border-t pt-3">
                  <Button
                    variant={row ? "outline" : "default"}
                    disabled={busy || data.loading || !!data.error}
                    onClick={() => edit(method)}
                  >
                    {row ? "修改" : "绑定"}
                  </Button>
                  {row && (
                    <Button
                      variant="destructive"
                      disabled={busy}
                      onClick={() => setRemoving(row)}
                    >
                      解绑
                    </Button>
                  )}
                </div>
              </Panel>
            )
          })}
        </div>
      </LoadState>
      <Confirm
        open={!!editor}
        title={`${editor ? methodLabel(editor.method) : ""}收款账户`}
        description="收款资料变更不影响已提交提现的原账户快照。"
        busy={busy}
        disabled={conflict || !name.trim() || !number.trim() || data.loading}
        onClose={() => setEditor(null)}
        onConfirm={() => void save()}
        label="保存账户"
      >
        <Notice error>
          {issue}
          {conflict && (
            <Button
              variant="link"
              onClick={async () => {
                const rows = await data.reload().catch(() => null)
                if (rows && editor) edit(editor.method, rows)
              }}
            >
              重新载入最新资料（替换草稿）
            </Button>
          )}
        </Notice>
        <Field label="收款人姓名">
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="收款账号">
          <Input value={number} onChange={(e) => setNumber(e.target.value)} />
        </Field>
        {conflict && (
          <Button
            variant="outline"
            onClick={() =>
              void navigator.clipboard
                .writeText(`${name}\n${number}`)
                .catch(() => setIssue("复制失败，请手动复制草稿。"))
            }
          >
            复制草稿
          </Button>
        )}
      </Confirm>
      <Confirm
        open={!!removing}
        title="解绑收款账户"
        confirmVariant="destructive"
        label="确认解绑"
        description="解绑后不可再用于新提现，已提交提现的账户快照保留。"
        onClose={() => setRemoving(null)}
        onConfirm={() => void unbind()}
        busy={busy}
      >
        {removing && (
          <DetailGrid
            items={[
              { label: "收款方式", value: methodLabel(removing.payee_method) },
              { label: "收款人", value: removing.payee_name },
              { label: "收款账号", value: removing.payee_account },
            ]}
          />
        )}
      </Confirm>
    </div>
  )
}

type PassProduct = {
  tier: string
  name: string
  daily_price: number
  delay_seconds: number
}
type PassData = {
  balance: number
  points_payment_available?: boolean
  purchase_block_reason?: string
  active_tier: string
  active_tier_display: string
  expires_at: string | null
  current_delay_seconds: number
  no_pass_delay_seconds: number
  products: PassProduct[]
  history: {
    id: number
    tier_display: string
    days: number
    paid_amount: number
    point_discount_amount?: number
    points_hundredths?: number
    points_returned?: number
    starts_at: string
    expires_at: string
    refundable: boolean
    refunded: boolean
  }[]
}
type PassPointsQuote = { points_hundredths: number; discount_units: number; payable_units: number; balance_sufficient: boolean; quote_token: string }
function pointHundredths(value: string): number | null {
  if (!/^(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(value)) return null
  const [whole, fraction = ""] = value.split(".")
  const amount = Number(whole) * 100 + Number((fraction + "00").slice(0, 2))
  return Number.isSafeInteger(amount) && amount <= 2147483647 ? amount : null
}
export function PassPage() {
  const data = useLoad(() => api<PassData>("/users/provider-pass/")),
    [product, setProduct] = useState<PassProduct | null>(null),
    [refundId, setRefundId] = useState<number | null>(null),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [issue, setIssue] = useState(""),
    [pointInput, setPointInput] = useState(""),
    [pointQuote, setPointQuote] = useState<PassPointsQuote | null>(null),
    [pointError, setPointError] = useState(""),
    [pointQuoting, setPointQuoting] = useState(false),
    pointQuoteSeq = useRef(0)
  const selectedPoints = pointInput === "" ? 0 : pointHundredths(pointInput)
  const wantsPoints = selectedPoints !== null && selectedPoints > 0
  useEffect(() => {
    pointQuoteSeq.current++
    setPointInput(""); setPointQuote(null); setPointError(""); setPointQuoting(false)
  }, [product?.tier])
  useRefresh(async () => {
    if (!busy) await data.reload()
  }, ["wallet_update"])
  async function buy() {
    if (!product || busy || issue || data.loading || data.error || data.data?.purchase_block_reason ||
        selectedPoints === null || pointQuoting || (wantsPoints && (!pointQuote || !pointQuote.balance_sufficient)) ||
        (!wantsPoints && (data.data?.balance ?? 0) < product.daily_price)) return
    setBusy(true)
    try {
      const value = await fund<PassData>("/users/provider-pass/", {
        tier: product.tier,
        days: 1,
        expected_daily_price: product.daily_price,
        ...(wantsPoints && pointQuote ? { points_quote_token: pointQuote.quote_token } : {}),
      })
      data.setData(value)
      setProduct(null)
      setNotice("采购成功")
    } catch (cause) {
      if (uncertain(cause))
        setIssue("采购结果未确认，请刷新通行证及钱包流水核对，暂勿重复提交。")
      else {
        setProduct(null)
        setNotice(message(cause))
      }
    } finally {
      await data.reload().catch(() => {})
      setBusy(false)
    }
  }
  async function previewPoints() {
    if (!product || selectedPoints === null || selectedPoints <= 0 || pointQuoting) return
    const seq = ++pointQuoteSeq.current
    setPointQuoting(true); setPointError(""); setPointQuote(null)
    try {
      const quote = await api<PassPointsQuote>("/users/provider-pass/points-quote/", { method: "POST", data: {
        tier: product.tier, selected_point_hundredths: selectedPoints,
      } })
      if (pointQuoteSeq.current === seq) setPointQuote(quote)
    } catch (cause) {
      if (pointQuoteSeq.current === seq) setPointError(message(cause))
    } finally {
      if (pointQuoteSeq.current === seq) setPointQuoting(false)
    }
  }
  async function refundPass() {
    if (refundId == null || busy || issue || data.loading || data.error) return
    setBusy(true)
    try {
      await fund(`/users/provider-pass/${refundId}/refund/`, {})
      setRefundId(null)
      setNotice("通行证退款成功")
    } catch (cause) {
      if (uncertain(cause)) setIssue("退款结果未确认，请先刷新采购记录和钱包流水核对。")
      else {
        setRefundId(null)
        setNotice(message(cause))
      }
    } finally {
      await data.reload().catch(() => {})
      setBusy(false)
    }
  }
  return (
    <div className="space-y-6">
      <PageHeader title="接单通行证" back="/mine" />
      <Notice tone={["提现申请已提交", "收款账户已保存", "收款账户已保存，已重新核对。", "已解绑", "采购成功", "通行证退款成功"].includes(notice) ? "success" : "info"}>{notice}</Notice>
      <LoadState
        hasData={data.data != null}
        loading={data.loading}
        error={data.error}
        reload={() => data.reload().catch(() => {})}
      >
        {data.data && (
          <>
            <section className="rounded-[12px] border bg-card p-5">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs text-muted-foreground">当前通行证</p>
                  <h2 className="mt-1 text-xl font-semibold">
                    {data.data.active_tier_display || "当前无通行证"}
                  </h2>
                </div>
                <div className="flex items-center gap-2">{data.data.active_tier && <Status value="ACTIVE" label="生效中" />}<Button type="button" variant="outline" disabled={busy || data.loading} onClick={() => void data.reload().catch(() => {})}>刷新状态</Button></div>
              </div>
              <div className="mt-5">
                <DetailGrid
                  items={[
                    {
                      label: "公共订单",
                      value: data.data.current_delay_seconds
                        ? `发布 ${data.data.current_delay_seconds} 秒后可见`
                        : "发布后立即可见",
                    },
                    {
                      label: "钱包余额",
                      value: `${money(data.data.balance)} ${coinUnit()}`,
                    },
                    { label: "有效至", value: date(data.data.expires_at) },
                  ]}
                />
              </div>
            </section>
            <div className="grid gap-4 md:grid-cols-2">
              {!data.data.products.length && (
                <div className="md:col-span-2">
                  <EmptyState
                    title="暂无可用通行证"
                    description="运营开放档位后，可在这里比较并采购。"
                  />
                </div>
              )}
              {data.data.products.map((row) => (
                <section
                  key={row.tier}
                  className="rounded-[12px] border bg-card p-5"
                >
                  <h2 className="flex items-center gap-2 font-semibold">
                    <TicketCheck className="size-5 text-primary" />
                    {row.name}
                  </h2>
                  <p className="mt-4 text-lg font-semibold">
                    {money(row.daily_price)}
                    <span className="ml-1 text-xs font-normal text-muted-foreground">
                      {coinText("浪花 / 天")}</span>
                  </p>
                  <p className="mt-2 text-sm text-muted-foreground">
                    {row.delay_seconds
                      ? `发布 ${row.delay_seconds} 秒后可见`
                      : "发布后立即可见"}
                  </p>
                {data.data!.purchase_block_reason && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        {data.data!.purchase_block_reason}
                      </p>
                    )}
                  <Button
                    className="mt-5 h-11 w-full"
                    variant="outline"
                    disabled={
                      busy ||
                      data.loading || !!data.error ||
                      (!data.data!.points_payment_available && data.data!.balance < row.daily_price) ||
                      !!data.data!.purchase_block_reason
                    }
                    onClick={() => {
                      setProduct(row)
                      setIssue("")
                    }}
                  >
                    {data.data!.purchase_block_reason
                      ? "暂不可购买"
                      : "采购1天"}
                  </Button>
                </section>
              ))}
            </div>
            <Panel className="gap-3 p-4 md:p-4">
              <h2 className="flex items-center gap-2 text-sm font-semibold">
                <History className="size-4 text-primary" />
                采购记录
              </h2>
              {!data.data.history.length && (
                <p className="text-sm text-muted-foreground">暂无采购记录</p>
              )}
              {data.data.history.map((row) => (
                <div className="space-y-1 border-t pt-3 text-sm" key={row.id}>
                  <strong>
                    {row.tier_display} · {row.days} 天
                  </strong>
                  <p>{money(row.paid_amount)} {coinText(" 浪花")}</p>
                  {!!row.points_hundredths && <p>积分抵扣 {row.points_hundredths / 100} 积分，抵 {money(row.point_discount_amount)} {coinUnit()}</p>}
                  <p className="text-xs text-muted-foreground">
                    {date(row.starts_at)} — {date(row.expires_at)}
                  </p>
                  {row.refunded ? <p className="text-xs text-muted-foreground">已退款{row.points_returned ? `，退回 ${row.points_returned / 100} 积分` : ""}</p> : row.refundable && (
                    <Button type="button" variant="outline" disabled={busy || data.loading}
                      onClick={() => { setIssue(""); setRefundId(row.id) }}>申请退款</Button>
                  )}
                </div>
              ))}
            </Panel>
          </>
        )}
      </LoadState>
      <Confirm
        open={!!product}
        title="确认采购通行证"
        description="从钱包扣款，请核对档位、价格及实际生效时间。"
        onClose={() => setProduct(null)}
        onConfirm={() => void buy()}
        busy={busy}
        disabled={!!issue || data.loading || !!data.error || !!data.data?.purchase_block_reason ||
          selectedPoints === null || pointQuoting || (wantsPoints && (!pointQuote || !pointQuote.balance_sufficient)) ||
          (!!product && !wantsPoints && (data.data?.balance ?? 0) < product.daily_price)}
        label="确认采购"
      >
        <Notice tone="warning">{issue}</Notice>
        {data.loading && <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><RefreshCw className="size-4 animate-spin" aria-hidden="true" />正在更新通行证与钱包余额…</p>}
        {data.error && <Notice error>
          <div className="space-y-3">
            <p>{data.error}。通行证资料尚未更新，请重新读取后继续。</p>
            <Button type="button" variant="outline" disabled={busy || data.loading} onClick={() => void data.reload().catch(() => {})}><RefreshCw className="size-4" />重新读取通行证</Button>
          </div>
        </Notice>}
        {product && (
          <DetailGrid
            items={[
              { label: "通行证", value: product.name },
              { label: "采购时长", value: "1 天" },
              {
                label: "扣款金额",
                value: `${money(pointQuote?.payable_units ?? product.daily_price)} ${coinUnit()}`,
              },
              { label: "当前余额", value: `${money(data.data?.balance)} ${coinUnit()}` },
            ]}
          />
        )}
        {product && data.data?.points_payment_available && <div className="space-y-2">
          <label htmlFor="pass-points" className="text-sm">选择使用积分（选填）</label>
          <div className="flex gap-2"><Input id="pass-points" inputMode="decimal" value={pointInput} disabled={busy || pointQuoting}
            onChange={event => { pointQuoteSeq.current++; setPointInput(event.target.value); setPointQuote(null); setPointError("") }} />
            <Button type="button" variant="outline" disabled={busy || pointQuoting || !wantsPoints} onClick={() => void previewPoints()}>{pointQuoting ? "试算中" : "试算"}</Button></div>
          {selectedPoints === null && <p className="text-sm text-destructive">请输入最多两位小数的积分。</p>}
          {pointError && <p role="alert" className="text-sm text-destructive">{pointError}</p>}
          {pointQuote && <p className="text-sm">实际使用 {pointQuote.points_hundredths / 100} 积分，抵扣 {money(pointQuote.discount_units)} {coinUnit()}；{pointQuote.balance_sufficient ? "钱包余额充足" : "钱包余额不足"}。</p>}
        </div>}
        {issue && (
          <Button
            variant="outline"
            onClick={() => {
              setProduct(null)
              void data.reload().catch(() => {})
            }}
          >
            查看最新通行证
          </Button>
        )}
      </Confirm>
      <Confirm
        open={refundId != null}
        title="退还待生效通行证"
        description="店币及原使用积分将分别退回；后续待生效通行证会按购买顺序提前。"
        onClose={() => setRefundId(null)}
        onConfirm={() => void refundPass()}
        busy={busy}
        disabled={!!issue || data.loading || !!data.error}
        label="确认退款"
      >
        <Notice tone="warning">{issue}</Notice>
        {refundId != null && data.data && (() => {
          const row = data.data.history.find((item) => item.id === refundId)
          return row ? <DetailGrid items={[
            { label: "通行证", value: row.tier_display },
            { label: "退回金额", value: `${money(row.paid_amount)} ${coinUnit()}` },
            ...(row.points_hundredths ? [{ label: "退回积分", value: `${row.points_hundredths / 100} 积分` }] : []),
            { label: "原生效时间", value: date(row.starts_at) },
          ]} /> : null
        })()}
        {issue && <Button variant="outline" onClick={() => { setRefundId(null); void data.reload().catch(() => {}) }}>查看采购记录</Button>}
      </Confirm>
    </div>
  )
}
