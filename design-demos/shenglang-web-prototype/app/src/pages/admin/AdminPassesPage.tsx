import { useMemo, useState } from 'react'
import {
  BadgeCheck,
  CircleAlert,
  Clock3,
  CreditCard,
  Eye,
  Info,
  ReceiptText,
  Save,
  Search,
  ShieldCheck,
  TimerReset,
  WalletCards,
} from 'lucide-react'
import type { AdminPassesScenario } from '@/components/admin/admin-config'
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
  adminPassPurchases,
  adminPassTierLabel,
  initialAdminPassProducts,
  initialNoPassDelaySeconds,
  type AdminPassTier,
} from '@/pages/admin/admin-pass-data'
import { formatWaveAmount } from '@/lib/format'

interface AdminPassesPageProps {
  scenario: AdminPassesScenario
  onScenarioChange: (scenario: AdminPassesScenario) => void
}

interface PassProductDraft {
  tier: AdminPassTier
  name: string
  dailyPrice: string
  delaySeconds: string
}

const createDraft = (): PassProductDraft[] => initialAdminPassProducts.map(item => ({
  tier: item.tier,
  name: item.name,
  dailyPrice: String(item.dailyPrice),
  delaySeconds: String(item.delaySeconds),
}))

function validDelay(value: string) {
  return value.trim() !== '' && Number.isInteger(Number(value)) && Number(value) >= 0 && Number(value) <= 86400
}

function validPrice(value: string) {
  return /^\d+$/.test(value.trim()) && Number.isSafeInteger(Number(value)) && Number(value) >= 1
}

