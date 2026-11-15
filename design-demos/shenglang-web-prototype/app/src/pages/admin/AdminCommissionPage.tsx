import { useMemo, useState } from 'react'
import {
  BadgeCheck,
  Calculator,
  Check,
  CircleAlert,
  Coins,
  Eye,
  FileClock,
  Info,
  LockKeyhole,
  Percent,
  RefreshCw,
  Save,
  ShieldCheck,
  UsersRound,
} from 'lucide-react'
import type { AdminCommissionScenario } from '@/components/admin/admin-config'
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
  adminCommissionPriority,
  fixedAdminCommissionConfig,
  formatCommissionRaw,
  initialAdminCommissionConfig,
  parseCommissionCoins,
  quoteAdminCommission,
  type AdminCommissionConfig,
  type AdminCommissionQuote,
  type AdminCommissionType,
} from '@/pages/admin/admin-commission-data'
import { cn } from '@/lib/utils'

interface AdminCommissionPageProps {
  scenario: AdminCommissionScenario
  onScenarioChange: (scenario: AdminCommissionScenario) => void
}

interface CommissionDraft {
  type: AdminCommissionType
  rate: string
  fixed: string
}

function toDraft(config: AdminCommissionConfig): CommissionDraft {
  return { type: config.type, rate: String(config.rate), fixed: formatCommissionRaw(config.fixedRaw) }
}

function parseFixedRaw(value: string) {
  if (!/^(?:0|[1-9]\d{0,8})$/.test(value)) return null
  const raw = Number(value) * 10
  return Number.isSafeInteger(raw) && raw <= 2147483647 ? raw : null
}

