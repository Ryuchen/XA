import { useMemo, useState } from 'react'
import {
  CalendarClock,
  CheckCircle2,
  CircleAlert,
  Copy,
  Edit3,
  Headset,
  Info,
  KeyRound,
  Link2,
  Plus,
  RotateCcw,
  Search,
  ShieldAlert,
  Trash2,
  UserRound,
  UsersRound,
  X,
} from 'lucide-react'
import type { AdminAuditionLinksScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { auditionAccountOptions, initialAdminAuditionLinks, type AdminAuditionLink, type AuditionBinding } from '@/pages/admin/admin-audition-data'
import { cn } from '@/lib/utils'

interface AdminAuditionLinksPageProps {
  scenario: AdminAuditionLinksScenario
  onScenarioChange: (scenario: AdminAuditionLinksScenario) => void
}

type EntryRole = 'boss' | 'provider'
interface LinkForm {
  id: number
  title: string
  remark: string
  expireAt: string
  active: boolean
}

const blankForm: LinkForm = { id: 0, title: '', remark: '', expireAt: '', active: true }
const statusLabel = { ACTIVE: '启用', DISABLED: '停用', EXPIRED: '已过期' }

function bindingLabel(binding: AuditionBinding, role: EntryRole) {
  if (binding.kind === 'unbound') return role === 'boss' ? '未绑定' : '普通报名'
  if (binding.kind === 'legacy') return role === 'boss' ? '旧账号绑定' : '旧账号专属'
  return binding.available === false ? '账号不可用' : role === 'boss' ? '已绑定' : '账号专属'
}

function formatExpireAt(value: string | null) {
  if (!value) return '长期有效'
  return `截止 ${value.slice(0, 10)} ${value.slice(11, 16)}`
}

export function AdminAuditionLinksPage({ scenario, onScenarioChange }: AdminAuditionLinksPageProps) {
  const [links, setLinks] = useState<AdminAuditionLink[]>(initialAdminAuditionLinks)
  const [keyword, setKeyword] = useState('')
  const [appliedKeyword, setAppliedKeyword] = useState('')
  const [status, setStatus] = useState('ALL')
  const [formOpen, setFormOpen] = useState(scenario === 'save-review')
  const [form, setForm] = useState<LinkForm>(scenario === 'save-review' ? { id: 107, title: '手游技术陪玩试音', remark: '和平精英与王者荣耀方向，需准备常用设备与清晰麦克风。', expireAt: '2026-11-30T18:00', active: true } : blankForm)
  const [formError, setFormError] = useState(scenario === 'save-review' ? '保存结果未确认，请关闭后在列表核对，原请求可能仍在处理。' : '')
  const [saveUnknown, setSaveUnknown] = useState(scenario === 'save-review')
  const [deleteTarget, setDeleteTarget] = useState<AdminAuditionLink | null>(null)
  const [binding, setBinding] = useState<{ row: AdminAuditionLink; role: EntryRole } | null>(scenario === 'binding-search-error' ? { row: initialAdminAuditionLinks[0], role: 'boss' } : null)
  const [accountSearch, setAccountSearch] = useState('')
  const [selectedAccount, setSelectedAccount] = useState('')
  const [searchError, setSearchError] = useState(scenario === 'binding-search-error')
  const [bindingConfirm, setBindingConfirm] = useState<{ accountId: number | null } | null>(null)
  const [copyConfirm, setCopyConfirm] = useState<{ row: AdminAuditionLink; role: EntryRole } | null>(null)
  const [reviewChecked, setReviewChecked] = useState(false)
  const [reviewText, setReviewText] = useState('')
  const [releaseOpen, setReleaseOpen] = useState(false)
  const [feedback, setFeedback] = useState<{ text: string; tone: 'success' | 'warning' } | null>(null)

  const canEdit = scenario !== 'readonly'
  const canDelete = scenario !== 'readonly' && scenario !== 'edit-only'
  const canBindBoss = scenario !== 'readonly' && scenario !== 'edit-only'
  const canBindProvider = canBindBoss
  const bindingReview = scenario === 'binding-review'

  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    const query = appliedKeyword.trim().toLocaleLowerCase()
    return links.filter(link => {
      const matchesKeyword = !query || `${link.title} ${link.remark}`.toLocaleLowerCase().includes(query)
      const matchesStatus = status === 'ALL' || String(link.active) === status
      return matchesKeyword && matchesStatus
    })
  }, [appliedKeyword, links, scenario, status])

  const accountOptions = useMemo(() => {
    if (!binding || !accountSearch.trim()) return []
    const query = accountSearch.trim().toLocaleLowerCase()
    return auditionAccountOptions.filter(account => account.role === binding.role && `${account.name} ${account.accountNo} ${account.username}`.toLocaleLowerCase().includes(query))
  }, [accountSearch, binding])

  function notify(text: string, tone: 'success' | 'warning' = 'success') {
    setFeedback({ text, tone })
  }

  function openCreate() {
    if (!canEdit || bindingReview) return
    setForm({ ...blankForm })
    setFormError('')
    setSaveUnknown(false)
    setFormOpen(true)
  }

  function openEdit(link: AdminAuditionLink) {
    if (!canEdit || bindingReview) return
    setForm({ id: link.id, title: link.title, remark: link.remark, expireAt: link.expireAt || '', active: link.active })
    setFormError('')
    setSaveUnknown(false)
    setFormOpen(true)
  }

  function closeForm() {
    setFormOpen(false)
    if (saveUnknown && scenario === 'save-review') onScenarioChange('default')
    setSaveUnknown(false)
    setFormError('')
  }

  function saveLink() {
    if (!canEdit || saveUnknown || bindingReview) return
    if (!form.title.trim()) { setFormError('请填写活动标题'); return }
    const expireAt = form.expireAt || null
    const entryStatus = !form.active ? 'DISABLED' : expireAt && new Date(expireAt).getTime() < Date.now() ? 'EXPIRED' : 'ACTIVE'
    const current = links.find(link => link.id === form.id)
    const payload: AdminAuditionLink = {
      id: form.id || Math.max(...links.map(link => link.id)) + 1,
      title: form.title.trim(),
      remark: form.remark.trim(),
      expireAt,
      active: form.active,
      entryStatus,
      bossBinding: current?.bossBinding || { kind: 'unbound' },
      providerBinding: current?.providerBinding || { kind: 'unbound' },
      bossUrlPlaceholder: current?.bossUrlPlaceholder || scenario === 'placeholder',
      providerUrlPlaceholder: current?.providerUrlPlaceholder || scenario === 'placeholder',
      createdAt: current?.createdAt || '刚刚 · 示例',
      hasSignups: current?.hasSignups || false,
    }
    setLinks(items => form.id ? items.map(item => item.id === form.id ? payload : item) : [payload, ...items])
    setFormOpen(false)
    setFormError('')
    notify(`试音活动「${payload.title}」已在原型中保存，未生成真实登录链接。`)
  }

  function confirmDelete() {
    if (!deleteTarget || !canDelete || bindingReview) return
    if (deleteTarget.hasSignups) {
      notify('已有试音报名，请停用链接并保留报名与审核记录。', 'warning')
    } else {
      setLinks(items => items.filter(item => item.id !== deleteTarget.id))
      notify(`试音活动「${deleteTarget.title}」已在原型中删除，两个入口均已失效。`)
    }
    setDeleteTarget(null)
  }

  async function writeLink(link: AdminAuditionLink, role: EntryRole) {
    const exampleUrl = `https://prototype.invalid/audition?role=${role}&activity=${link.id}&token=example`
    try {
      if (!navigator.clipboard) throw new Error('clipboard unavailable')
      await navigator.clipboard.writeText(exampleUrl)
      notify(`已复制${role === 'boss' ? '老板' : '陪玩'}示例链接，请仅交给预定使用人。`)
    } catch {
      notify('复制失败，请检查浏览器剪贴板权限。', 'warning')
    } finally {
      setCopyConfirm(null)
    }
  }

  function copyLink(link: AdminAuditionLink, role: EntryRole) {
    if (bindingReview) { notify('请先核对上次绑定结果。', 'warning'); return }
    if (link.entryStatus !== 'ACTIVE') { notify('入口未启用或已过期，请先核对活动状态。', 'warning'); return }
    const current = role === 'boss' ? link.bossBinding : link.providerBinding
    if (role === 'boss' && (!canBindBoss || current.kind === 'unbound' || current.available === false)) { notify('老板入口未绑定或账号不可用，无法复制登录链接。', 'warning'); return }
    if (role === 'provider' && current.kind !== 'unbound' && !canBindProvider) { notify('无权读取陪玩专属链接。', 'warning'); return }
    if (scenario === 'placeholder') { notify('入口域名待配置，请先核对部署地址。', 'warning'); return }
    if (role === 'provider' && current.kind !== 'unbound') { setCopyConfirm({ row: link, role }); return }
    void writeLink(link, role)
  }

  function openBinding(link: AdminAuditionLink, role: EntryRole) {
    if (bindingReview || (role === 'boss' ? !canBindBoss : !canBindProvider)) return
    setBinding({ row: link, role })
    setAccountSearch('')
    setSelectedAccount('')
    setSearchError(scenario === 'binding-search-error')
  }

  function requestBinding(accountId: number | null) {
    if (!binding || bindingReview) return
    if (accountId !== null && !accountOptions.some(account => account.id === accountId)) return
    const current = binding.role === 'boss' ? binding.row.bossBinding : binding.row.providerBinding
    if (accountId !== null && current.kind === 'account' && current.accountId === accountId) {
      notify('该账号已绑定，无需重复操作；当前链接保持不变。')
      return
    }
    setBindingConfirm({ accountId })
  }

  function confirmBinding() {
    if (!binding || !bindingConfirm || bindingReview) return
    const account = bindingConfirm.accountId === null ? null : auditionAccountOptions.find(item => item.id === bindingConfirm.accountId && item.role === binding.role)
    if (bindingConfirm.accountId !== null && !account) return
    const nextBinding: AuditionBinding = account ? { kind: 'account', accountId: account.id, name: account.name, accountNo: account.accountNo, username: account.username, available: true, otpRequired: false } : { kind: 'unbound' }
    setLinks(items => items.map(item => item.id === binding.row.id ? { ...item, [binding.role === 'boss' ? 'bossBinding' : 'providerBinding']: nextBinding } : item))
    notify(`${binding.role === 'boss' ? '老板' : '陪玩'}入口绑定已在原型中更新，旧链接已失效。`)
    setBinding(null)
    setBindingConfirm(null)
  }

  function clearFilters() {
    setKeyword('')
    setAppliedKeyword('')
    setStatus('ALL')
    if (scenario === 'empty') onScenarioChange('default')
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="试音活动加载中"><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-20 rounded-xl" /><Skeleton className="h-[520px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">试音活动加载失败</CardTitle><CardDescription className="mt-2">请检查网络或查看权限后重试，当前没有展示推测入口。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm"><div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
      <div><div className="flex items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">试音招募</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">示例入口</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">按活动分别管理老板观看入口与陪玩报名入口，核对有效期、账号绑定和链接使用范围。</p></div>
      <div className="flex items-end gap-5"><dl className="grid grid-cols-2 gap-5"><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{links.length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">全部活动</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{links.filter(link => link.entryStatus === 'ACTIVE').length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">有效入口</dt></div></dl>{canEdit && <Button variant="secondary" disabled={bindingReview} onClick={openCreate}><Plus className="size-4" />新增试音链接</Button>}</div>
    </div></section>

    <Alert><ShieldAlert className="size-4" /><AlertTitle>绑定账号的登录链接仅交给账号本人</AlertTitle><AlertDescription>变更绑定会使当前角色的旧链接失效，另一端链接不变；已签发的登录态不会自动撤销。本页仅复制明确标识的示例地址。</AlertDescription></Alert>
    {scenario === 'readonly' && <Alert><Info className="size-4" /><AlertTitle>当前为仅查看权限</AlertTitle><AlertDescription>可核对活动状态与普通陪玩报名入口；活动编辑、删除和账号绑定操作已隐藏。</AlertDescription></Alert>}
    {scenario === 'edit-only' && <Alert><Info className="size-4" /><AlertTitle>当前仅可编辑活动</AlertTitle><AlertDescription>可新增、编辑活动，不可删除或管理账号绑定；普通陪玩报名入口仍可按权限复制。</AlertDescription></Alert>}
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>活动更新失败，当前显示上次成功读取的列表</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">复制或变更绑定前请核对最新状态。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {bindingReview && <Alert variant="destructive"><KeyRound className="size-4" /><AlertTitle>老板入口绑定结果待核对</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">「11 月端游陪玩招募」可能已更换绑定，其他绑定与复制操作已锁定。<Button variant="link" className="h-auto p-0" onClick={() => { setReviewChecked(true); setReviewText('当前：已绑定 · 岚声体验 · B-0303；这是读取时快照。') }}>读取当前绑定</Button>{reviewText && <span className="text-xs">{reviewText}</span>}<Button variant="link" className="h-auto p-0" disabled={!reviewChecked} onClick={() => setReleaseOpen(true)}>已核对，解除限制</Button></AlertDescription></Alert>}

    <Card className="min-w-0 overflow-hidden"><CardHeader className="gap-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle className="flex items-center gap-2 text-base"><Headset className="size-4 text-primary" />试音活动与双入口</CardTitle><CardDescription className="mt-1">本页 {rows.length} 项 · 按创建时间倒序</CardDescription></div><Badge variant="outline" className="font-normal">全部数据均为示例</Badge></div>
      <form data-admin-filter className="flex flex-wrap items-end gap-3" onSubmit={event => { event.preventDefault(); setAppliedKeyword(keyword) }}><div className="min-w-56 flex-1 space-y-2"><Label htmlFor="audition-keyword">搜索活动</Label><div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input id="audition-keyword" value={keyword} onChange={event => { setKeyword(event.target.value); if (!event.target.value) setAppliedKeyword('') }} placeholder="活动标题或备注" className="h-10 pl-9" /></div></div><div className="space-y-2"><Label htmlFor="audition-status">启用状态</Label><Select value={status} onValueChange={setStatus}><SelectTrigger id="audition-status" className="h-10 w-36" aria-label="启用状态"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部状态</SelectItem><SelectItem value="true">启用</SelectItem><SelectItem value="false">停用</SelectItem></SelectContent></Select></div><Button type="submit" variant="outline" className="h-10 min-w-20 whitespace-nowrap">查询</Button></form></CardHeader>
      <div className="overflow-x-auto border-t"><Table className={cn('min-w-full', canEdit || canBindBoss ? 'lg:min-w-[1080px]' : 'lg:min-w-[900px]')}><TableHeader><TableRow><TableHead>试音活动</TableHead><TableHead>老板入口</TableHead><TableHead>陪玩入口</TableHead><TableHead>状态 / 有效期</TableHead>{(canEdit || canDelete) && <TableHead className="text-right">活动操作</TableHead>}</TableRow></TableHeader><TableBody>{rows.map(link => <TableRow key={link.id}>
        <TableCell className="max-w-72"><div className="flex items-start gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary"><Headset className="size-5" /></span><div><h2 className="text-sm font-medium">{link.title}</h2><p className="mt-1 font-mono text-[10px] text-muted-foreground">活动 #{link.id} · {link.createdAt}</p>{link.remark && <p className="mt-1 text-xs leading-5 text-muted-foreground">{link.remark}</p>}</div></div></TableCell>
        <TableCell><div className="flex items-center gap-2"><UserRound className="size-4 shrink-0 text-primary" /><strong className="text-xs font-medium">{bindingLabel(link.bossBinding, 'boss')}</strong></div>{link.bossBinding.name && <p className="mt-1 text-[11px] text-muted-foreground">{link.bossBinding.name}{link.bossBinding.accountNo ? ` · ${link.bossBinding.accountNo}` : ''}{link.bossBinding.username ? ` · ${link.bossBinding.username}` : ''}</p>}{scenario === 'placeholder' && <p className="mt-1 text-[11px] text-warning">入口域名待配置</p>}{canBindBoss && <div className="mt-3 flex flex-wrap gap-1.5"><Button variant="outline" size="sm" disabled={bindingReview} onClick={() => copyLink(link, 'boss')}><Copy className="size-3.5" />老板链接</Button><Button variant="ghost" size="sm" disabled={bindingReview} onClick={() => openBinding(link, 'boss')}><KeyRound className="size-3.5" />老板绑定</Button></div>}</TableCell>
        <TableCell><div className="flex items-center gap-2"><UsersRound className="size-4 shrink-0 text-primary" /><strong className="text-xs font-medium">{bindingLabel(link.providerBinding, 'provider')}</strong></div>{link.providerBinding.name && <p className="mt-1 text-[11px] text-muted-foreground">{link.providerBinding.name}{link.providerBinding.accountNo ? ` · ${link.providerBinding.accountNo}` : ''}{link.providerBinding.username ? ` · ${link.providerBinding.username}` : ''}</p>}{link.providerBinding.otpRequired && <p className="mt-1 text-[11px] text-warning">需使用微信快捷登录并完成 OTP 验证</p>}{scenario === 'placeholder' && <p className="mt-1 text-[11px] text-warning">入口域名待配置</p>}<div className="mt-3 flex flex-wrap gap-1.5">{(link.providerBinding.kind === 'unbound' || canBindProvider) && <Button variant="outline" size="sm" disabled={bindingReview} onClick={() => copyLink(link, 'provider')}><Copy className="size-3.5" />陪玩链接</Button>}{canBindProvider && <Button variant="ghost" size="sm" disabled={bindingReview} onClick={() => openBinding(link, 'provider')}><KeyRound className="size-3.5" />陪玩绑定</Button>}</div></TableCell>
        <TableCell><Badge variant="outline" className={cn(link.entryStatus === 'ACTIVE' ? 'border-success/30 bg-success/10 text-success' : link.entryStatus === 'EXPIRED' ? 'border-warning/30 bg-warning/10 text-warning' : 'text-muted-foreground')}>{statusLabel[link.entryStatus]}</Badge><p className="mt-2 flex items-center gap-1.5 text-[11px] text-muted-foreground"><CalendarClock className="size-3.5" />{formatExpireAt(link.expireAt)}</p></TableCell>
        {(canEdit || canDelete) && <TableCell><div className="flex justify-end gap-1.5">{canEdit && <Button variant="outline" size="sm" disabled={bindingReview} onClick={() => openEdit(link)}><Edit3 className="size-3.5" />编辑</Button>}{canDelete && <Button variant="ghost" size="sm" disabled={bindingReview} className="text-destructive hover:text-destructive" onClick={() => setDeleteTarget(link)}><Trash2 className="size-3.5" />删除</Button>}</div></TableCell>}
      </TableRow>)}</TableBody></Table>{rows.length === 0 && <div className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><Link2 className="size-7 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有符合筛选条件的试音活动</h2><p className="mt-2 text-sm text-muted-foreground">清除关键词与状态筛选后查看全部活动。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除筛选</Button></div>}</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>链接、账号与活动均为示例，不连接真实登录态。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    <Dialog open={formOpen} onOpenChange={open => { if (!open) closeForm() }}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>{form.id ? '编辑试音链接' : '新增试音链接'}</DialogTitle><DialogDescription>管理活动标题、说明、截止时间与启用状态。</DialogDescription></DialogHeader><div className="grid gap-4 md:grid-cols-2">
      <div className="space-y-2 md:col-span-2"><Label htmlFor="audition-title">活动标题</Label><Input id="audition-title" value={form.title} onChange={event => setForm(value => ({ ...value, title: event.target.value }))} maxLength={100} placeholder="如：12 月端游陪玩招募" className="h-11" /></div>
      <div className="space-y-2 md:col-span-2"><Label htmlFor="audition-remark">备注</Label><Textarea id="audition-remark" value={form.remark} onChange={event => setForm(value => ({ ...value, remark: event.target.value }))} maxLength={255} placeholder="说明试音方向与报名准备事项" /></div>
      <div className="space-y-2"><Label htmlFor="audition-expire">截止时间</Label><Input id="audition-expire" type="datetime-local" step="1" value={form.expireAt} onChange={event => setForm(value => ({ ...value, expireAt: event.target.value }))} className="h-11" /><p className="text-[11px] text-muted-foreground">留空为长期有效；过期后入口不可使用。</p></div>
      <label className="flex min-h-11 items-center justify-between gap-3 self-start rounded-xl border px-3"><span><span className="block text-xs font-medium">启用活动</span><span className="mt-1 block text-[11px] text-muted-foreground">停用后两个入口均不可使用</span></span><Switch aria-label="启用活动" checked={form.active} onCheckedChange={value => setForm(item => ({ ...item, active: value }))} /></label>
    </div><Alert><Info className="size-4" /><AlertDescription>新建后可分别管理老板与陪玩入口。老板未绑定时不能复制登录链接；普通陪玩入口供已登录陪玩报名。</AlertDescription></Alert>{formError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{formError}</AlertDescription></Alert>}<DialogFooter><Button variant="outline" onClick={closeForm}>关闭</Button><Button disabled={saveUnknown} onClick={saveLink}>保存活动</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={!!deleteTarget} onOpenChange={open => { if (!open) setDeleteTarget(null) }}><DialogContent className="sm:max-w-lg"><DialogHeader><DialogTitle>删除试音链接</DialogTitle><DialogDescription>删除前请核对活动与已有报名记录。</DialogDescription></DialogHeader>{deleteTarget && <Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>删除「{deleteTarget.title}」将使两个入口失效</AlertTitle><AlertDescription>已有试音报名时不能删除，请停用以保留报名与审核记录；已签发的登录态不会自动撤销。</AlertDescription></Alert>}<DialogFooter><Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button><Button variant="destructive" onClick={confirmDelete}><Trash2 className="size-4" />确认删除</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={!!binding} onOpenChange={open => { if (!open) setBinding(null) }}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-xl"><DialogHeader><DialogTitle>管理{binding?.role === 'boss' ? '老板' : '陪玩'}入口</DialogTitle><DialogDescription>核对试音活动与绑定账号，变更将轮换当前角色的链接。</DialogDescription></DialogHeader>{binding && <><dl className="grid gap-3 rounded-xl border bg-muted/20 p-4 text-xs sm:grid-cols-2"><div><dt className="text-muted-foreground">试音活动</dt><dd className="mt-1 font-medium">{binding.row.title} · #{binding.row.id}</dd></div><div><dt className="text-muted-foreground">当前绑定</dt><dd className="mt-1 font-medium">{bindingLabel(binding.role === 'boss' ? binding.row.bossBinding : binding.row.providerBinding, binding.role)}</dd></div><div className="sm:col-span-2"><dt className="text-muted-foreground">有效时间</dt><dd className="mt-1 font-medium">{formatExpireAt(binding.row.expireAt)}</dd></div></dl><Alert variant="destructive"><KeyRound className="size-4" /><AlertTitle>持专属链接的人可能登录绑定账号</AlertTitle><AlertDescription>只交给账号本人；变更会使旧链接失效，已签发登录态不会自动撤销，另一端链接不变。{binding.role === 'provider' && '绑定不会限制其他已登录陪玩报名，原有报名记录保留。'}</AlertDescription></Alert><div className="space-y-2"><Label htmlFor="binding-account-search">搜索{binding.role === 'boss' ? '老板' : '陪玩'}账号</Label><div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input id="binding-account-search" value={accountSearch} onChange={event => { setAccountSearch(event.target.value); setSearchError(false); setSelectedAccount('') }} placeholder="搜索编号、账号或昵称" className="h-11 pl-9" /></div></div>{searchError ? <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>账号读取失败</AlertTitle><AlertDescription><Button variant="link" className="h-auto p-0" onClick={() => { setSearchError(false); setAccountSearch('岚') }}>重试搜索</Button></AlertDescription></Alert> : <div className="space-y-2"><Label htmlFor="binding-account">选择账号</Label><Select value={selectedAccount} onValueChange={setSelectedAccount}><SelectTrigger id="binding-account" className="h-11 w-full" aria-label="选择绑定账号"><SelectValue placeholder={accountSearch ? '请选择搜索结果' : '请先搜索账号'} /></SelectTrigger><SelectContent>{accountOptions.map(account => <SelectItem key={account.id} value={String(account.id)}>{account.name} · {account.accountNo} · {account.username}</SelectItem>)}</SelectContent></Select>{accountSearch && accountOptions.length === 0 && <p className="text-xs text-muted-foreground">没有匹配的可用账号，请更换关键词。</p>}</div>}<p className="text-xs leading-5 text-muted-foreground">账号状态、链接有效期和部署地址会影响实际使用；状态以再次读取时为准。</p></>}<DialogFooter className="flex-wrap"><Button variant="outline" onClick={() => setBinding(null)}>取消</Button>{binding && (binding.role === 'boss' ? binding.row.bossBinding : binding.row.providerBinding).kind !== 'unbound' && <Button variant="outline" onClick={() => requestBinding(null)}>解除绑定</Button>}<Button disabled={!selectedAccount} onClick={() => requestBinding(Number(selectedAccount))}>绑定账号</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={!!bindingConfirm} onOpenChange={open => { if (!open) setBindingConfirm(null) }}><DialogContent className="sm:max-w-lg"><DialogHeader><DialogTitle>{bindingConfirm?.accountId === null ? '确认解除绑定' : `确认绑定${binding?.role === 'boss' ? '老板' : '陪玩'}`}</DialogTitle><DialogDescription>请确认目标账号和链接失效影响。</DialogDescription></DialogHeader><Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>{binding?.row.title}</AlertTitle><AlertDescription>变更后旧{binding?.role === 'boss' ? '老板' : '陪玩'}链接失效，已签发登录态不会自动撤销；{binding?.role === 'boss' ? '陪玩链接不变。' : '老板链接与已有报名记录不变，其他已登录陪玩仍可报名。'}</AlertDescription></Alert><DialogFooter><Button variant="outline" onClick={() => setBindingConfirm(null)}>返回</Button><Button onClick={confirmBinding}>确认变更</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={!!copyConfirm} onOpenChange={open => { if (!open) setCopyConfirm(null) }}><DialogContent className="sm:max-w-lg"><DialogHeader><DialogTitle>复制陪玩专属链接</DialogTitle><DialogDescription>该链接可能登录陪玩账号「{copyConfirm?.row.providerBinding.name}」。</DialogDescription></DialogHeader><Alert><ShieldAlert className="size-4" /><AlertTitle>仅交给账号本人</AlertTitle><AlertDescription>其他已登录陪玩也可报名。{copyConfirm?.row.providerBinding.otpRequired ? '当前需使用微信快捷登录并完成 OTP 验证。' : '请核对预定使用人后再复制。'}</AlertDescription></Alert><DialogFooter><Button variant="outline" onClick={() => setCopyConfirm(null)}>取消</Button><Button onClick={() => copyConfirm && void writeLink(copyConfirm.row, copyConfirm.role)}><Copy className="size-4" />确认复制</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={releaseOpen} onOpenChange={setReleaseOpen}><DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle>解除待核对限制</DialogTitle><DialogDescription>当前绑定是读取时快照，原请求仍可能处理。确认已核对并解除限制？</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={() => setReleaseOpen(false)}>继续核对</Button><Button onClick={() => { setReleaseOpen(false); onScenarioChange('default') }}>已核对，解除</Button></DialogFooter></DialogContent></Dialog>

    {feedback && <div role="status" className="fixed bottom-5 right-5 z-50 flex max-w-md items-start gap-3 rounded-xl border bg-card p-4 text-sm shadow-2xl">{feedback.tone === 'success' ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /> : <CircleAlert className="mt-0.5 size-4 shrink-0 text-warning" />}<span className="leading-6">{feedback.text}</span><Button variant="ghost" size="icon" className="-mr-2 -mt-2 size-8" onClick={() => setFeedback(null)} aria-label="关闭反馈"><X className="size-4" /></Button></div>}
  </div>
}