export function AdminPassesPage({ scenario, onScenarioChange }: AdminPassesPageProps) {
  const [draft, setDraft] = useState<PassProductDraft[]>(createDraft)
  const [savedDelays, setSavedDelays] = useState(() => initialAdminPassProducts.map(item => item.delaySeconds))
  const [noPassDelay, setNoPassDelay] = useState(String(initialNoPassDelaySeconds))
  const [configResolved, setConfigResolved] = useState(false)
  const [notice, setNotice] = useState('')
  const [saveIssue, setSaveIssue] = useState(scenario === 'save-error' ? '保存失败，请重试；当前输入仍保留。' : '')
  const [keyword, setKeyword] = useState('')
  const [appliedKeyword, setAppliedKeyword] = useState('')
  const [tier, setTier] = useState('ALL')
  const readOnly = scenario === 'readonly'
  const saving = scenario === 'saving'
  const loading = scenario === 'loading' && !configResolved
  const loadError = scenario === 'error' && !configResolved
  const configUnavailable = loading || loadError

  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    const query = appliedKeyword.trim().toLocaleLowerCase()
    return adminPassPurchases.filter(item => {
      const matchesTier = tier === 'ALL' || item.tier === tier
      const matchesKeyword = !query || [item.accountNo, item.nickname, item.username, item.txNo].some(value => value.toLocaleLowerCase().includes(query))
      return matchesTier && matchesKeyword
    })
  }, [appliedKeyword, scenario, tier])

  function updateProduct(tierValue: AdminPassTier, field: 'dailyPrice' | 'delaySeconds', value: string) {
    setDraft(items => items.map(item => item.tier === tierValue ? { ...item, [field]: value } : item))
    setNotice('')
    setSaveIssue('')
  }

  function saveConfig() {
    if (readOnly || saving || configUnavailable) return
    if (draft.some(item => !validPrice(item.dailyPrice))) {
      setSaveIssue('每日价格须为至少 1 浪花的整数')
      return
    }
    if (draft.some(item => !validDelay(item.delaySeconds)) || !validDelay(noPassDelay)) {
      setSaveIssue('可见延迟须为 0 至 86400 秒的整数')
      return
    }
    const delays = [...draft.map(item => Number(item.delaySeconds)), Number(noPassDelay)]
    if (delays.some((value, index) => index > 0 && value < delays[index - 1])) {
      setSaveIssue('可见延迟应按黑卡、金卡、银卡、铜卡、无卡依次递增或相同')
      return
    }
    setSavedDelays(draft.map(item => Number(item.delaySeconds)))
    setSaveIssue('')
    setNotice('通行证规则已在原型中更新，未修改真实价格或接单配置。')
  }

  function clearFilters() {
    setKeyword('')
    setAppliedKeyword('')
    setTier('ALL')
    if (scenario === 'empty') onScenarioChange('default')
  }

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm">
      <div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
        <div>
          <div className="flex items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">通行证</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">示例规则</Badge></div>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">配置陪玩按日购买的接单通行证，核对公共订单可见优先级与钱包扣款记录。</p>
        </div>
        <dl className="grid grid-cols-3 gap-5">
          <div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">4</dd><dt className="mt-1 text-[11px] text-blue-100/65">固定档位</dt></div>
          <div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{Math.min(...savedDelays)}<span className="ml-1 text-xs font-normal">秒</span></dd><dt className="mt-1 text-[11px] text-blue-100/65">最快可见</dt></div>
          <div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{adminPassPurchases.length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">购买记录</dt></div>
        </dl>
      </div>
    </section>

    <Alert><Info className="size-4" /><AlertTitle>价格与可见延迟的生效范围不同</AlertTitle><AlertDescription>价格调整仅适用于后续购买，已购有效期和实付记录保留；可见延迟调整立即影响所有陪玩的公共单池。</AlertDescription></Alert>

    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0">
        <div><CardTitle className="flex items-center gap-2 text-base"><ShieldCheck className="size-4 text-primary" />价格与接单优先级</CardTitle><CardDescription className="mt-1">陪玩使用钱包按日购买，同档续费从原到期时间顺延 1 天</CardDescription></div>
        <Badge variant="outline" className="gap-1.5">{readOnly ? <Eye className="size-3.5" /> : <BadgeCheck className="size-3.5" />}{readOnly ? '只读权限' : '可配置'}</Badge>
      </CardHeader>
      <CardContent className="space-y-4 border-t pt-5">
        {loadError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>通行证规则加载失败</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">购买记录仍可查询；请重新读取规则后再配置。<Button variant="link" className="h-auto p-0" onClick={() => setConfigResolved(true)}>重新加载</Button></AlertDescription></Alert>}
        {loading && <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4" aria-label="正在读取通行证规则">{[0, 1, 2, 3].map(item => <Skeleton key={item} className="h-52 rounded-xl" />)}</div>}
        {!configUnavailable && <>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {draft.map(item => <section key={item.tier} className="min-w-0 rounded-xl border bg-muted/20 p-4">
              <div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2"><CreditCard className="size-4 text-primary" /><h2 className="text-sm font-medium">{item.name}</h2></div><span className="font-mono text-[10px] text-muted-foreground">{item.tier}</span></div>
              {readOnly ? <dl className="mt-4 grid grid-cols-2 gap-3"><div><dt className="text-[11px] text-muted-foreground">每日价格</dt><dd className="mt-1.5 text-lg font-semibold tabular-nums">{formatWaveAmount(item.dailyPrice)}<span className="ml-1 text-xs font-normal">浪花</span></dd></div><div><dt className="text-[11px] text-muted-foreground">可见延迟</dt><dd className="mt-1.5 text-lg font-semibold tabular-nums">{item.delaySeconds}<span className="ml-1 text-xs font-normal">秒</span></dd></div></dl> : <div className="mt-4 space-y-3">
                <div className="space-y-1.5"><Label htmlFor={`pass-price-${item.tier}`} className="text-[11px] text-muted-foreground">每天价格（浪花）</Label><Input id={`pass-price-${item.tier}`} aria-label={`${item.name}每天价格（浪花）`} type="number" min="1" step="1" value={item.dailyPrice} disabled={saving} onChange={event => updateProduct(item.tier, 'dailyPrice', event.target.value)} /></div>
                <div className="space-y-1.5"><Label htmlFor={`pass-delay-${item.tier}`} className="text-[11px] text-muted-foreground">公共订单可见延迟（秒）</Label><Input id={`pass-delay-${item.tier}`} aria-label={`${item.name}公共订单可见延迟（秒）`} type="number" min="0" max="86400" step="1" value={item.delaySeconds} disabled={saving} onChange={event => updateProduct(item.tier, 'delaySeconds', event.target.value)} /></div>
              </div>}
              <p className="mt-3 flex items-center gap-1.5 text-[11px] text-muted-foreground"><Clock3 className="size-3.5" />{Number(item.delaySeconds) === 0 ? '新单发布后立即可见' : `新单发布 ${item.delaySeconds || '—'} 秒后可见`}</p>
            </section>)}
          </div>
          <div className="flex flex-wrap items-end justify-between gap-4 border-t pt-4">
            <div className="min-w-64 space-y-1.5"><Label htmlFor="pass-delay-none" className="text-xs">无通行证公共订单可见延迟（秒）</Label>{readOnly ? <p className="flex h-10 items-center text-sm"><strong className="tabular-nums">{noPassDelay}</strong><span className="ml-1 text-muted-foreground">秒</span></p> : <Input id="pass-delay-none" aria-label="无通行证公共订单可见延迟（秒）" className="max-w-72" type="number" min="0" max="86400" step="1" value={noPassDelay} disabled={saving} onChange={event => { setNoPassDelay(event.target.value); setNotice(''); setSaveIssue('') }} />}<p className="flex items-center gap-1.5 text-[11px] text-muted-foreground"><TimerReset className="size-3.5" />不得早于铜卡，允许与上一档相同</p></div>
            {!readOnly && <Button disabled={saving || configUnavailable} onClick={saveConfig}><Save className="size-4" />{saving ? '保存中…' : '保存规则'}</Button>}
          </div>
        </>}
        {saveIssue && <Alert variant="destructive" role="alert"><CircleAlert className="size-4" /><AlertDescription>{saveIssue}</AlertDescription></Alert>}
        {notice && <Alert role="status" className="border-success/30 bg-success/10"><BadgeCheck className="size-4 text-success" /><AlertDescription>{notice}</AlertDescription></Alert>}
      </CardContent>
    </Card>

    <Card className="min-w-0 overflow-hidden">
      <CardHeader><CardTitle className="flex items-center gap-2 text-base"><ReceiptText className="size-4 text-primary" />购买记录</CardTitle><CardDescription className="mt-1">查询陪玩购买、同档续费及对应的钱包扣款流水；实付金额是购买时的快照</CardDescription></CardHeader>
      <div data-admin-filter className="flex flex-wrap items-end gap-3 border-t px-4 py-4">
        <div className="min-w-56 flex-1 space-y-1.5 md:max-w-md"><Label htmlFor="pass-keyword" className="text-xs">搜索购买记录</Label><Input id="pass-keyword" value={keyword} placeholder="陪玩编号 / 昵称 / 账号 / 流水号" onChange={event => setKeyword(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') setAppliedKeyword(keyword) }} /></div>
        <div className="space-y-1.5"><Label className="text-xs">通行证档位</Label><Select value={tier} onValueChange={setTier}><SelectTrigger className="w-40" aria-label="通行证档位"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部通行证</SelectItem>{initialAdminPassProducts.map(item => <SelectItem key={item.tier} value={item.tier}>{item.name}</SelectItem>)}</SelectContent></Select></div>
        <Button variant="outline" className="whitespace-nowrap" onClick={() => setAppliedKeyword(keyword)}><Search className="size-4" />查询购买记录</Button>
      </div>
      <div className="overflow-x-auto border-t"><Table className="min-w-[1080px]"><TableHeader><TableRow><TableHead>陪玩</TableHead><TableHead>通行证</TableHead><TableHead>实付</TableHead><TableHead>生效时间</TableHead><TableHead>到期时间</TableHead><TableHead>扣款流水</TableHead><TableHead>购买时间</TableHead></TableRow></TableHeader><TableBody>{rows.map(row => <TableRow key={row.id}>
        <TableCell><div className="flex items-center gap-3"><span className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary"><WalletCards className="size-4" /></span><div><p className="text-sm font-medium">{row.nickname}</p><p className="mt-1 font-mono text-[11px] text-muted-foreground">{row.accountNo} · {row.username}</p></div></div></TableCell>
        <TableCell><Badge variant="outline">{adminPassTierLabel[row.tier]}</Badge><p className="mt-1.5 text-[11px] text-muted-foreground">有效期 {row.days} 天</p></TableCell>
        <TableCell><strong className="text-sm tabular-nums">{formatWaveAmount(row.paidAmount)}<span className="ml-1 text-xs font-normal">浪花</span></strong></TableCell>
        <TableCell className="text-xs tabular-nums">{row.startsAt}</TableCell><TableCell className="text-xs tabular-nums">{row.expiresAt}</TableCell>
        <TableCell><span className="font-mono text-[11px]">{row.txNo}</span></TableCell><TableCell className="text-xs tabular-nums">{row.createdAt}</TableCell>
      </TableRow>)}</TableBody></Table>{rows.length === 0 && <div className="flex min-h-56 flex-col items-center justify-center p-8 text-center"><ReceiptText className="size-7 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有匹配的购买记录</h2><p className="mt-2 text-sm text-muted-foreground">调整陪玩或流水号关键词，或查看全部通行证。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除筛选</Button></div>}</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>共 {rows.length} 条明确示例记录，不连接真实钱包或订单。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>
  </div>
}
