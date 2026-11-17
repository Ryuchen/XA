import { useMemo, useState } from 'react'
import {
  BadgeCheck,
  Calculator,
  CircleAlert,
  Clock3,
  Eye,
  FileClock,
  Info,
  LockKeyhole,
  ReceiptText,
  RefreshCw,
  Save,
  ShieldCheck,
  Store,
} from 'lucide-react'
import type { AdminPaymentFeeScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  formatPaymentFeeFen,
  initialPaymentFeeConfig,
  initialPaymentFeeHistory,
  normalizeRatePercent,
  parsePaymentFeeYuan,
  paymentFeeBasisLabels,
  paymentFeeRoundingLabels,
  quotePaymentFee,
  type PaymentFeeBasis,
  type PaymentFeeConfig,
  type PaymentFeeHistory,
  type PaymentFeePreview,
  type PaymentFeeRounding,
} from '@/pages/admin/admin-payment-fee-data'

interface AdminPaymentFeePageProps {
  scenario: AdminPaymentFeeScenario
  onScenarioChange: (scenario: AdminPaymentFeeScenario) => void
}

type PaymentFeeDraft = Omit<PaymentFeeConfig, 'revision' | 'updatedAt'>

const emptyDraft: PaymentFeeDraft = {
  merchantId: '',
  appid: '',
  ratePercent: '',
  feeBasis: 'SERVICE_AMOUNT',
  rounding: 'HALF_UP',
}

function toDraft(config: PaymentFeeConfig): PaymentFeeDraft {
  return {
    merchantId: config.merchantId,
    appid: config.appid,
    ratePercent: config.ratePercent,
    feeBasis: config.feeBasis,
    rounding: config.rounding,
  }
}