export function AdminCommissionPage({ scenario, onScenarioChange }: AdminCommissionPageProps) {
  const initialConfig = scenario === 'fixed' ? fixedAdminCommissionConfig : initialAdminCommissionConfig
  const startsDirty = scenario === 'save-error' || scenario === 'save-review' || scenario === 'refresh-error'
  const [saved, setSaved] = useState(initialConfig)
  const [draft, setDraft] = useState<CommissionDraft>(() => startsDirty && initialConfig.type === 'PERCENT' ? { ...toDraft(initialConfig), rate: '25' } : toDraft(initialConfig))
  const [attempted, setAttempted] = useState(false)
  const [previewAttempted, setPreviewAttempted] = useState(false)
  const [previewAmount, setPreviewAmount] = useState('216')
  const [providerCount, setProviderCount] = useState<1 | 2>(2)
  const [previewResult, setPreviewResult] = useState<AdminCommissionQuote | null>(() => scenario === 'preview-loading' || scenario === 'preview-error' || scenario === 'save-review' || scenario === 'refresh-error' ? null : quoteAdminCommission(2160, 2, initialConfig))
  const [configResolved, setConfigResolved] = useState(false)
  const [previewResolved, setPreviewResolved] = useState(false)
  const [saveIssue, setSaveIssue] = useState(() => scenario === 'save-error' ? '上次保存未完成，当前输入已保留；核对后可重试。' : scenario === 'save-review' ? '保存结果未确认，请重新读取当前配置后再核对，避免重复提交。' : scenario === 'refresh-error' ? '配置刷新失败，当前展示上次成功读取的快照；编辑与试算已锁定。' : '')
  const [notice, setNotice] = useState('')

  const readOnly = scenario === 'readonly'
  const forbidden = scenario === 'forbidden'
  const loading = scenario === 'loading' && !configResolved
  const loadError = scenario === 'error' && !configResolved
  const previewLoading = scenario === 'preview-loading' && !previewResolved
  const previewError = scenario === 'preview-error' && !previewResolved
  const needsRefresh = scenario === 'save-review' || scenario === 'refresh-error'
  const configUnavailable = loading || loadError || forbidden
  const validRate = /^(?:0|[1-9]\d{0,2})$/.test(draft.rate) && Number(draft.rate) <= 100
  const draftFixedRaw = parseFixedRaw(draft.fixed)
  const validDraft = draft.type === 'PERCENT' ? validRate : draftFixedRaw !== null
  const previewAmountRaw = parseCommissionCoins(previewAmount)
  const inputDisabled = readOnly || needsRefresh || configUnavailable

  const dirty = useMemo(() => {
    if (draft.type !== saved.type || Number(draft.rate) !== saved.rate) return true
    return draft.type === 'FIXED' && (draftFixedRaw === null || draftFixedRaw !== saved.fixedRaw)
  }, [draft, draftFixedRaw, saved])

  function updateDraft(next: Partial<CommissionDraft>) {
    setDraft(current => ({ ...current, ...next }))
    setAttempted(false)
    setSaveIssue('')
    setNotice('')
  }

  function saveConfig() {
    setAttempted(true)
    setNotice('')
    if (readOnly || needsRefresh || configUnavailable || !validDraft || !dirty) return
    const next: AdminCommissionConfig = {
      type: draft.type,
      rate: Number(draft.rate),
      fixedRaw: draft.type === 'FIXED' ? draftFixedRaw ?? 0 : 0,
      updatedAt: '2026-11-04 17:28',
    }
    setSaved(next)
    setDraft(toDraft(next))
    setAttempted(false)
    setSaveIssue('')
    setPreviewResult(null)
    setNotice(`订单默认抽成已保存为${next.type === 'FIXED' ? `整单固定 ${formatCommissionRaw(next.fixedRaw)} 浪花` : `${next.rate}%`}；旧手工报单默认比例保留为 ${next.rate}%。`)
  }

  function runPreview() {
    setPreviewAttempted(true)
    setNotice('')
    if (!previewAmountRaw || needsRefresh || configUnavailable || previewLoading) return
    setPreviewResolved(true)
    setPreviewResult(quoteAdminCommission(previewAmountRaw, providerCount, saved))
  }

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm">
      <div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
        <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">订单默认抽成</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">后续普通订单</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">设置未命中商品、活动或自动策略时的全局默认值，并通过单陪／双陪试算核对实际分账。</p></div>
        <dl className="grid grid-cols-3 gap-5"><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{configUnavailable ? '—' : saved.type === 'FIXED' ? formatCommissionRaw(saved.fixedRaw) : `${saved.rate}%`}</dd><dt className="mt-1 text-[11px] text-blue-100/65">{saved.type === 'FIXED' ? '整单固定浪花' : '默认百分比'}</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{configUnavailable ? '—' : `${saved.rate}%`}</dd><dt className="mt-1 text-[11px] text-blue-100/65">旧报单保留比例</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-sm font-semibold leading-8">分账快照</dd><dt className="mt-1 text-[11px] text-blue-100/65">历史订单口径</dt></div></dl>
      </div>
    </section>

    <Alert className="border-primary/25 bg-primary/5"><Info className="size-4 text-primary" /><AlertTitle>默认值不是全平台统一抽成</AlertTitle><AlertDescription>商品自定义、活动、订单档位、体验及陪玩自动规则优先；礼物使用独立规则。保存只影响后续普通订单，已有订单仍按自身分账快照结算。</AlertDescription></Alert>
    {readOnly && <Alert><Eye className="size-4" /><AlertTitle>当前为仅查看权限</AlertTitle><AlertDescription>可读取配置并进行本地试算，但不能保存修改；修改配置需要 report:audit 权限。</AlertDescription></Alert>}
    {saveIssue && <Alert variant="destructive" role="alert"><CircleAlert className="size-4" /><AlertTitle>{scenario === 'save-review' ? '保存结果待核对' : scenario === 'refresh-error' ? '配置刷新失败' : '配置尚未保存'}</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">{saveIssue}{needsRefresh && <Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新读取当前配置</Button>}</AlertDescription></Alert>}
    {notice && <Alert role="status" className="border-success/30 bg-success/10"><BadgeCheck className="size-4 text-success" /><AlertDescription>{notice}</AlertDescription></Alert>}

    {forbidden ? <Card><CardContent className="flex min-h-80 flex-col items-center justify-center p-8 text-center"><LockKeyhole className="size-8 text-muted-foreground" /><h2 className="mt-4 text-lg font-semibold">当前账号无抽成配置查看权限</h2><p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">需要 report:view 或 report:audit 权限。当前页面不会显示比例、固定金额或分账试算。</p></CardContent></Card> : <>
      {loadError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>抽成配置加载失败</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">尚未取得可核对的配置，不会展示推测值。<Button variant="link" className="h-auto p-0" onClick={() => setConfigResolved(true)}>重新加载</Button></AlertDescription></Alert>}
      {loading ? <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(340px,2fr)]" aria-label="正在读取抽成配置"><Skeleton className="h-[450px] rounded-xl" /><Skeleton className="h-[450px] rounded-xl" /></div> : !loadError && <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(340px,2fr)]">
        <Card>
          <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0"><div><CardTitle className="flex items-center gap-2 text-base"><Percent className="size-4 text-primary" />全局默认配置</CardTitle><CardDescription className="mt-1">当前读取于 {saved.updatedAt} · 多人同时修改时以最后保存为准</CardDescription></div><Badge variant="outline" className="gap-1.5">{readOnly ? <Eye className="size-3.5" /> : <ShieldCheck className="size-3.5" />}{readOnly ? '只读权限' : dirty ? '有未保存修改' : '配置已读取'}</Badge></CardHeader>
          <CardContent className="space-y-5 border-t pt-5">
            <div className="space-y-2"><Label>抽成方式</Label><div className="grid grid-cols-2 gap-2 rounded-xl border bg-muted/30 p-1" role="group" aria-label="订单默认抽成方式"><Button type="button" variant={draft.type === 'PERCENT' ? 'default' : 'ghost'} aria-pressed={draft.type === 'PERCENT'} disabled={inputDisabled} onClick={() => updateDraft({ type: 'PERCENT' })}><Percent className="size-4" />百分比</Button><Button type="button" variant={draft.type === 'FIXED' ? 'default' : 'ghost'} aria-pressed={draft.type === 'FIXED'} disabled={inputDisabled} onClick={() => updateDraft({ type: 'FIXED' })}><Coins className="size-4" />整单固定金额</Button></div></div>
            {draft.type === 'PERCENT' ? <div className="space-y-2"><Label htmlFor="commission-rate">默认抽成比例（%）</Label><div className="relative"><Input id="commission-rate" value={draft.rate} disabled={inputDisabled} inputMode="numeric" maxLength={3} aria-invalid={attempted && !validRate} onChange={event => updateDraft({ rate: event.target.value.trim() })} className="h-11 pr-9" /><span className="pointer-events-none absolute right-3 top-3 text-sm text-muted-foreground">%</span></div>{attempted && !validRate ? <p className="text-xs text-destructive">抽成比例须为 0–100 的整数</p> : <p className="text-[11px] text-muted-foreground">0 表示不抽成，100 表示抽取全部份额。</p>}</div> : <div className="space-y-2"><Label htmlFor="commission-fixed">整单固定抽成（浪花）</Label><Input id="commission-fixed" value={draft.fixed} disabled={inputDisabled} inputMode="numeric" aria-invalid={attempted && draftFixedRaw === null} onChange={event => updateDraft({ fixed: event.target.value.trim() })} className="h-11" placeholder="例如 30" />{attempted && draftFixedRaw === null ? <p className="text-xs text-destructive">请输入不小于 0 的有效整数金额</p> : <p className="text-[11px] text-muted-foreground">整笔订单仅扣一次，双陪按结算份额分摊；不超过订单实付。</p>}</div>}
            <dl className="grid gap-3 rounded-xl border bg-muted/20 p-4 text-xs sm:grid-cols-2"><div><dt className="text-muted-foreground">旧手工报单默认比例</dt><dd className="mt-1.5 font-medium">{draft.rate}% · {draft.type === 'FIXED' ? '保留，不改为固定金额' : '与默认比例一致'}</dd></div><div><dt className="text-muted-foreground">生效范围</dt><dd className="mt-1.5 font-medium">后续普通订单 · 不回写历史</dd></div></dl>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4"><p className="text-[11px] text-muted-foreground">试算始终使用已保存配置，不使用当前未保存输入。</p>{!readOnly && <Button disabled={needsRefresh || !dirty} onClick={saveConfig}><Save className="size-4" />保存配置</Button>}</div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Calculator className="size-4 text-primary" />普通订单分账试算</CardTitle><CardDescription className="mt-1">按当前已保存的{saved.type === 'FIXED' ? `整单固定 ${formatCommissionRaw(saved.fixedRaw)} 浪花` : `${saved.rate}%`}计算</CardDescription></CardHeader>
          <CardContent className="space-y-4 border-t pt-5">
            {(dirty || needsRefresh) && <Alert><Info className="size-4" /><AlertDescription>{needsRefresh ? '配置待核对，旧试算结果已隐藏。' : '当前修改尚未保存，以下试算仍使用已保存配置。'}</AlertDescription></Alert>}
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_132px]"><div className="space-y-2"><Label htmlFor="commission-preview-amount">订单实付（浪花）</Label><Input id="commission-preview-amount" value={previewAmount} disabled={needsRefresh} inputMode="numeric" aria-invalid={previewAttempted && !previewAmountRaw} onChange={event => { setPreviewAmount(event.target.value.trim()); setPreviewResult(null); setPreviewAttempted(false); setPreviewResolved(false) }} onKeyDown={event => { if (event.key === 'Enter') runPreview() }} /></div><div className="space-y-2"><Label>陪玩人数</Label><Select value={String(providerCount)} disabled={needsRefresh} onValueChange={value => { setProviderCount(Number(value) as 1 | 2); setPreviewResult(null); setPreviewResolved(false) }}><SelectTrigger className="w-full" aria-label="试算陪玩人数"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="1">单陪</SelectItem><SelectItem value="2">双陪</SelectItem></SelectContent></Select></div></div>
            {previewAttempted && !previewAmountRaw && <p className="text-xs text-destructive">请输入大于 0 的整数订单金额</p>}
            <Button variant="outline" className="w-full" disabled={needsRefresh || previewLoading} onClick={runPreview}>{previewLoading ? <RefreshCw className="size-4 animate-spin" /> : <Calculator className="size-4" />}{previewLoading ? '试算中…' : previewError && !previewResolved ? '重试试算' : '按已保存配置试算'}</Button>
            {previewError && !previewResolved && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>试算失败，请重试；不会沿用旧结果代替本次结果。</AlertDescription></Alert>}
            {previewLoading ? <div className="space-y-3 rounded-xl bg-muted/40 p-4" aria-label="正在试算订单分账"><Skeleton className="h-8 w-full" /><Skeleton className="h-24 w-full" /><Skeleton className="h-16 w-full" /></div> : needsRefresh ? <div className="flex min-h-40 flex-col items-center justify-center rounded-xl border border-dashed p-6 text-center"><LockKeyhole className="size-6 text-muted-foreground" /><p className="mt-3 text-sm font-medium">配置核对前暂停试算</p></div> : previewResult && <div className="rounded-xl border bg-primary/5 p-4" role="status">
              <div className="grid grid-cols-2 gap-3"><div><p className="text-[11px] text-muted-foreground">平台合计抽成</p><p className="mt-1 text-xl font-semibold tabular-nums text-primary">{formatCommissionRaw(previewResult.commissionRaw)} 浪花</p></div><div><p className="text-[11px] text-muted-foreground">陪玩合计收益</p><p className="mt-1 text-xl font-semibold tabular-nums">{formatCommissionRaw(previewResult.incomeRaw)} 浪花</p></div></div>
              <Table className="mt-4"><TableHeader><TableRow><TableHead>陪玩</TableHead><TableHead className="text-right">结算基数</TableHead><TableHead className="text-right">抽成</TableHead><TableHead className="text-right">收益</TableHead></TableRow></TableHeader><TableBody>{previewResult.shares.map(share => <TableRow key={share.provider}><TableCell className="text-xs font-medium">{share.provider}</TableCell><TableCell className="text-right text-xs tabular-nums">{formatCommissionRaw(share.baseRaw)}</TableCell><TableCell className="text-right text-xs tabular-nums">{formatCommissionRaw(share.commissionRaw)}</TableCell><TableCell className="text-right text-xs font-medium tabular-nums">{formatCommissionRaw(share.incomeRaw)}</TableCell></TableRow>)}</TableBody></Table>
              <p className="mt-3 text-[11px] leading-5 text-muted-foreground">单位：浪花。按内部账务整数向下取整，固定抽成按最大余数法分摊，余数相同按陪玩顺序处理。</p>
            </div>}
          </CardContent>
        </Card>
      </div>}

      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2 text-base"><FileClock className="size-4 text-primary" />规则命中顺序</CardTitle><CardDescription className="mt-1">先核对订单自身与业务专属规则，最后才使用本页默认值</CardDescription></CardHeader>
        <CardContent className="border-t pt-5"><ol className="grid gap-3 lg:grid-cols-5">{adminCommissionPriority.map((item, index) => <li key={item.name} className={cn('rounded-xl border p-4', index === adminCommissionPriority.length - 1 && 'border-primary/30 bg-primary/5')}><div className="flex items-center justify-between gap-2"><span className="grid size-7 place-items-center rounded-full bg-muted text-xs font-semibold">{index + 1}</span>{item.locked ? <LockKeyhole className="size-4 text-muted-foreground" /> : index === adminCommissionPriority.length - 1 ? <Check className="size-4 text-primary" /> : <UsersRound className="size-4 text-muted-foreground" />}</div><h3 className="mt-3 text-sm font-semibold">{item.name}</h3><p className="mt-1 text-xs font-medium text-primary">{item.rule}</p><p className="mt-2 text-[11px] leading-5 text-muted-foreground">{item.description}</p></li>)}</ol><p className="mt-4 text-xs text-muted-foreground">本页配置与试算均为本地示例，不读取或修改真实订单、报单及钱包。</p></CardContent>
      </Card>
    </>}
  </div>
}
