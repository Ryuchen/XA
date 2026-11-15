import { useMemo, useState, type FormEvent } from 'react'
import {
  BadgeCheck,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Eye,
  EyeOff,
  Filter,
  Plus,
  RotateCcw,
  Search,
  Star,
  UsersRound,
  X,
} from 'lucide-react'
import type { AdminProviderDetailScenario, AdminProvidersScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { adminPassTierLabel, adminProviderKindLabel, adminProviders, adminProviderStatusLabel, type AdminProvider } from '@/pages/admin/admin-provider-data'
import { cn } from '@/lib/utils'
import { formatWaveAmount } from '@/lib/format'

interface AdminProvidersPageProps {
  scenario: AdminProvidersScenario
  createdProviders: AdminProvider[]
  onScenarioChange: (scenario: AdminProvidersScenario) => void
  onProviderCreated: (provider: AdminProvider) => void
  onOpen: (provider: AdminProvider, scenario: AdminProviderDetailScenario) => void
}

function detailScenario(provider: AdminProvider): AdminProviderDetailScenario {
  if (!provider.verified) return 'unverified'
  if (provider.status === 'BUSY') return 'busy'
  return 'default'
}

export function AdminProvidersPage({ scenario, createdProviders, onScenarioChange, onProviderCreated, onOpen }: AdminProvidersPageProps) {
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState(scenario === 'available' ? 'AVAILABLE' : 'ALL')
  const [verified, setVerified] = useState(scenario === 'unverified' ? 'false' : 'ALL')
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [game, setGame] = useState('ALL')
  const [kind, setKind] = useState('ALL')
  const [platform, setPlatform] = useState('ALL')
  const [gender, setGender] = useState('ALL')
  const [passTier, setPassTier] = useState('ALL')
  const [createOpen, setCreateOpen] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [createForm, setCreateForm] = useState({ username: '', password: '', displayName: '', phone: '', gender: 'UNKNOWN' as AdminProvider['gender'], city: '', depositMode: 'CASH' as AdminProvider['depositMode'] })
  const [createError, setCreateError] = useState('')
  const [feedback, setFeedback] = useState('')
  const allProviders = useMemo(() => [...createdProviders, ...adminProviders], [createdProviders])

  const games = useMemo(() => [...new Set(adminProviders.flatMap(provider => provider.games))], [])
  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    return allProviders.filter(provider => {
      if (keyword && !`${provider.displayName} ${provider.username} ${provider.phone}`.toLowerCase().includes(keyword.toLowerCase())) return false
      if (status !== 'ALL' && provider.status !== status) return false
      if (verified !== 'ALL' && String(provider.verified) !== verified) return false
      if (game !== 'ALL' && !provider.games.includes(game)) return false
      if (kind !== 'ALL' && !provider.qualifications.some(item => item.kind === kind && item.active)) return false
      if (platform !== 'ALL' && !provider.qualifications.some(item => item.platform === platform && item.active)) return false
      if (gender !== 'ALL' && provider.gender !== gender) return false
      if (passTier !== 'ALL' && provider.passTier !== passTier) return false
      return true
    })
  }, [allProviders, game, gender, keyword, kind, passTier, platform, scenario, status, verified])

  const filterCount = [game !== 'ALL', kind !== 'ALL', platform !== 'ALL', gender !== 'ALL', passTier !== 'ALL'].filter(Boolean).length

  function search(event: FormEvent) {
    event.preventDefault()
    setKeyword(keywordInput.trim())
  }

  function clearFilters() {
    setKeywordInput(''); setKeyword(''); setStatus('ALL'); setVerified('ALL')
    setGame('ALL'); setKind('ALL'); setPlatform('ALL'); setGender('ALL'); setPassTier('ALL')
    onScenarioChange('default')
  }

  function updateCreateForm(next: Partial<typeof createForm>) {
    setCreateForm(form => ({ ...form, ...next }))
    setCreateError('')
  }

  function createAccount() {
    const username = createForm.username.trim()
    const displayName = createForm.displayName.trim()
    if (!username) { setCreateError('请输入陪玩登录账号'); return }
    if (username.length > 150) { setCreateError('登录账号最多 150 字'); return }
    if (allProviders.some(provider => provider.username.toLowerCase() === username.toLowerCase())) { setCreateError('该登录账号已存在，请更换后重试'); return }
    if (createForm.password.length < 6) { setCreateError('登录密码至少 6 位'); return }
    if (createForm.password.length > 128) { setCreateError('登录密码最多 128 位'); return }
    if (!displayName) { setCreateError('请输入对外展示的陪玩昵称'); return }
    if (displayName.length > 50) { setCreateError('对外昵称最多 50 字'); return }
    if (createForm.phone.trim().length > 20) { setCreateError('手机号最多 20 字'); return }
    if (createForm.city.trim().length > 50) { setCreateError('城市最多 50 字'); return }
    const id = Math.max(...allProviders.map(provider => provider.id)) + 1
    const provider: AdminProvider = {
      id, escortNo: `PW-${String(id).padStart(4, '0')}`, username, displayName,
      phone: createForm.phone.trim() || '未记录', gender: createForm.gender, city: createForm.city.trim() || '未填写',
      serviceArea: '待完善', status: 'OFFLINE', verified: false, passTier: 'NONE', recommendationSlot: null,
      pricePerHour: 0, rating: 0, completedOrders: 0, rewardTotal: 0, penaltyTotal: 0, walletBalance: 0,
      depositMode: createForm.depositMode, depositRequired: 500, depositPaid: 0, activeOrderCount: 0,
      bio: '账号已创建，基础资料与服务资格待补充。', rankTier: '待完善', winRate: 0,
      games: [], serviceItems: [], qualifications: [],
    }
    onProviderCreated(provider)
    setKeywordInput(''); setKeyword(''); setStatus('ALL'); setVerified('ALL')
    setGame('ALL'); setKind('ALL'); setPlatform('ALL'); setGender('ALL'); setPassTier('ALL')
    setCreateError(''); setCreateOpen(false)
    setFeedback(`陪玩账号「${displayName}」已加入本地档案列表，未创建真实账号或发送登录凭证。`)
    setCreateForm({ username: '', password: '', displayName: '', phone: '', gender: 'UNKNOWN', city: '', depositMode: 'CASH' }); setShowPassword(false)
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="陪玩档案加载中"><Skeleton className="h-24 rounded-xl" /><Skeleton className="h-44 rounded-xl" /><Skeleton className="h-[560px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">陪玩档案加载失败</CardTitle><CardDescription className="mt-2">请检查网络和访问权限后重试，当前没有展示推测档案。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1600px] flex-col gap-4">
    <section className="flex flex-wrap items-start gap-3">
      <div className="min-w-[260px] flex-1"><div className="flex items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">陪玩档案</h1><Badge variant="outline" className="border-warning/40 bg-warning/10 text-[11px]">示例快照</Badge></div><p className="mt-1 text-sm text-muted-foreground">管理入驻陪玩的资质、服务范围、状态与业绩。</p></div>
      <Button disabled={scenario === 'refresh-error'} onClick={() => { setCreateError(''); setCreateOpen(true) }}><Plus className="size-4" />创建陪玩账号</Button>
    </section>

    <Alert><BadgeCheck className="size-4" /><AlertTitle>档案状态不等于可接单</AlertTitle><AlertDescription>账号、实名认证、押金、档期、具体服务资格及名额均需核验；列表只用于筛选，操作前请进入档案复核。</AlertDescription></Alert>
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>档案更新失败，当前显示上次成功读取的列表</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">现有筛选已保留，创建账号暂时锁定。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}

    <Card><CardContent className="py-4">
      <form data-admin-filter onSubmit={search} className="grid gap-3 md:grid-cols-2 xl:grid-cols-[minmax(240px,1fr)_160px_160px_auto]">
        <div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="搜索陪玩" value={keywordInput} onChange={event => setKeywordInput(event.target.value)} placeholder="昵称 / 账号 / 手机号" className="h-11 pl-9" /></div>
        <Select value={status} onValueChange={setStatus}><SelectTrigger className="h-11 w-full" aria-label="档案状态"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部状态</SelectItem>{Object.entries(adminProviderStatusLabel).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select>
        <Select value={verified} onValueChange={setVerified}><SelectTrigger className="h-11 w-full" aria-label="认证状态"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部认证</SelectItem><SelectItem value="true">已认证</SelectItem><SelectItem value="false">未认证</SelectItem></SelectContent></Select>
        <div className="flex flex-wrap items-center gap-2 md:col-span-2 xl:col-span-1">
          <Button type="submit" className="h-11 min-w-20 whitespace-nowrap">查询</Button>
          <Button type="button" variant="outline" className="h-11 whitespace-nowrap" aria-expanded={advancedOpen} onClick={() => setAdvancedOpen(value => !value)}><Filter className="size-4" />更多筛选{filterCount > 0 && <Badge className="px-1.5">{filterCount}</Badge>}<ChevronDown className="size-3.5" /></Button>
          <Button type="button" variant="outline" className="h-11 min-w-24 whitespace-nowrap" onClick={clearFilters}>清除条件</Button>
        </div>
      </form>
      {keywordInput.trim() !== keyword && <p role="status" className="mt-2 text-xs text-warning-foreground">搜索内容已更改，列表尚未更新；请点击查询。</p>}
      {advancedOpen && <div data-admin-filter-advanced className="mt-4 grid gap-3 rounded-xl border bg-muted/30 p-4 md:grid-cols-3 xl:grid-cols-5">
        <div className="space-y-2"><Label>可接游戏</Label><Select value={game} onValueChange={setGame}><SelectTrigger className="h-11 w-full" aria-label="可接游戏"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部游戏</SelectItem>{games.map(item => <SelectItem key={item} value={item}>{item}</SelectItem>)}</SelectContent></Select></div>
        <div className="space-y-2"><Label>服务方向</Label><Select value={kind} onValueChange={setKind}><SelectTrigger className="h-11 w-full" aria-label="服务方向"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部方向</SelectItem>{Object.entries(adminProviderKindLabel).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
        <div className="space-y-2"><Label>端手游</Label><Select value={platform} onValueChange={setPlatform}><SelectTrigger className="h-11 w-full" aria-label="端手游"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部平台</SelectItem><SelectItem value="PC">端游</SelectItem><SelectItem value="MOBILE">手游</SelectItem></SelectContent></Select></div>
        <div className="space-y-2"><Label>性别</Label><Select value={gender} onValueChange={setGender}><SelectTrigger className="h-11 w-full" aria-label="性别"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部性别</SelectItem><SelectItem value="FEMALE">女陪</SelectItem><SelectItem value="MALE">男陪</SelectItem><SelectItem value="UNKNOWN">未知</SelectItem></SelectContent></Select></div>
        <div className="space-y-2"><Label>通行证</Label><Select value={passTier} onValueChange={setPassTier}><SelectTrigger className="h-11 w-full" aria-label="通行证"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部通行证</SelectItem>{Object.entries(adminPassTierLabel).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
      </div>}
    </CardContent></Card>

    <Card className="min-w-0 overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0"><div><CardTitle className="text-base">陪玩列表</CardTitle><CardDescription className="mt-1">本页 {rows.length} 人 · 资料与业绩均为示例</CardDescription></div><div className="flex items-center gap-2 text-xs text-muted-foreground"><UsersRound className="size-4" />已认证 {allProviders.filter(item => item.verified).length} / {allProviders.length}</div></CardHeader>
      <div className="overflow-x-auto border-t"><Table className="min-w-[1120px]">
        <TableHeader><TableRow><TableHead>陪玩</TableHead><TableHead>服务范围</TableHead><TableHead>接单资格</TableHead><TableHead>状态 / 认证</TableHead><TableHead>押金</TableHead><TableHead className="text-right">业绩</TableHead><TableHead>通行证</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader>
        <TableBody>{rows.map(provider => {
          const activeQualifications = provider.qualifications.filter(item => item.active)
          return <TableRow key={provider.id}>
            <TableCell><div className="flex items-center gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-full bg-primary/10 text-sm font-semibold text-primary">{provider.displayName.slice(0, 1)}</span><div><button type="button" onClick={() => onOpen(provider, detailScenario(provider))} className="text-sm font-medium text-primary hover:underline">{provider.displayName}</button><p className="mt-1 font-mono text-[11px] text-muted-foreground">{provider.escortNo} · {provider.username}</p></div></div></TableCell>
            <TableCell className="max-w-60"><div className="flex flex-wrap gap-1">{provider.games.length ? provider.games.map(item => <Badge key={item} variant="secondary" className="text-[10px]">{item}</Badge>) : <span className="text-xs text-muted-foreground">尚未配置游戏</span>}</div><p className="mt-1.5 text-[11px] text-muted-foreground">{provider.serviceItems.length} 项服务技能</p></TableCell>
            <TableCell>{activeQualifications.length ? <><span className="text-xs font-medium">{activeQualifications.length} 项启用资格</span><p className="mt-1 max-w-44 truncate text-[11px] text-muted-foreground">{activeQualifications.map(item => `${item.game} · ${item.grade}`).join(' / ')}</p></> : <span className="text-xs text-warning-foreground">{provider.qualifications.length ? '资格均已停用' : '资格未配置'}</span>}</TableCell>
            <TableCell><Badge variant="outline" className={cn('whitespace-nowrap', provider.status === 'AVAILABLE' && 'border-success/30 bg-success/10 text-success', provider.status === 'BUSY' && 'border-warning/30 bg-warning/10 text-warning-foreground')}>{adminProviderStatusLabel[provider.status]}</Badge><span className={cn('mt-1.5 block text-[11px]', provider.verified ? 'text-success' : 'text-warning-foreground')}>{provider.verified ? '已认证' : '未认证'}</span></TableCell>
            <TableCell><span className="whitespace-nowrap text-xs font-medium tabular-nums">{formatWaveAmount(provider.depositPaid)} / {formatWaveAmount(provider.depositRequired)} 浪花</span><span className="mt-1 block text-[11px] text-muted-foreground">{provider.depositMode === 'CASH' ? '现金缴纳' : '打单抵扣'}</span></TableCell>
            <TableCell className="whitespace-nowrap text-right"><span className="inline-flex items-center gap-1 text-xs font-medium"><Star className="size-3.5 fill-warning text-warning" />{provider.rating ? provider.rating.toFixed(2) : '—'}</span><span className="mt-1 block text-[11px] text-muted-foreground">{provider.completedOrders} 完成单</span></TableCell>
            <TableCell><Badge variant="outline" className="whitespace-nowrap text-[11px]">{adminPassTierLabel[provider.passTier]}</Badge>{provider.recommendationSlot && <span className="mt-1.5 block text-[11px] text-primary">推荐位 {provider.recommendationSlot}</span>}</TableCell>
            <TableCell className="text-right"><Button variant="ghost" size="sm" onClick={() => onOpen(provider, detailScenario(provider))}>查看档案<ChevronRight className="size-3.5" /></Button></TableCell>
          </TableRow>
        })}</TableBody>
      </Table>{rows.length === 0 && <div className="flex min-h-72 flex-col items-center justify-center p-8 text-center"><span className="grid size-12 place-items-center rounded-full bg-muted text-muted-foreground"><UsersRound className="size-5" /></span><h2 className="mt-4 text-base font-semibold">没有匹配的陪玩</h2><p className="mt-2 text-sm text-muted-foreground">调整关键词或清除筛选后再查看。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除全部条件</Button></div>}</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>不连接真实陪玩账号、钱包或认证记录。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    <Dialog open={createOpen} onOpenChange={setCreateOpen}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>创建陪玩账号</DialogTitle><DialogDescription>先建立登录凭证与基础档案，服务资格和押金仍需后续核验。</DialogDescription></DialogHeader><div className="grid gap-4 md:grid-cols-2">
      <div className="space-y-2"><Label htmlFor="create-provider-username">登录账号</Label><Input id="create-provider-username" value={createForm.username} onChange={event => updateCreateForm({ username: event.target.value })} placeholder="陪玩登录用账号" className="h-11" /></div>
      <div className="space-y-2"><Label htmlFor="create-provider-password">登录密码</Label><div className="relative"><Input id="create-provider-password" type={showPassword ? 'text' : 'password'} value={createForm.password} onChange={event => updateCreateForm({ password: event.target.value })} placeholder="至少 6 位" className="h-11 pr-11" /><Button type="button" variant="ghost" size="icon" className="absolute right-1 top-1 size-9" aria-label={showPassword ? '隐藏密码' : '显示密码'} onClick={() => setShowPassword(value => !value)}>{showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}</Button></div></div>
      <div className="space-y-2"><Label htmlFor="create-provider-name">对外昵称</Label><Input id="create-provider-name" value={createForm.displayName} onChange={event => updateCreateForm({ displayName: event.target.value })} placeholder="陪玩展示昵称" className="h-11" /></div>
      <div className="space-y-2"><Label htmlFor="create-provider-phone">手机号（选填）</Label><Input id="create-provider-phone" value={createForm.phone} onChange={event => updateCreateForm({ phone: event.target.value })} placeholder="用于账号联系与检索" className="h-11" /></div>
      <div className="space-y-2"><Label htmlFor="create-provider-gender">性别</Label><Select value={createForm.gender} onValueChange={value => updateCreateForm({ gender: value as AdminProvider['gender'] })}><SelectTrigger id="create-provider-gender" className="h-11 w-full" aria-label="开户性别"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="FEMALE">女陪</SelectItem><SelectItem value="MALE">男陪</SelectItem><SelectItem value="UNKNOWN">暂不填写</SelectItem></SelectContent></Select></div>
      <div className="space-y-2"><Label htmlFor="create-provider-deposit">押金缴纳模式</Label><Select value={createForm.depositMode} onValueChange={value => updateCreateForm({ depositMode: value as AdminProvider['depositMode'] })}><SelectTrigger id="create-provider-deposit" className="h-11 w-full" aria-label="开户押金缴纳模式"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="CASH">现金缴纳</SelectItem><SelectItem value="EARNINGS">打单抵扣</SelectItem></SelectContent></Select></div>
      <div className="space-y-2"><Label htmlFor="create-provider-city">城市（选填）</Label><Input id="create-provider-city" value={createForm.city} onChange={event => updateCreateForm({ city: event.target.value })} placeholder="所在城市" className="h-11" /></div>
      <p className="rounded-lg bg-muted/50 p-3 text-xs leading-5 text-muted-foreground md:col-span-2">创建后按当前规则确定应缴押金。头像、查外挂图、视频介绍与具体接单资格在档案中继续配置。</p>
    </div>{createError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{createError}</AlertDescription></Alert>}<DialogFooter><Button variant="outline" onClick={() => setCreateOpen(false)}>取消</Button><Button onClick={createAccount}>创建账号</Button></DialogFooter></DialogContent></Dialog>

    {feedback && <div role="status" className="fixed bottom-5 right-5 z-50 flex max-w-md items-start gap-3 rounded-xl border bg-card p-4 text-sm shadow-2xl"><CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /><span className="leading-6">{feedback}</span><Button variant="ghost" size="icon" className="-mr-2 -mt-2 size-8" onClick={() => setFeedback('')} aria-label="关闭反馈"><X className="size-4" /></Button></div>}
  </div>
}