export function AdminPaymentFeePage({ scenario, onScenarioChange }: AdminPaymentFeePageProps) {
  const unconfigured = scenario === 'unconfigured'
  const initialConfig = unconfigured ? null : initialPaymentFeeConfig
  const startsDirty = scenario === 'saving' || scenario === 'save-error' || scenario === 'conflict' || scenario === 'save-review'
  const [saved, setSaved] = useState<PaymentFeeConfig | null>(initialConfig)
  const [draft, setDraft] = useState<PaymentFeeDraft>(() => initialConfig ? { ...toDraft(initialConfig), ratePercent: startsDirty ? '0.6500' : initialConfig.ratePercent } : emptyDraft)
  const [history, setHistory] = useState<PaymentFeeHistory[]>(() => unconfigured || scenario === 'history-empty' ? [] : initialPaymentFeeHistory)
  const [attempted, setAttempted] = useState(false)
  const [previewAttempted, setPreviewAttempted] = useState(false)
  const [previewYuan, setPreviewYuan] = useState('100.00')
  const [previewResult, setPreviewResult] = useState<PaymentFeePreview | null>(() => initialConfig && scenario !== 'preview-loading' && scenario !== 'preview-error' ? quotePaymentFee(10000, initialConfig) : null)
  const [configResolved, setConfigResolved] = useState(false)
  const [historyResolved, setHistoryResolved] = useState(false)
  const [previewResolved, setPreviewResolved] = useState(false)
  const needsRefresh = scenario === 'conflict' || scenario === 'save-review' || scenario === 'refresh-error'
  const [saveIssue, setSaveIssue] = useState(() => scenario === 'save-error' ? '保存失败，当前输入已保留；可重试，或刷新核对当前版本。' : scenario === 'conflict' ? '配置已被其他管理员修改，请刷新后核对。' : scenario === 'save-review' ? '保存结果未确认，请刷新核对，避免重复提交。' : scenario === 'refresh-error' ? '刷新失败，当前展示的是上次读取的快照；配置和试算已暂时锁定。' : '')
  const [notice, setNotice] = useState('')

  const readOnly = scenario === 'readonly'
  const forbidden = scenario === 'forbidden'
  const saving = scenario === 'saving'
  const loading = scenario === 'loading' && !configResolved
  const loadError = scenario === 'error' && !configResolved
  const historyLoading = scenario === 'history-loading' && !historyResolved
  const historyError = scenario === 'history-error' && !historyResolved
  const previewLoading = scenario === 'preview-loading' && !previewResolved
  const previewError = scenario === 'preview-error' && !previewResolved
  const configUnavailable = loading || loadError || forbidden

  const dirty = useMemo(() => {
    if (!saved) return Boolean(draft.merchantId || draft.appid || draft.ratePercent) || draft.feeBasis !== emptyDraft.feeBasis || draft.rounding !== emptyDraft.rounding
    if (draft.merchantId !== saved.merchantId || draft.appid !== saved.appid || draft.feeBasis !== saved.feeBasis || draft.rounding !== saved.rounding) return true
    if (draft.ratePercent && saved.ratePercent && /^(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,4})?$/.test(draft.ratePercent)) return Number(draft.ratePercent) !== Number(saved.ratePercent)
    return draft.ratePercent !== saved.ratePercent
  }, [draft, saved])

  const validMerchant = /^[0-9]{8,32}$/.test(draft.merchantId)
  const validAppid = /^[A-Za-z0-9_-]{1,32}$/.test(draft.appid)
  const validRate = /^(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,4})?$/.test(draft.ratePercent) && Number(draft.ratePercent) <= 100 && !(draft.feeBasis === 'PAYMENT_TOTAL' && Number(draft.ratePercent) === 100)
  const validForm = validMerchant && validAppid && validRate
  const previewFen = parsePaymentFeeYuan(previewYuan)
  const inputDisabled = readOnly || saving || needsRefresh || configUnavailable

  function updateDraft<K extends keyof PaymentFeeDraft>(key: K, value: PaymentFeeDraft[K]) {
    setDraft(current => ({ ...current, [key]: value }))
    setNotice('')
    setSaveIssue('')
  }

  function refreshConfig() {
    onScenarioChange('default')
  }

  function saveConfig() {
    setAttempted(true)
    setNotice('')
    if (readOnly || saving || needsRefresh || configUnavailable || !validForm || !dirty) return
    const next: PaymentFeeConfig = {
      ...draft,
      ratePercent: normalizeRatePercent(draft.ratePercent),
      revision: (saved?.revision ?? 0) + 1,
      updatedAt: '2026-11-04 09:18',
    }
    setSaved(next)
    setDraft(toDraft(next))
    setHistory(rows => [{ ...next, operatorName: '林岚' }, ...rows])
    setAttempted(false)
    setSaveIssue('')
    setPreviewResult(null)
    setNotice(`配置已保存为版本 ${next.revision}；历史记录已同步更新，试算将使用新版本。`)
  }

  function runPreview() {
    setPreviewAttempted(true)
    setNotice('')
    if (!saved || !previewFen || needsRefresh || saving || previewLoading) return
    setPreviewResolved(true)
    setPreviewResult(quotePaymentFee(previewFen, saved))
  }

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm">
      <div className="grid gap-4 p-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
        <div>
          <div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">微信支付费率</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">版本化配置</Badge></div>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">维护商户签约信息、老板报价口径与取整方式，通过独立试算和变更记录复核每一次调整。</p>
        </div>
        <dl className="grid grid-cols-3 gap-4">
          <div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{saved && !forbidden ? `v${saved.revision}` : '—'}</dd><dt className="mt-1 text-[11px] text-blue-100/65">当前版本</dt></div>
          <div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{forbidden ? '无权限' : saved ? `${Number(saved.ratePercent).toFixed(2)}%` : '未配置'}</dd><dt className="mt-1 text-[11px] text-blue-100/65">商户费率</dt></div>
          <div className="border-l border-white/15 pl-4"><dd className="text-sm font-semibold leading-8">{saved && !forbidden ? paymentFeeRoundingLabels[saved.rounding].replace('不足一分', '') : '—'}</dd><dt className="mt-1 text-[11px] text-blue-100/65">取整方式</dt></div>
        </dl>
      </div>
    </section>

    <Alert className="border-primary/25 bg-primary/5"><Info className="size-4 text-primary" /><AlertTitle>微信直接支付尚未开通</AlertTitle><AlertDescription>此处仅供配置与报价试算，不影响现有余额下单，也不会产生真实扣费；实际渠道费用以商户合同和微信账单为准。</AlertDescription></Alert>

    {saveIssue && <Alert variant="destructive" role="alert"><CircleAlert className="size-4" /><AlertTitle>{scenario === 'conflict' ? '配置版本冲突' : scenario === 'save-review' ? '保存结果待核对' : scenario === 'refresh-error' ? '配置刷新失败' : '配置尚未保存'}</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">{saveIssue}{needsRefresh && <Button variant="link" className="h-auto p-0" onClick={refreshConfig}>刷新并核对</Button>}</AlertDescription></Alert>}
    {notice && <Alert role="status" className="border-success/30 bg-success/10"><BadgeCheck className="size-4 text-success" /><AlertDescription>{notice}</AlertDescription></Alert>}

    {forbidden ? <Card><CardContent className="flex min-h-72 flex-col items-center justify-center p-8 text-center"><LockKeyhole className="size-8 text-muted-foreground" /><h2 className="mt-4 text-lg font-semibold">当前账号无查看费率的权限</h2><p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">请联系具备支付费率权限的管理员。当前页面不会显示商户号、AppID、报价规则或变更记录。</p></CardContent></Card> : <>
      {loadError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>支付费率配置加载失败</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">尚未取得可核对的配置，不会展示推测内容。<Button variant="link" className="h-auto p-0" onClick={() => setConfigResolved(true)}>重新加载</Button></AlertDescription></Alert>}
      {loading ? <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(320px,2fr)]" aria-label="正在读取支付费率配置"><Skeleton className="h-[440px] rounded-xl" /><Skeleton className="h-[440px] rounded-xl" /></div> : !loadError && <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(320px,2fr)]">
        <Card>
          <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3 space-y-0">
            <div><CardTitle className="flex items-center gap-2 text-base"><Store className="size-4 text-primary" />商户与费率</CardTitle><CardDescription className="mt-1">{saved ? `当前配置版本 ${saved.revision} · 更新于 ${saved.updatedAt}` : '尚未配置，首次保存将创建版本 1'}</CardDescription></div>
            <Badge variant="outline" className="gap-1.5">{readOnly ? <Eye className="size-3.5" /> : <ShieldCheck className="size-3.5" />}{readOnly ? '只读权限' : dirty ? '有未保存修改' : '可编辑'}</Badge>
          </CardHeader>
          <CardContent className="space-y-4 border-t pt-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2"><Label htmlFor="payment-merchant">微信支付商户号</Label><Input id="payment-merchant" value={draft.merchantId} disabled={inputDisabled} maxLength={32} inputMode="numeric" autoComplete="off" aria-invalid={attempted && !validMerchant} onChange={event => updateDraft('merchantId', event.target.value.trim())} placeholder="8–32 位数字" />{attempted && !validMerchant ? <p className="text-xs text-destructive">请输入 8–32 位数字</p> : <p className="text-[11px] text-muted-foreground">仅保存商户标识，不保存支付密钥</p>}</div>
              <div className="space-y-2"><Label htmlFor="payment-appid">微信 AppID</Label><Input id="payment-appid" value={draft.appid} disabled={inputDisabled} maxLength={32} autoComplete="off" aria-invalid={attempted && !validAppid} onChange={event => updateDraft('appid', event.target.value.trim())} placeholder="例如 wx1234567890abcdef" />{attempted && !validAppid && <p className="text-xs text-destructive">请输入有效的 AppID</p>}</div>
              <div className="space-y-2"><Label htmlFor="payment-rate">商户费率（%）</Label><div className="relative"><Input id="payment-rate" className="pr-9" value={draft.ratePercent} disabled={inputDisabled} maxLength={8} inputMode="decimal" aria-invalid={attempted && !validRate} onChange={event => updateDraft('ratePercent', event.target.value.trim())} placeholder="例如 0.6" /><span className="pointer-events-none absolute right-3 top-2.5 text-sm text-muted-foreground">%</span></div>{attempted && !validRate ? <p className="text-xs text-destructive">{draft.feeBasis === 'PAYMENT_TOTAL' && Number(draft.ratePercent) === 100 ? '以支付总额为基数时费率须小于 100%' : '请输入 0–100 的百分数，最多四位小数'}</p> : <p className="text-[11px] text-muted-foreground">填写签约费率百分数，例如 0.6 表示 0.6%</p>}</div>
              <div className="space-y-2"><Label>手续费计算基数</Label><Select value={draft.feeBasis} disabled={inputDisabled} onValueChange={value => updateDraft('feeBasis', value as PaymentFeeBasis)}><SelectTrigger className="w-full" aria-label="手续费计算基数"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="SERVICE_AMOUNT">服务成交金额</SelectItem><SelectItem value="PAYMENT_TOTAL">服务金额及手续费总额</SelectItem></SelectContent></Select><p className="text-[11px] text-muted-foreground">用于向老板报价，不代表渠道实际计费口径</p></div>
              <div className="space-y-2 sm:col-span-2"><Label>手续费取整方式</Label><Select value={draft.rounding} disabled={inputDisabled} onValueChange={value => updateDraft('rounding', value as PaymentFeeRounding)}><SelectTrigger className="w-full" aria-label="手续费取整方式"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="DOWN">不足一分舍去</SelectItem><SelectItem value="UP">不足一分进一分</SelectItem><SelectItem value="HALF_UP">四舍五入到分</SelectItem></SelectContent></Select></div>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4">
              <p className="text-[11px] text-muted-foreground">保存会生成新版本，历史版本不会被覆盖。</p>
              {!readOnly && <Button disabled={saving || needsRefresh || !dirty} onClick={saveConfig}><Save className="size-4" />{saving ? '保存中…' : scenario === 'save-error' && !previewResolved ? '重试保存' : '保存配置'}</Button>}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Calculator className="size-4 text-primary" />手续费试算</CardTitle><CardDescription className="mt-1">{saved ? `仅使用已保存的版本 ${saved.revision}，金额以人民币元计` : '保存首个费率版本后即可试算'}</CardDescription></CardHeader>
          <CardContent className="space-y-4 border-t pt-4">
            {!saved ? <div className="flex min-h-64 flex-col items-center justify-center rounded-xl border border-dashed p-6 text-center"><Calculator className="size-7 text-muted-foreground" /><h2 className="mt-4 text-sm font-semibold">暂无可试算配置</h2><p className="mt-2 text-xs leading-5 text-muted-foreground">完整填写商户、费率和计算规则并保存，再进行金额试算。</p></div> : <>
              {(dirty || needsRefresh) && <Alert><Info className="size-4" /><AlertDescription>{needsRefresh ? '配置待核对，试算暂不可用。' : `当前修改尚未保存，试算仍使用版本 ${saved.revision}。`}</AlertDescription></Alert>}
              <div className="space-y-2"><Label htmlFor="payment-preview-amount">服务成交金额（元）</Label><div className="flex gap-2"><Input id="payment-preview-amount" value={previewYuan} inputMode="decimal" maxLength={12} disabled={needsRefresh || saving} aria-invalid={previewAttempted && !previewFen} onChange={event => { setPreviewYuan(event.target.value.trim()); setPreviewResult(null); setPreviewAttempted(false); setPreviewResolved(false) }} onKeyDown={event => { if (event.key === 'Enter') runPreview() }} /><Button variant="outline" disabled={needsRefresh || saving || previewLoading} onClick={runPreview}>{previewLoading ? <RefreshCw className="size-4 animate-spin" /> : <Calculator className="size-4" />}{previewLoading ? '试算中…' : previewError && !previewResolved ? '重试试算' : '开始试算'}</Button></div>{previewAttempted && !previewFen && <p className="text-xs text-destructive">请输入大于 0、最多两位小数的金额</p>}</div>
              {previewError && !previewResolved && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>试算失败，请重试；不会沿用旧结果代替本次报价。</AlertDescription></Alert>}
              {previewLoading ? <div className="space-y-3 rounded-xl bg-muted/40 p-4" aria-label="正在试算手续费"><Skeleton className="h-4 w-28" /><Skeleton className="h-8 w-full" /><Skeleton className="h-8 w-full" /><Skeleton className="h-10 w-full" /></div> : needsRefresh ? <div className="flex min-h-40 flex-col items-center justify-center rounded-xl border border-dashed p-6 text-center"><LockKeyhole className="size-6 text-muted-foreground" /><p className="mt-3 text-sm font-medium">旧试算结果已隐藏</p><p className="mt-1.5 text-xs text-muted-foreground">刷新核对当前配置后，可重新试算。</p></div> : previewResult && <div className="rounded-xl border bg-primary/5 p-4" role="status">
                <div className="flex items-center justify-between gap-3"><span className="text-xs font-medium">预计支付明细</span><Badge variant="outline">版本 {previewResult.revision}</Badge></div>
                <dl className="mt-4 space-y-3 text-sm"><div className="flex items-center justify-between gap-4"><dt className="text-muted-foreground">服务金额</dt><dd className="tabular-nums">¥ {formatPaymentFeeFen(previewResult.serviceFen)}</dd></div><div className="flex items-center justify-between gap-4"><dt className="text-muted-foreground">预计手续费</dt><dd className="font-medium tabular-nums text-primary">+ ¥ {formatPaymentFeeFen(previewResult.feeFen)}</dd></div><div className="flex items-center justify-between gap-4 border-t pt-3 text-base font-semibold"><dt>预计合计</dt><dd className="tabular-nums">¥ {formatPaymentFeeFen(previewResult.totalFen)}</dd></div></dl>
                <p className="mt-4 text-[11px] leading-5 text-muted-foreground">按{paymentFeeBasisLabels[saved.feeBasis]}计算，{paymentFeeRoundingLabels[saved.rounding]}；结果仅为报价估算。</p>
              </div>}
            </>}
          </CardContent>
        </Card>
      </div>}

      <Card className="min-w-0 overflow-hidden">
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3 space-y-0"><div><CardTitle className="flex items-center gap-2 text-base"><FileClock className="size-4 text-primary" />费率变更记录</CardTitle><CardDescription className="mt-1">按版本倒序核对商户信息、报价口径与操作人</CardDescription></div><Badge variant="outline">{history.length} 个示例版本</Badge></CardHeader>
        {historyError && !historyResolved ? <div className="border-t p-4"><Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>变更记录加载失败</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">当前配置与试算不受影响。<Button variant="link" className="h-auto p-0" onClick={() => setHistoryResolved(true)}>重新加载记录</Button></AlertDescription></Alert></div> : historyLoading ? <div className="space-y-3 border-t p-4" aria-label="正在加载费率变更记录">{[0, 1, 2].map(item => <Skeleton key={item} className="h-14 w-full" />)}</div> : <>
          <div className="overflow-x-auto border-t"><Table className="min-w-[1040px]"><TableHeader><TableRow><TableHead>版本与时间</TableHead><TableHead>商户号 / AppID</TableHead><TableHead>费率 / 计算基数</TableHead><TableHead>取整方式</TableHead><TableHead>操作人</TableHead></TableRow></TableHeader><TableBody>{history.map(row => <TableRow key={row.revision}>
            <TableCell><div className="flex items-center gap-2"><strong className="text-sm">版本 {row.revision}</strong>{row.revision === saved?.revision && <Badge variant="secondary">当前</Badge>}</div><p className="mt-1.5 flex items-center gap-1.5 text-[11px] text-muted-foreground"><Clock3 className="size-3" />{row.updatedAt}</p></TableCell>
            <TableCell><span className="font-mono text-xs">{row.merchantId}</span><p className="mt-1.5 font-mono text-[11px] text-muted-foreground">{row.appid}</p></TableCell>
            <TableCell><strong className="text-sm tabular-nums">{row.ratePercent}%</strong><p className="mt-1.5 text-[11px] text-muted-foreground">{paymentFeeBasisLabels[row.feeBasis]}</p></TableCell>
            <TableCell className="text-xs">{paymentFeeRoundingLabels[row.rounding]}</TableCell><TableCell><div className="flex items-center gap-2"><span className="grid size-7 place-items-center rounded-full bg-primary/10 text-[11px] font-medium text-primary">{row.operatorName.slice(0, 1)}</span><span className="text-xs">{row.operatorName}</span></div></TableCell>
          </TableRow>)}</TableBody></Table>{history.length === 0 && <div className="flex min-h-48 flex-col items-center justify-center p-8 text-center"><ReceiptText className="size-7 text-muted-foreground" /><h2 className="mt-4 text-sm font-semibold">暂无费率变更记录</h2><p className="mt-2 text-xs text-muted-foreground">首次保存配置后，将在这里形成不可覆盖的版本记录。</p></div>}</div>
          <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>每页 10 条 · 所有记录均为本地示例</span><div className="flex items-center gap-2"><Button variant="outline" size="sm" disabled>上一页</Button><span>第 1 页 / 共 1 页</span><Button variant="outline" size="sm" disabled>下一页</Button></div></div>
        </>}
      </Card>
    </>}

  </div>
}
