import { useMemo, useState } from 'react'
import {
  ArrowDownLeft,
  ArrowUpRight,
  BadgeCheck,
  BookOpenText,
  CheckCircle2,
  CircleAlert,
  Coins,
  FileImage,
  Landmark,
  LockKeyhole,
  Plus,
  ReceiptText,
  RotateCcw,
  Search,
  ShieldAlert,
  SlidersHorizontal,
  Upload,
  WalletCards,
  X,
} from 'lucide-react'
import type { AdminWalletsScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { initialAdminWallets, initialPendingOrders, initialPlatformFunds, initialSubsidyRecords, transactionTypeLabel, type AdminRechargeRecord, type AdminSubsidyRecord, type AdminWallet, type AdminWalletKind } from '@/pages/admin/admin-wallet-data'
import { cn } from '@/lib/utils'
import { formatWaveAmount } from '@/lib/format'

interface AdminWalletPageProps {
  kind: AdminWalletKind
  scenario: AdminWalletsScenario
  onScenarioChange: (scenario: AdminWalletsScenario) => void
  onOpenTransactions: (walletId: number) => void
  onOpenRecharges: (walletId: number) => void
}

type LedgerView = { type: 'transactions' | 'recharges'; wallet: AdminWallet } | { type: 'subsidy' | 'pending'; wallet?: never }
type FundDialog = { wallet: AdminWallet; stage: 'form' | 'confirm' }
const emptyFilters = { keyword: '', walletNo: '', nickname: '', phone: '' }
const rechargePresets = [{ paid: '100', gift: '10' }, { paid: '300', gift: '30' }, { paid: '500', gift: '60' }, { paid: '1000', gift: '150' }]

function coins(amount: number) {
  return formatWaveAmount(amount / 10)
}

function rmb(amount: number) {
  return `¥${(amount / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function signedCoins(amount: number) {
  return `${amount > 0 ? '+' : ''}${coins(amount)}`
}

export function AdminWalletPage({ kind, scenario, onScenarioChange, onOpenTransactions, onOpenRecharges }: AdminWalletPageProps) {
  const [wallets, setWallets] = useState<AdminWallet[]>(initialAdminWallets)
  const [filters, setFilters] = useState(emptyFilters)
  const [applied, setApplied] = useState(emptyFilters)
  const [advanced, setAdvanced] = useState(false)
  const [ledger, setLedger] = useState<LedgerView | null>(null)
  const [pendingPage, setPendingPage] = useState(1)
  const [adjust, setAdjust] = useState<FundDialog | null>(null)
  const [adjustAmount, setAdjustAmount] = useState('100')
  const [adjustReason, setAdjustReason] = useState('')
  const [adjustError, setAdjustError] = useState('')
  const [recharge, setRecharge] = useState<FundDialog | null>(null)
  const [paidAmount, setPaidAmount] = useState('300')
  const [giftAmount, setGiftAmount] = useState('30')
  const [rechargeReference, setRechargeReference] = useState('')
  const [voucher, setVoucher] = useState<{ name: string; url: string } | null>(null)
  const [rechargeError, setRechargeError] = useState('')
  const [platformFunds, setPlatformFunds] = useState(initialPlatformFunds)
  const [subsidyRecords, setSubsidyRecords] = useState<AdminSubsidyRecord[]>(initialSubsidyRecords)
  const [platformOpen, setPlatformOpen] = useState(false)
  const [platformStage, setPlatformStage] = useState<'form' | 'confirm'>('form')
  const [platformAmount, setPlatformAmount] = useState('1000')
  const [platformReference, setPlatformReference] = useState('')
  const [platformError, setPlatformError] = useState('')
  const [reviewTransaction, setReviewTransaction] = useState(false)
  const [reviewRecharge, setReviewRecharge] = useState(false)
  const [feedback, setFeedback] = useState<{ text: string; tone: 'success' | 'warning' } | null>(null)

  const isBoss = kind === 'BOSS'
  const pageLabel = isBoss ? '老板钱包' : '陪玩钱包'
  const canFund = scenario !== 'readonly'
  const platformEnabled = scenario !== 'platform-disabled'
  const fundsLocked = scenario === 'review' || scenario === 'refresh-error'
  const advancedCount = [applied.walletNo, applied.nickname, applied.phone].filter(Boolean).length
  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    const match = (value: string, query: string) => value.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())
    return wallets.filter(row => row.kind === kind &&
      (!applied.keyword || [row.walletNo, row.username, row.nickname, row.phone].some(value => match(value, applied.keyword))) &&
      (!applied.walletNo || match(row.walletNo, applied.walletNo)) &&
      (!applied.nickname || match(row.nickname, applied.nickname)) &&
      (!applied.phone || match(row.phone, applied.phone)),
    )
  }, [applied, kind, scenario, wallets])
  const kindWallets = wallets.filter(row => row.kind === kind)
  const totals = kindWallets.reduce((sum, row) => ({ balance: sum.balance + row.balance, frozen: sum.frozen + row.frozen, recharge: sum.recharge + row.totalRecharge, gift: sum.gift + row.totalGift }), { balance: 0, frozen: 0, recharge: 0, gift: 0 })
  const reviewWallet = wallets.find(row => row.id === (isBoss ? 1001 : 2001)) || kindWallets[0] || wallets[0]
  const currentAdjustBalance = adjust ? wallets.find(row => row.id === adjust.wallet.id)?.balance ?? adjust.wallet.balance : 0
  const currentRechargeWallet = recharge ? wallets.find(row => row.id === recharge.wallet.id) ?? recharge.wallet : null
  const expectedCredit = /^\d+(\.\d{1,2})?$/.test(paidAmount) && /^\d+$/.test(giftAmount) ? Math.round(Number(paidAmount) * 100) + Math.round(Number(giftAmount) * 10) : 0
  const pendingPages = Math.max(1, Math.ceil(initialPendingOrders.length / 10))
  const pendingOrders = initialPendingOrders.slice((pendingPage - 1) * 10, pendingPage * 10)
  const pendingStatusLabel = { COMPLETED: '已完成待结算', IN_SERVICE: '转单待结算', CANCELLED: '协商终止待结算' } as const

  function notify(text: string, tone: 'success' | 'warning' = 'success') {
    setFeedback({ text, tone })
  }

  function openLedger(type: 'transactions' | 'recharges', wallet: AdminWallet) {
    if (scenario === 'review' && wallet.id === reviewWallet.id) {
      if (type === 'transactions') setReviewTransaction(true)
      else setReviewRecharge(true)
    }
    setLedger({ type, wallet })
  }

  function openAdjust(wallet: AdminWallet) {
    if (!canFund || fundsLocked || !wallet.enabled || wallet.kind !== 'BOSS') return
    setAdjust({ wallet, stage: 'form' })
    setAdjustAmount('100')
    setAdjustReason('')
    setAdjustError('')
  }

  function validateAdjust() {
    if (!adjust) return
    if (!/^-?\d+$/.test(adjustAmount) || !Number.isSafeInteger(Number(adjustAmount)) || Number(adjustAmount) === 0) { setAdjustError('请输入非零整数浪花；正数增加，负数扣减。'); return }
    if (currentAdjustBalance + Math.round(Number(adjustAmount) * 10) < 0) { setAdjustError(`扣减后余额不能为负，当前可用余额为 ${coins(currentAdjustBalance)} 浪花。`); return }
    if (!adjustReason.trim()) { setAdjustError('请填写调账原因，便于后续审计核对。'); return }
    setAdjustError('')
    setAdjust({ ...adjust, stage: 'confirm' })
  }

  function submitAdjust() {
    if (!adjust || fundsLocked) return
    if (scenario === 'read-error') { setAdjustError('钱包最新状态读取失败，请重试；本次未变更余额或流水。'); return }
    if (scenario === 'conflict') { setAdjustError('钱包余额已被其他操作更新，请重新读取；本次未执行调账。'); return }
    const amount = Math.round(Number(adjustAmount) * 10)
    const nextBalance = currentAdjustBalance + amount
    setWallets(items => items.map(row => row.id === adjust.wallet.id ? { ...row, balance: nextBalance, updatedAt: '刚刚 · 示例', transactions: [{ id: `${row.walletNo}-ADJ-${row.transactions.length + 1}`, type: 'ADJUST', amount, balanceAfter: nextBalance, remark: adjustReason.trim(), reference: `DEMO-ADJ-${row.id}-${row.transactions.length + 1}`, operator: '运营管理员', createdAt: '刚刚 · 示例' }, ...row.transactions] } : row))
    notify(`已在原型中完成 ${adjust.wallet.nickname} 的钱包调账，余额 ${coins(nextBalance)} 浪花。`)
    setAdjust(null)
  }

  function openRecharge(wallet: AdminWallet) {
    if (!canFund || fundsLocked || !wallet.enabled) return
    setRecharge({ wallet, stage: 'form' })
    setPaidAmount('300')
    setGiftAmount('30')
    setRechargeReference('')
    setVoucher(null)
    setRechargeError('')
  }

  function validateRecharge() {
    if (!recharge) return
    if (!/^\d+(\.\d{1,2})?$/.test(paidAmount) || Number(paidAmount) <= 0) { setRechargeError('实收人民币必须大于 0，最多 2 位小数。'); return }
    if (!/^\d+$/.test(giftAmount) || !Number.isSafeInteger(Number(giftAmount))) { setRechargeError('赠送浪花须为非负整数。'); return }
    if (!rechargeReference.trim() || rechargeReference.trim().length > 64) { setRechargeError('请填写 1–64 字的收款流水号。'); return }
    if (wallets.some(row => row.recharges.some(item => item.reference === rechargeReference.trim()))) { setRechargeError('收款流水号已存在，请核对后更换，避免重复入账。'); return }
    if (!voucher) { setRechargeError('请上传收款凭证图片后再继续。'); return }
    setRechargeError('')
    setRecharge({ ...recharge, stage: 'confirm' })
  }

  function submitRecharge() {
    if (!recharge || !currentRechargeWallet || !voucher || fundsLocked) return
    if (scenario === 'read-error') { setRechargeError('钱包最新状态读取失败，请重试；本次未入账或修改余额。'); return }
    if (scenario === 'conflict') { setRechargeError('钱包余额或收款流水号发生冲突，请重新核对；本次未入账。'); return }
    const paid = Math.round(Number(paidAmount) * 100)
    const gift = Math.round(Number(giftAmount) * 10)
    const nextBalance = currentRechargeWallet.balance + paid + gift
    const reference = rechargeReference.trim()
    const record: AdminRechargeRecord = { id: `${recharge.wallet.walletNo}-R${currentRechargeWallet.recharges.length + 1}`, paidAmount: paid, giftAmount: gift, creditedAmount: paid + gift, reference, voucherName: voucher.name, operator: '运营管理员', createdAt: '刚刚 · 示例' }
    setWallets(items => items.map(row => {
      if (row.id !== recharge.wallet.id) return row
      const giftTransactions = gift > 0 ? [{ id: `${row.walletNo}-G${row.transactions.length + 1}`, type: 'GIFT' as const, amount: gift, balanceAfter: nextBalance, remark: '客服充值赠送', reference, operator: '运营管理员', createdAt: '刚刚 · 示例' }] : []
      return { ...row, balance: nextBalance, totalRecharge: row.totalRecharge + paid, totalGift: row.totalGift + gift, updatedAt: '刚刚 · 示例', recharges: [record, ...row.recharges], transactions: [...giftTransactions, { id: `${row.walletNo}-P${row.transactions.length + 1}`, type: 'RECHARGE' as const, amount: paid, balanceAfter: row.balance + paid, remark: '线下收款后客服实充', reference, operator: '运营管理员', createdAt: '刚刚 · 示例' }, ...row.transactions] }
    }))
    notify(`已在原型中入账 ${rmb(paid)}，赠送 ${coins(gift)} 浪花，到账 ${coins(paid + gift)} 浪花。`)
    setRecharge(null)
  }

  function chooseVoucher(file?: File) {
    if (!file) return
    if (!file.type.startsWith('image/')) { setRechargeError('收款凭证须为图片文件。'); return }
    const reader = new FileReader()
    reader.onload = () => { setVoucher({ name: file.name, url: String(reader.result) }); setRechargeError('') }
    reader.readAsDataURL(file)
  }

  function openPlatform() {
    if (!canFund || !platformEnabled || fundsLocked) return
    setPlatformOpen(true)
    setPlatformStage('form')
    setPlatformAmount('1000')
    setPlatformReference('')
    setPlatformError('')
  }

  function validatePlatform() {
    if (!/^\d+$/.test(platformAmount) || !Number.isSafeInteger(Number(platformAmount)) || Number(platformAmount) <= 0) { setPlatformError('线下补入浪花必须为正整数。'); return }
    if (!platformReference.trim() || platformReference.trim().length > 64) { setPlatformError('请填写 1–64 字的线下凭据编号。'); return }
    if (subsidyRecords.some(item => item.reference === platformReference.trim())) { setPlatformError('线下凭据编号已登记，请勿重复补入。'); return }
    setPlatformError('')
    setPlatformStage('confirm')
  }

  function submitPlatform() {
    const amount = Math.round(Number(platformAmount) * 10)
    if (!amount || fundsLocked) return
    setPlatformFunds(value => ({ ...value, balance: value.balance + amount, subsidyAvailable: value.subsidyAvailable + amount }))
    setSubsidyRecords(items => [{ id: `SUB-DEMO-${items.length + 1}`, amount, reference: platformReference.trim(), remark: '平台线下补入', operator: '运营管理员', createdAt: '刚刚 · 示例' }, ...items])
    setPlatformOpen(false)
    notify(`已在原型中补入 ${coins(amount)} 浪花，陪玩押金未计入可用补贴。`)
  }

  function clearFilters() {
    setFilters(emptyFilters)
    setApplied(emptyFilters)
    if (scenario === 'empty') onScenarioChange('default')
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label={`${pageLabel}加载中`}><Skeleton className="h-32 rounded-xl" /><div className="grid gap-4 md:grid-cols-4"><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-28 rounded-xl" /></div><Skeleton className="h-[500px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">{pageLabel}加载失败</CardTitle><CardDescription className="mt-2">请检查网络或钱包查看权限后重试，当前没有展示推测余额。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4" data-wallet-kind={kind.toLowerCase()}>
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm"><div className="grid gap-6 p-5 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-end md:p-6">
      <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">{pageLabel}</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">独立资金域 · 示例</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">{isBoss ? '仅核对本店老板账户的余额、冻结与累计入账，不展示陪玩账户或押金。' : '仅核对本店陪玩账户、押金与待结算资金，不展示老板账户余额。'}1 元 = 10 浪花；所有操作只更新本地原型。</p>{!isBoss && <div className="mt-5 flex flex-wrap gap-2"><Button variant="outline" className="border-white/25 bg-transparent text-white hover:bg-white/10 hover:text-white" onClick={() => setLedger({ type: 'subsidy' })}><BookOpenText className="size-4" />补贴流水</Button>{canFund && <Button variant="outline" disabled={!platformEnabled || fundsLocked} className="border-white/25 bg-transparent text-white hover:bg-white/10 hover:text-white" onClick={openPlatform}><Plus className="size-4" />平台线下补入</Button>}</div>}</div>
      {isBoss ? <div className="max-w-sm rounded-xl border border-white/15 bg-white/5 p-4"><p className="text-sm font-semibold">老板账户独立核算</p><p className="mt-2 text-xs leading-5 text-blue-100/70">本页 {kindWallets.length} 个老板钱包。充值、调账和流水只作用于所选老板账户，不与陪玩资金混用。</p></div> : <dl className="grid grid-cols-2 gap-x-7 gap-y-5 xl:min-w-[430px]"><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{coins(platformFunds.subsidyAvailable)}</dd><dt className="mt-1 text-[11px] text-blue-100/65">可用于补贴 · 浪花</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{coins(platformFunds.balance)}</dd><dt className="mt-1 text-[11px] text-blue-100/65">平台资金余额 · 浪花</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-lg font-semibold tabular-nums">{coins(platformFunds.providerDeposits)}</dd><dt className="mt-1 text-[11px] text-blue-100/65">陪玩押金 · 不可补贴</dt></div><div className="border-l border-white/15 pl-4"><dd><Button variant="link" className="h-auto p-0 text-lg font-semibold tabular-nums text-white hover:text-blue-100" onClick={() => { setPendingPage(1); setLedger({ type: 'pending' }) }}>{coins(platformFunds.unsettled)} <span className="text-xs font-normal text-blue-100/65">/ {platformFunds.unsettledOrders} 单</span></Button></dd><dt className="mt-1 text-[11px] text-blue-100/65">待结算金额 · 浪花 · 查看订单</dt></div></dl>}
    </div></section>

    <Alert><ShieldAlert className="size-4" /><AlertTitle>{isBoss ? '老板钱包独立核算' : '陪玩钱包与平台资金分口径展示'}</AlertTitle><AlertDescription>{isBoss ? '本页只显示老板账户余额、冻结、充值与调账；陪玩账户和平台押金不在此页展示。' : '本页只显示陪玩账户；可用于补贴金额不包含陪玩押金，待结算金额与订单单独核对。'}</AlertDescription></Alert>
    {scenario === 'readonly' && <Alert><LockKeyhole className="size-4" /><AlertTitle>当前为钱包只读权限</AlertTitle><AlertDescription>可查看当前资金域的钱包、流水与充值记录；资金写操作需要独立权限。</AlertDescription></Alert>}
    {!isBoss && !platformEnabled && <Alert><CircleAlert className="size-4" /><AlertTitle>平台补贴功能未开放</AlertTitle><AlertDescription>当前 VIP_SAFE_BOX_SUBSIDY_ENABLED 未启用，仅保留资金快照与补贴流水查看；陪玩钱包其他操作不受影响。</AlertDescription></Alert>}
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>快照更新失败，当前显示上次成功读取的数据</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">为避免基于旧余额操作，资金功能已暂时锁定。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新读取</Button></AlertDescription></Alert>}
    {scenario === 'review' && <Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>充值结果待核对，全部资金操作已锁定</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">钱包 {reviewWallet.walletNo} 的提交结果未知，请勿重复充值。先核对流水与充值记录。<Button variant="link" className="h-auto p-0" onClick={() => openLedger('transactions', reviewWallet)}>{reviewTransaction ? '流水已核对' : '核对流水'}</Button><Button variant="link" className="h-auto p-0" onClick={() => openLedger('recharges', reviewWallet)}>{reviewRecharge ? '充值记录已核对' : '核对充值记录'}</Button><Button variant="link" className="h-auto p-0" disabled={!reviewTransaction || !reviewRecharge} onClick={() => { onScenarioChange('default'); notify('已核对最新流水与充值记录，资金操作限制已解除。') }}>完成核对并解除限制</Button></AlertDescription></Alert>}

    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[
      { label: `${kind === 'BOSS' ? '老板' : '陪玩'}钱包余额`, value: coins(totals.balance), unit: '浪花', note: `${kindWallets.length} 个钱包 · 可用余额，不含冻结`, icon: WalletCards },
      { label: '冻结金额', value: coins(totals.frozen), unit: '浪花', note: '订单或提现处理中', icon: LockKeyhole },
      { label: '累计实充', value: coins(totals.recharge), unit: '浪花', note: '仅客服实充，不含赠送', icon: Landmark },
      { label: '累计赠送', value: coins(totals.gift), unit: '浪花', note: '与实充分开记账', icon: Coins },
    ].map(item => <Card key={item.label} className="gap-0 py-4"><CardContent className="flex items-start justify-between gap-3"><div><p className="text-xs text-muted-foreground">{item.label}</p><p className="mt-2 text-2xl font-semibold tracking-tight tabular-nums">{item.value} <span className="text-xs font-normal text-muted-foreground">{item.unit}</span></p><p className="mt-2 text-[11px] text-muted-foreground">{item.note}</p></div><span className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary"><item.icon className="size-4" /></span></CardContent></Card>)}</section>

    <Card className="min-w-0 overflow-hidden"><CardHeader className="gap-4"><div><CardTitle className="flex items-center gap-2 text-base"><WalletCards className="size-4 text-primary" />{pageLabel}账户列表</CardTitle><CardDescription className="mt-1">本页 {rows.length} 项 · 仅展示当前资金域账户</CardDescription></div>
      <form data-admin-filter className="grid items-end gap-3 md:grid-cols-[minmax(240px,1fr)_auto]" onSubmit={event => { event.preventDefault(); setApplied(filters) }}>
        <div className="space-y-2"><Label htmlFor="wallet-keyword">搜索钱包</Label><div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input id="wallet-keyword" value={filters.keyword} onChange={event => setFilters(value => ({ ...value, keyword: event.target.value }))} placeholder="编号、账号、昵称或手机号" className="h-10 pl-9" /></div></div>
        <div className="flex items-center gap-2"><Button type="submit" variant="outline" className="h-10 min-w-20 whitespace-nowrap"><Search className="size-4" />查询</Button><Button type="button" variant="outline" className="h-10 whitespace-nowrap" aria-expanded={advanced} onClick={() => setAdvanced(value => !value)}><SlidersHorizontal className="size-4" />高级筛选{advancedCount > 0 && `（${advancedCount}）`}</Button></div>
        {advanced && <div data-admin-filter-advanced className="grid gap-3 border-t pt-4 md:col-span-2 md:grid-cols-3"><div className="space-y-2"><Label htmlFor="wallet-number">钱包编号</Label><Input id="wallet-number" value={filters.walletNo} onChange={event => setFilters(value => ({ ...value, walletNo: event.target.value }))} placeholder="W-Boss-1001" className="h-10" /></div><div className="space-y-2"><Label htmlFor="wallet-nickname">昵称</Label><Input id="wallet-nickname" value={filters.nickname} onChange={event => setFilters(value => ({ ...value, nickname: event.target.value }))} placeholder="按昵称精确筛选" className="h-10" /></div><div className="space-y-2"><Label htmlFor="wallet-phone">手机号</Label><Input id="wallet-phone" value={filters.phone} onChange={event => setFilters(value => ({ ...value, phone: event.target.value }))} placeholder="按手机号筛选" className="h-10" /></div></div>}
      </form></CardHeader>
      <div className="overflow-x-auto border-t"><Table className="min-w-[1180px]"><TableHeader><TableRow><TableHead>账户 / 编号</TableHead><TableHead>账号信息</TableHead>{kind === 'BOSS' && <TableHead>老板分级</TableHead>}<TableHead className="text-right">余额</TableHead><TableHead className="text-right">冻结</TableHead><TableHead className="text-right">累计实充</TableHead><TableHead className="text-right">累计赠送</TableHead><TableHead>状态</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader><TableBody>{rows.map(row => <TableRow key={row.id}>
        <TableCell><p className="text-sm font-medium">{row.nickname}</p><p className="mt-1 font-mono text-[10px] text-muted-foreground">{row.walletNo}</p></TableCell>
        <TableCell><p className="text-xs font-medium">{row.username}</p><p className="mt-1 text-[11px] text-muted-foreground">{row.phone}</p></TableCell>
        {kind === 'BOSS' && <TableCell><Badge variant="outline" className="border-primary/20 bg-primary/5 font-normal text-primary"><BadgeCheck className="size-3" />{row.bossLevel}</Badge></TableCell>}
        <TableCell className="text-right"><strong className="text-sm font-semibold tabular-nums">{coins(row.balance)}</strong><span className="text-[11px] text-muted-foreground"> 浪花</span></TableCell>
        <TableCell className="text-right text-sm tabular-nums">{coins(row.frozen)}</TableCell>
        <TableCell className="text-right text-sm tabular-nums">{coins(row.totalRecharge)} 浪花</TableCell>
        <TableCell className="text-right text-sm tabular-nums">{coins(row.totalGift)} 浪花</TableCell>
        <TableCell><Badge variant="outline" className={row.enabled ? 'border-success/30 bg-success/10 text-success' : 'border-muted bg-muted text-muted-foreground'}>{row.enabled ? '已启用' : '已停用'}</Badge><p className="mt-1.5 text-[10px] text-muted-foreground">{row.updatedAt}</p></TableCell>
        <TableCell><div className="flex min-w-64 flex-wrap justify-end gap-1.5"><Button variant="outline" size="sm" onClick={() => scenario === 'review' ? openLedger('transactions', row) : onOpenTransactions(row.id)}>流水</Button><Button variant="outline" size="sm" onClick={() => scenario === 'review' ? openLedger('recharges', row) : onOpenRecharges(row.id)}>充值记录</Button>{canFund && row.kind === 'BOSS' && <Button variant="ghost" size="sm" disabled={fundsLocked || !row.enabled} title={!row.enabled ? '停用钱包不能调账' : undefined} onClick={() => openAdjust(row)}>调账</Button>}{canFund && <Button size="sm" disabled={fundsLocked || !row.enabled} title={!row.enabled ? '停用钱包不能充值' : undefined} onClick={() => openRecharge(row)}>客服充值</Button>}</div></TableCell>
      </TableRow>)}</TableBody></Table>{rows.length === 0 && <div className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><WalletCards className="size-7 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有符合条件的钱包</h2><p className="mt-2 text-sm text-muted-foreground">可清除关键词与高级筛选后重新查询。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除筛选</Button></div>}</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>停用钱包可查看历史记录，但不能调账或充值。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    <Dialog open={!!ledger} onOpenChange={open => { if (!open) setLedger(null) }}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-4xl"><DialogHeader><DialogTitle>{ledger?.type === 'transactions' ? '钱包交易流水' : ledger?.type === 'recharges' ? '客服充值记录' : ledger?.type === 'pending' ? '待结算订单明细' : '平台补贴资金流水'}</DialogTitle><DialogDescription>{ledger?.wallet ? `${ledger.wallet.walletNo} · ${ledger.wallet.nickname} · 示例记录` : ledger?.type === 'pending' ? '已完成、转单与协商终止订单的待付份额 · 示例记录' : '平台线下补入与补贴资金核对 · 示例记录'}</DialogDescription></DialogHeader>{ledger?.type === 'transactions' && <div className="overflow-x-auto rounded-xl border"><Table className="min-w-[760px]"><TableHeader><TableRow><TableHead>时间 / 类型</TableHead><TableHead>说明 / 凭据</TableHead><TableHead className="text-right">变动</TableHead><TableHead className="text-right">变动后余额</TableHead><TableHead>经办</TableHead></TableRow></TableHeader><TableBody>{ledger.wallet.transactions.map(item => <TableRow key={item.id}><TableCell><p className="text-xs font-medium">{transactionTypeLabel[item.type]}</p><p className="mt-1 text-[11px] text-muted-foreground">{item.createdAt}</p></TableCell><TableCell><p className="text-xs">{item.remark}</p><p className="mt-1 break-all font-mono text-[10px] text-muted-foreground">{item.reference}</p></TableCell><TableCell className={cn('text-right text-sm font-semibold tabular-nums', item.amount > 0 ? 'text-success' : 'text-foreground')}>{signedCoins(item.amount)} 浪花</TableCell><TableCell className="text-right text-xs tabular-nums">{coins(item.balanceAfter)} 浪花</TableCell><TableCell className="text-xs">{item.operator}</TableCell></TableRow>)}</TableBody></Table></div>}{ledger?.type === 'recharges' && <div className="overflow-x-auto rounded-xl border"><Table className="min-w-[760px]"><TableHeader><TableRow><TableHead>时间 / 经办</TableHead><TableHead>收款流水号 / 凭证</TableHead><TableHead className="text-right">实收</TableHead><TableHead className="text-right">赠送</TableHead><TableHead className="text-right">合计到账</TableHead></TableRow></TableHeader><TableBody>{ledger.wallet.recharges.map(item => <TableRow key={item.id}><TableCell><p className="text-xs font-medium">{item.operator}</p><p className="mt-1 text-[11px] text-muted-foreground">{item.createdAt}</p></TableCell><TableCell><p className="break-all font-mono text-[11px]">{item.reference}</p><p className="mt-1 flex items-center gap-1 text-[11px] text-muted-foreground"><FileImage className="size-3" />{item.voucherName}</p></TableCell><TableCell className="text-right text-sm tabular-nums">{rmb(item.paidAmount)}</TableCell><TableCell className="text-right text-sm tabular-nums">{coins(item.giftAmount)} 浪花</TableCell><TableCell className="text-right text-sm font-semibold tabular-nums text-success">+{coins(item.creditedAmount)} 浪花</TableCell></TableRow>)}</TableBody></Table></div>}{ledger?.type === 'subsidy' && <><Alert><ShieldAlert className="size-4" /><AlertDescription>当前可用于补贴 {coins(platformFunds.subsidyAvailable)} 浪花；陪玩押金 {coins(platformFunds.providerDeposits)} 浪花独立保管，不用于补贴。</AlertDescription></Alert><div className="overflow-x-auto rounded-xl border"><Table className="min-w-[680px]"><TableHeader><TableRow><TableHead>时间 / 经办</TableHead><TableHead>线下凭据编号</TableHead><TableHead>说明</TableHead><TableHead className="text-right">补入金额</TableHead></TableRow></TableHeader><TableBody>{subsidyRecords.map(item => <TableRow key={item.id}><TableCell><p className="text-xs">{item.createdAt}</p><p className="mt-1 text-[11px] text-muted-foreground">{item.operator}</p></TableCell><TableCell className="break-all font-mono text-[11px]">{item.reference}</TableCell><TableCell className="text-xs">{item.remark}</TableCell><TableCell className="text-right text-sm font-semibold tabular-nums text-success">+{coins(item.amount)} 浪花</TableCell></TableRow>)}</TableBody></Table></div></>}{ledger?.type === 'pending' && <><div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-muted/40 px-4 py-3 text-xs"><span className="text-muted-foreground">共 {platformFunds.unsettledOrders} 笔订单 · 每页 10 笔</span><strong className="text-sm font-semibold tabular-nums">待结算 {coins(platformFunds.unsettled)} 浪花</strong></div><div className="overflow-x-auto rounded-xl border"><Table className="min-w-[720px]"><TableHeader><TableRow><TableHead>订单编号</TableHead><TableHead>服务</TableHead><TableHead>结算状态</TableHead><TableHead className="text-right">待付份额</TableHead></TableRow></TableHeader><TableBody>{pendingOrders.map(item => <TableRow key={item.id}><TableCell className="font-mono text-xs">{item.orderNo}</TableCell><TableCell className="text-xs">{item.serviceName}</TableCell><TableCell><Badge variant="outline" className={cn('font-normal', item.status === 'COMPLETED' ? 'border-success/30 bg-success/10 text-success' : item.status === 'IN_SERVICE' ? 'border-primary/20 bg-primary/5 text-primary' : 'border-warning/30 bg-warning/10 text-warning')}>{pendingStatusLabel[item.status]}</Badge></TableCell><TableCell className="text-right text-sm font-semibold tabular-nums">{coins(item.amount)} 浪花</TableCell></TableRow>)}</TableBody></Table></div><div className="flex items-center justify-between gap-3"><p className="text-xs text-muted-foreground">部分订单仍需审核或等待售后期，金额不计入已结算流水。</p><div className="flex shrink-0 items-center gap-2"><Button variant="outline" size="sm" disabled={pendingPage === 1} onClick={() => setPendingPage(page => page - 1)}>上一页</Button><span className="text-xs text-muted-foreground" aria-live="polite">{pendingPage} / {pendingPages}</span><Button variant="outline" size="sm" disabled={pendingPage === pendingPages} onClick={() => setPendingPage(page => page + 1)}>下一页</Button></div></div></>}<DialogFooter><Button variant="outline" onClick={() => setLedger(null)}>关闭</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={!!adjust} onOpenChange={open => { if (!open) setAdjust(null) }}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-xl"><DialogHeader><DialogTitle>{adjust?.stage === 'confirm' ? '二次确认钱包调账' : '老板钱包调账'}</DialogTitle><DialogDescription>{adjust?.wallet.walletNo} · {adjust?.wallet.nickname} · 当前余额 {coins(currentAdjustBalance)} 浪花</DialogDescription></DialogHeader>{adjust?.stage === 'form' ? <><div className="space-y-2"><Label htmlFor="adjust-amount">调账浪花</Label><Input id="adjust-amount" inputMode="numeric" value={adjustAmount} onChange={event => setAdjustAmount(event.target.value)} placeholder="正数增加，负数扣减" /><p className="text-[11px] text-muted-foreground">非零整数。扣减后余额不能为负。</p></div><div className="space-y-2"><Label htmlFor="adjust-reason">调账原因</Label><Textarea id="adjust-reason" rows={3} maxLength={255} value={adjustReason} onChange={event => setAdjustReason(event.target.value)} placeholder="说明业务依据与核对情况" /></div></> : <section className="rounded-xl border bg-primary/5 p-4"><p className="text-xs text-muted-foreground">确认{Number(adjustAmount) > 0 ? '增加' : '扣减'}钱包余额</p><p className="mt-2 flex items-center gap-2 text-2xl font-semibold tabular-nums">{Number(adjustAmount) > 0 ? <ArrowUpRight className="size-6 text-success" /> : <ArrowDownLeft className="size-6 text-destructive" />}{signedCoins(Math.round(Number(adjustAmount) * 10))} 浪花</p><dl className="mt-4 space-y-2 border-t pt-3 text-xs"><div className="flex justify-between gap-4"><dt className="text-muted-foreground">调账后余额</dt><dd className="font-semibold">{coins(currentAdjustBalance + Math.round(Number(adjustAmount) * 10))} 浪花</dd></div><div className="flex justify-between gap-4"><dt className="text-muted-foreground">原因</dt><dd className="text-right">{adjustReason}</dd></div></dl></section>}{adjustError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{adjustError}</AlertDescription></Alert>}<Alert><ShieldAlert className="size-4" /><AlertDescription>原型只新增示例调账流水，不修改真实余额，也不发送通知。</AlertDescription></Alert><DialogFooter>{adjust?.stage === 'confirm' && <Button variant="outline" onClick={() => adjust && setAdjust({ ...adjust, stage: 'form' })}>返回修改</Button>}<Button variant="outline" onClick={() => setAdjust(null)}>取消</Button>{adjust?.stage === 'form' ? <Button onClick={validateAdjust}>核对调账</Button> : <Button onClick={submitAdjust}>确认调账</Button>}</DialogFooter></DialogContent></Dialog>

    <Dialog open={!!recharge} onOpenChange={open => { if (!open) setRecharge(null) }}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>{recharge?.stage === 'confirm' ? '二次确认客服充值' : '客服充值'}</DialogTitle><DialogDescription>{recharge?.wallet.walletNo} · {recharge?.wallet.nickname} · 实充与赠送分别记账</DialogDescription></DialogHeader>{recharge?.stage === 'form' ? <><div className="flex flex-wrap gap-2">{rechargePresets.map(preset => <Button key={preset.paid} type="button" variant="outline" size="sm" onClick={() => { setPaidAmount(preset.paid); setGiftAmount(preset.gift); setRechargeError('') }}>{preset.paid} 元 + {preset.gift} 浪花</Button>)}</div><div className="grid gap-4 sm:grid-cols-2"><div className="space-y-2"><Label htmlFor="recharge-paid">实收人民币（元）</Label><Input id="recharge-paid" inputMode="decimal" value={paidAmount} onChange={event => setPaidAmount(event.target.value)} /><p className="text-[11px] text-muted-foreground">大于 0，最多 2 位小数。</p></div><div className="space-y-2"><Label htmlFor="recharge-gift">赠送浪花</Label><Input id="recharge-gift" inputMode="numeric" value={giftAmount} onChange={event => setGiftAmount(event.target.value)} /><p className="text-[11px] text-muted-foreground">非负整数。</p></div></div><div className="space-y-2"><Label htmlFor="recharge-reference">收款流水号</Label><Input id="recharge-reference" value={rechargeReference} maxLength={64} onChange={event => setRechargeReference(event.target.value)} placeholder="填写实际线下收款流水号" /><p className="text-[11px] text-muted-foreground">必填、全局去重，最多 64 字。</p></div><div className="space-y-2"><Label htmlFor="recharge-voucher">收款凭证图片</Label><label htmlFor="recharge-voucher" className="flex min-h-24 cursor-pointer items-center justify-center gap-3 rounded-xl border border-dashed bg-muted/20 p-4 text-center hover:bg-muted/40"><Upload className="size-5 text-primary" /><span className="text-xs text-muted-foreground">{voucher ? '重新选择本地凭证图片' : '选择本地图片，仅在当前原型预览'}</span></label><Input id="recharge-voucher" type="file" accept="image/*" className="sr-only" onChange={event => chooseVoucher(event.target.files?.[0])} />{voucher && <div className="flex items-center gap-3 rounded-xl border p-3"><img src={voucher.url} alt="本地收款凭证预览" className="size-16 rounded-lg border object-cover" /><div className="min-w-0"><p className="truncate text-xs font-medium">{voucher.name}</p><p className="mt-1 text-[11px] text-success">凭证已就绪 · 未上传外部服务</p></div></div>}</div><section className="rounded-xl bg-primary/5 p-4"><p className="text-xs text-muted-foreground">预计合计到账</p><p className="mt-1 text-2xl font-semibold tabular-nums text-primary">+{coins(expectedCredit)} 浪花</p><p className="mt-1 text-[11px] text-muted-foreground">实充 {coins(/^\d+(\.\d{1,2})?$/.test(paidAmount) ? Math.round(Number(paidAmount) * 100) : 0)} + 赠送 {coins(/^\d+$/.test(giftAmount) ? Math.round(Number(giftAmount) * 10) : 0)}</p></section></> : <><section className="rounded-xl border bg-primary/5 p-4"><p className="text-xs text-muted-foreground">确认向 {currentRechargeWallet?.nickname} 入账</p><p className="mt-2 text-2xl font-semibold tabular-nums">+{coins(expectedCredit)} 浪花</p><p className="mt-2 text-xs text-muted-foreground">当前余额 {coins(currentRechargeWallet?.balance || 0)} → 入账后 {coins((currentRechargeWallet?.balance || 0) + expectedCredit)} 浪花</p></section><dl className="grid gap-3 rounded-xl border p-4 text-xs sm:grid-cols-2"><div><dt className="text-muted-foreground">实际收款</dt><dd className="mt-1 font-medium">{rmb(Math.round(Number(paidAmount) * 100))}</dd></div><div><dt className="text-muted-foreground">赠送</dt><dd className="mt-1 font-medium">{formatWaveAmount(giftAmount)} 浪花</dd></div><div><dt className="text-muted-foreground">收款流水号</dt><dd className="mt-1 break-all font-mono">{rechargeReference}</dd></div><div><dt className="text-muted-foreground">凭证</dt><dd className="mt-1 flex items-center gap-1.5 break-all"><FileImage className="size-3.5 shrink-0" />{voucher?.name}</dd></div></dl></>}{rechargeError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{rechargeError}</AlertDescription></Alert>}<Alert><ShieldAlert className="size-4" /><AlertDescription>请确认已实际收款并核对凭证。原型不连接支付渠道，不上传凭证，不修改真实钱包。</AlertDescription></Alert><DialogFooter>{recharge?.stage === 'confirm' && <Button variant="outline" onClick={() => recharge && setRecharge({ ...recharge, stage: 'form' })}>返回修改</Button>}<Button variant="outline" onClick={() => setRecharge(null)}>取消</Button>{recharge?.stage === 'form' ? <Button onClick={validateRecharge}>核对充值</Button> : <Button onClick={submitRecharge}>确认入账</Button>}</DialogFooter></DialogContent></Dialog>

    <Dialog open={platformOpen} onOpenChange={setPlatformOpen}><DialogContent className="sm:max-w-lg"><DialogHeader><DialogTitle>{platformStage === 'confirm' ? '二次确认平台线下补入' : '平台线下补入'}</DialogTitle><DialogDescription>补入金额进入可用于补贴资金，不占用陪玩押金。</DialogDescription></DialogHeader>{platformStage === 'form' ? <><div className="space-y-2"><Label htmlFor="platform-amount">补入浪花</Label><Input id="platform-amount" inputMode="numeric" value={platformAmount} onChange={event => setPlatformAmount(event.target.value)} /><p className="text-[11px] text-muted-foreground">正整数。</p></div><div className="space-y-2"><Label htmlFor="platform-reference">线下凭据编号</Label><Input id="platform-reference" maxLength={64} value={platformReference} onChange={event => setPlatformReference(event.target.value)} placeholder="填写 1–64 字且未登记的凭据编号" /></div></> : <section className="rounded-xl border bg-primary/5 p-4"><p className="text-xs text-muted-foreground">确认补入平台补贴资金</p><p className="mt-2 text-2xl font-semibold tabular-nums">+{formatWaveAmount(platformAmount)} 浪花</p><p className="mt-3 break-all border-t pt-3 font-mono text-xs text-muted-foreground">{platformReference}</p></section>}{platformError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{platformError}</AlertDescription></Alert>}<Alert><ShieldAlert className="size-4" /><AlertDescription>陪玩押金 {coins(platformFunds.providerDeposits)} 浪花始终保持独立，不计入本次可用补贴。</AlertDescription></Alert><DialogFooter>{platformStage === 'confirm' && <Button variant="outline" onClick={() => setPlatformStage('form')}>返回修改</Button>}<Button variant="outline" onClick={() => setPlatformOpen(false)}>取消</Button>{platformStage === 'form' ? <Button onClick={validatePlatform}>核对补入</Button> : <Button onClick={submitPlatform}><ReceiptText className="size-4" />确认登记</Button>}</DialogFooter></DialogContent></Dialog>

    {feedback && <div role="status" className="fixed bottom-5 right-5 z-50 flex max-w-md items-start gap-3 rounded-xl border bg-card p-4 text-sm shadow-2xl">{feedback.tone === 'success' ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /> : <CircleAlert className="mt-0.5 size-4 shrink-0 text-warning" />}<span className="leading-6">{feedback.text}</span><Button variant="ghost" size="icon" className="-mr-2 -mt-2 size-8" onClick={() => setFeedback(null)} aria-label="关闭反馈"><X className="size-4" /></Button></div>}
  </div>
}
