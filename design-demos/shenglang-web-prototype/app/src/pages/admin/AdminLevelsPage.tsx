import { useMemo, useState } from 'react'
import {
  Award,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Edit3,
  Info,
  Layers3,
  Link2,
  Percent,
  Plus,
  RotateCcw,
  ShieldAlert,
  Trash2,
  X,
} from 'lucide-react'
import type { AdminLevelsScenario } from '@/components/admin/admin-config'
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
import { adminScopeRules, initialAdminLevels, type AdminLevel } from '@/pages/admin/admin-level-data'
import { cn } from '@/lib/utils'

interface AdminLevelsPageProps {
  scenario: AdminLevelsScenario
  onScenarioChange: (scenario: AdminLevelsScenario) => void
}

interface LevelForm {
  id: number
  name: string
  commissionRate: string
  remark: string
  sortOrder: string
  active: boolean
}

const blankForm: LevelForm = { id: 0, name: '', commissionRate: '20', remark: '', sortOrder: '0', active: true }

export function AdminLevelsPage({ scenario, onScenarioChange }: AdminLevelsPageProps) {
  const [levels, setLevels] = useState<AdminLevel[]>(initialAdminLevels)
  const [status, setStatus] = useState(scenario === 'inactive' ? 'false' : 'ALL')
  const [scopeExpanded, setScopeExpanded] = useState(false)
  const [scopeResolved, setScopeResolved] = useState(false)
  const [formOpen, setFormOpen] = useState(scenario === 'save-review')
  const [form, setForm] = useState<LevelForm>(scenario === 'save-review' ? { ...initialAdminLevels[1], commissionRate: String(initialAdminLevels[1].commissionRate), sortOrder: String(initialAdminLevels[1].sortOrder) } : blankForm)
  const [saveError, setSaveError] = useState(scenario === 'save-review' ? '保存结果未确认，请关闭后在列表核对，避免重复提交。' : '')
  const [saveUnknown, setSaveUnknown] = useState(scenario === 'save-review')
  const [deleteTarget, setDeleteTarget] = useState<AdminLevel | null>(null)
  const [impactResolved, setImpactResolved] = useState(false)
  const [impactError, setImpactError] = useState(scenario === 'impact-error')
  const [deleteChecked, setDeleteChecked] = useState(false)
  const [deleteResult, setDeleteResult] = useState('')
  const [releaseOpen, setReleaseOpen] = useState(false)
  const [feedback, setFeedback] = useState('')

  function updateForm<K extends keyof LevelForm>(key: K, value: LevelForm[K]) {
    setForm(current => ({ ...current, [key]: value }))
    if (!saveUnknown) setSaveError('')
  }

  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    return levels.filter(item => status === 'ALL' || String(item.active) === status).sort((a, b) => a.sortOrder - b.sortOrder)
  }, [levels, scenario, status])
  const deleteReview = scenario === 'delete-review'
  const impactLoading = scenario === 'impact-loading' && !impactResolved
  const canEdit = scenario !== 'readonly'
  const canDelete = canEdit && scenario !== 'edit-only'

  function openCreate() {
    if (!canEdit) return
    setForm({ ...blankForm })
    setSaveError('')
    setSaveUnknown(false)
    setFormOpen(true)
  }

  function openEdit(level: AdminLevel) {
    if (!canEdit) return
    setForm({ id: level.id, name: level.name, commissionRate: String(level.commissionRate), remark: level.remark, sortOrder: String(level.sortOrder), active: level.active })
    setSaveError('')
    setSaveUnknown(false)
    setFormOpen(true)
  }

  function closeForm() {
    setFormOpen(false)
    if (saveUnknown) {
      setSaveUnknown(false)
      setSaveError('')
      setFeedback('已返回等级列表，请核对当前示例记录，避免重复保存。')
      if (scenario === 'save-review') onScenarioChange('default')
    }
  }

  function saveLevel() {
    if (!canEdit || saveUnknown) return
    const commissionRate = Number(form.commissionRate)
    const sortOrder = Number(form.sortOrder)
    if (!form.name.trim()) { setSaveError('请填写等级名称'); return }
    if (levels.some(item => item.id !== form.id && item.name === form.name.trim())) { setSaveError('等级名称已存在，请使用其他名称'); return }
    if (form.commissionRate.trim() === '' || !Number.isInteger(commissionRate) || commissionRate < 0 || commissionRate > 100 || form.sortOrder.trim() === '' || !Number.isInteger(sortOrder) || sortOrder < 0) { setSaveError('抽成率须为 0–100 的整数，排序须为不小于 0 的整数'); return }
    const payload: AdminLevel = {
      id: form.id || levels.reduce((maxId, item) => Math.max(maxId, item.id), 0) + 1,
      name: form.name.trim(), commissionRate, remark: form.remark.trim(), sortOrder, active: form.active,
      impact: form.id ? levels.find(item => item.id === form.id)?.impact || { providers: 0, services: 0, registrations: 0 } : { providers: 0, services: 0, registrations: 0 },
    }
    setLevels(items => form.id ? items.map(item => item.id === form.id ? payload : item) : [...items, payload])
    setFormOpen(false)
    setSaveError('')
    setFeedback(`等级「${payload.name}」已在原型中保存，未修改真实抽成配置。`)
  }

  function openDelete(level: AdminLevel) {
    if (!canDelete) return
    setDeleteTarget(level)
    setImpactResolved(false)
    setImpactError(scenario === 'impact-error')
  }

  function retryImpact() {
    setImpactError(false)
    setImpactResolved(true)
  }

  function confirmDelete() {
    if (!canDelete || !deleteTarget || impactError || impactLoading || deleteReview) return
    setLevels(items => items.filter(item => item.id !== deleteTarget.id))
    setFeedback(`等级「${deleteTarget.name}」已在原型中删除，关联等级字段按规则清空。`)
    setDeleteTarget(null)
  }

  function checkDeletion() {
    setDeleteChecked(true)
    setDeleteResult('当前未找到该等级；请继续核对关联记录。')
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="陪玩等级加载中"><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-24 rounded-xl" /><Skeleton className="h-[480px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">陪玩等级加载失败</CardTitle><CardDescription className="mt-2">请检查网络或配置权限后重试，当前没有展示推测等级。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm"><div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
      <div><div className="flex items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">陪玩等级</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">示例配置</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">管理等级抽成、展示顺序与启用状态，并查看接单档位的自动抽成规则。</p></div>
      <div className="flex items-end gap-5"><dl className="grid grid-cols-2 gap-5"><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{levels.length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">全部等级</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{levels.filter(item => item.active).length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">当前启用</dt></div></dl>{canEdit && <Button variant="secondary" onClick={openCreate}><Plus className="size-4" />新增等级</Button>}</div>
    </div></section>

    {!canEdit && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前为仅查看权限</AlertTitle><AlertDescription>可核对等级、抽成与接单档位规则；新增、编辑和删除需要相应的配置权限。</AlertDescription></Alert>}
    {scenario === 'edit-only' && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前可编辑等级，但不可删除</AlertTitle><AlertDescription>可新增等级并修改抽成、排序与启停状态；删除操作需要单独权限。</AlertDescription></Alert>}
    <Alert><Info className="size-4" /><AlertTitle>等级抽成不是所有订单的统一抽成</AlertTitle><AlertDescription>部分商品与手工报单会使用等级抽成，具体以订单或报单记录为准；自动抽成则按订单档位计算。</AlertDescription></Alert>
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>等级更新失败，当前显示上次成功读取的列表</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">请在保存或删除前核对当前配置。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {deleteReview && <Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>删除结果未确认</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">请核对等级「正式陪玩」；原请求可能仍在处理中，其他删除操作已锁定。<Button variant="link" className="h-auto p-0" onClick={checkDeletion}>读取当前状态</Button>{deleteResult && <span className="text-xs">{deleteResult}</span>}<Button variant="link" className="h-auto p-0" disabled={!deleteChecked} onClick={() => setReleaseOpen(true)}>已核对，解除限制</Button></AlertDescription></Alert>}

    <Card><CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0"><div><CardTitle className="flex items-center gap-2 text-base"><Layers3 className="size-4 text-primary" />接单档位与自动抽成</CardTitle><CardDescription className="mt-1">档位从低到高排列，同范围高档可接低档</CardDescription></div><Button variant="outline" aria-expanded={scopeExpanded} onClick={() => setScopeExpanded(value => !value)}>{scopeExpanded ? '收起规则' : '查看规则'}<ChevronDown className={cn('size-4 transition-transform', scopeExpanded && 'rotate-180')} /></Button></CardHeader>{scopeExpanded && <CardContent className="border-t">
      {scenario === 'scope-error' && !scopeResolved ? <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>档位规则读取失败</AlertTitle><AlertDescription><Button variant="link" className="h-auto p-0" onClick={() => setScopeResolved(true)}>重新读取</Button></AlertDescription></Alert> : <><div className="grid gap-3 lg:grid-cols-2">{adminScopeRules.map(rule => <section key={rule.id} className="rounded-xl border bg-muted/20 p-4"><h3 className="text-sm font-medium">{rule.scope}</h3><ul className="mt-3 flex flex-wrap gap-2">{rule.grades.map(grade => <li key={grade.label} className="flex min-w-28 items-center justify-between gap-3 rounded-lg border bg-card px-3 py-2 text-xs"><span>{grade.label}</span><strong className="text-primary tabular-nums">{grade.commissionRate == null ? '按项目' : `${grade.commissionRate}%`}</strong></li>)}</ul></section>)}</div><p className="mt-4 text-xs leading-5 text-muted-foreground">自动抽成按订单档位，具体订单可能采用其他已配置的抽成规则；以上比例为明确示例。</p></>}
    </CardContent>}</Card>

    <Card className="min-w-0 overflow-hidden"><CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0"><div><CardTitle className="text-base">等级配置</CardTitle><CardDescription className="mt-1">本页 {rows.length} 项 · 按排序值升序</CardDescription></div><Select value={status} onValueChange={setStatus}><SelectTrigger className="h-10 w-36" aria-label="等级状态"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部状态</SelectItem><SelectItem value="true">启用</SelectItem><SelectItem value="false">停用</SelectItem></SelectContent></Select></CardHeader>
      <div className="overflow-x-auto border-t"><Table className={cn(canEdit ? 'min-w-[900px]' : 'min-w-[720px]')}><TableHeader><TableRow><TableHead>陪玩等级</TableHead><TableHead>等级抽成</TableHead><TableHead>排序</TableHead><TableHead>状态</TableHead>{canEdit && <TableHead className="text-right">操作</TableHead>}</TableRow></TableHeader><TableBody>{rows.map(level => <TableRow key={level.id}>
        <TableCell><div className="flex items-start gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary"><Award className="size-5" /></span><div><h3 className="text-sm font-medium">{level.name}</h3>{level.remark && <p className="mt-1 max-w-xl text-xs leading-5 text-muted-foreground">{level.remark}</p>}<p className="mt-1 font-mono text-[10px] text-muted-foreground">ID {level.id}</p></div></div></TableCell>
        <TableCell><div className="flex items-center gap-1.5"><Percent className="size-4 text-primary" /><strong className="text-lg font-semibold tabular-nums">{level.commissionRate}%</strong></div><p className="mt-1 text-[11px] text-muted-foreground">部分业务使用</p></TableCell>
        <TableCell><span className="font-mono text-sm">{level.sortOrder}</span></TableCell>
        <TableCell><Badge variant="outline" className={cn(level.active ? 'border-success/30 bg-success/10 text-success' : 'text-muted-foreground')}>{level.active ? '启用' : '停用'}</Badge>{!level.active && <p className="mt-1 text-[11px] text-muted-foreground">不用于新申请</p>}</TableCell>
        {canEdit && <TableCell><div className="flex justify-end gap-2"><Button variant="outline" size="sm" onClick={() => openEdit(level)}><Edit3 className="size-3.5" />编辑</Button>{canDelete && <Button variant="ghost" size="sm" disabled={deleteReview} className="text-destructive hover:text-destructive" onClick={() => openDelete(level)}><Trash2 className="size-3.5" />删除</Button>}</div></TableCell>}
      </TableRow>)}</TableBody></Table>{rows.length === 0 && <div className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><Award className="size-7 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">当前状态下没有等级</h2><p className="mt-2 text-sm text-muted-foreground">{canEdit ? '切换状态筛选，或新增一项等级配置。' : '切换状态筛选以查看其他等级。'}</p><Button variant="outline" className="mt-5" onClick={() => { setStatus('ALL'); onScenarioChange('default') }}>查看全部等级</Button></div>}</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>等级与关联数量均为示例，不连接真实配置。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    <Dialog open={formOpen} onOpenChange={open => { if (!open) closeForm() }}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>{form.id ? '编辑陪玩等级' : '新增陪玩等级'}</DialogTitle><DialogDescription>设置等级名称，以及部分业务使用的抽成比例。</DialogDescription></DialogHeader><div className="grid gap-4 md:grid-cols-2">
      <div className="space-y-2"><Label htmlFor="level-name">等级名称</Label><Input id="level-name" value={form.name} onChange={event => updateForm('name', event.target.value)} maxLength={50} placeholder="如：金牌陪玩" className="h-11" /></div>
      <div className="space-y-2"><Label htmlFor="level-rate">等级抽成率（%）</Label><Input id="level-rate" type="number" min="0" max="100" step="1" value={form.commissionRate} onChange={event => updateForm('commissionRate', event.target.value)} className="h-11" /><p className="text-[11px] text-muted-foreground">0–100 的整数，具体订单以实际规则为准。</p></div>
      <div className="space-y-2 md:col-span-2"><Label htmlFor="level-remark">备注</Label><Textarea id="level-remark" value={form.remark} onChange={event => updateForm('remark', event.target.value)} maxLength={255} placeholder="说明等级适用范围，不影响订单结算" /></div>
      <div className="space-y-2"><Label htmlFor="level-sort">排序</Label><Input id="level-sort" type="number" min="0" step="1" value={form.sortOrder} onChange={event => updateForm('sortOrder', event.target.value)} className="h-11" /><p className="text-[11px] text-muted-foreground">列表按数值升序；旧版最低等级资格也按此判断档位高低。</p></div>
      <label className="flex min-h-11 items-center justify-between gap-3 self-start rounded-xl border px-3"><span><span className="block text-xs font-medium">启用等级</span><span className="mt-1 block text-[11px] text-muted-foreground">停用后新申请不可选，旧版等级资格也不生效</span></span><Switch aria-label="启用等级" checked={form.active} onCheckedChange={value => updateForm('active', value)} /></label>
    </div>{saveError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{saveError}</AlertDescription></Alert>}<DialogFooter><Button variant="outline" onClick={closeForm}>关闭</Button><Button disabled={saveUnknown} onClick={saveLevel}>保存等级</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={!!deleteTarget} onOpenChange={open => { if (!open) setDeleteTarget(null) }}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg"><DialogHeader><DialogTitle>删除陪玩等级</DialogTitle><DialogDescription>删除前必须读取并核对当前关联情况。</DialogDescription></DialogHeader>{impactLoading ? <div className="space-y-3" aria-label="关联情况读取中"><Skeleton className="h-20 rounded-xl" /><Skeleton className="h-28 rounded-xl" /></div> : impactError ? <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>关联情况读取失败</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">请重新读取后再删除。<Button variant="link" className="h-auto p-0" onClick={retryImpact}>重新读取</Button></AlertDescription></Alert> : deleteTarget && <><div className="rounded-xl border bg-muted/30 p-4"><p className="text-sm">删除「<strong>{deleteTarget.name}</strong>」？</p><p className="mt-2 text-xs text-muted-foreground">关联数量为本次读取时的示例快照。</p><div className="mt-4 grid grid-cols-3 gap-2">{[[deleteTarget.impact.providers, '陪玩档案'], [deleteTarget.impact.services, '服务商品'], [deleteTarget.impact.registrations, '注册申请']].map(([value, label]) => <div key={label} className="rounded-lg border bg-card p-3 text-center"><Link2 className="mx-auto size-4 text-primary" /><p className="mt-2 text-xl font-semibold tabular-nums">{value}</p><p className="mt-1 text-[11px] text-muted-foreground">{label}</p></div>)}</div></div><Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>删除会清空这些关联的等级字段</AlertTitle><AlertDescription>商品最低等级被清空后，未设置分范围资格的商品将不再按该最低等级限制接单。历史订单仍按自身记录计算。</AlertDescription></Alert></>}<DialogFooter><Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button><Button variant="destructive" disabled={impactLoading || impactError || !deleteTarget || deleteReview} onClick={confirmDelete}><Trash2 className="size-4" />确认删除</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={releaseOpen} onOpenChange={setReleaseOpen}><DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle>解除删除限制</DialogTitle><DialogDescription>读取只代表当前状态，原请求可能仍在处理中。请确认已核对等级及关联。</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={() => setReleaseOpen(false)}>继续核对</Button><Button onClick={() => { setReleaseOpen(false); onScenarioChange('default'); setFeedback('已解除原型删除限制，请继续核对当前等级列表。') }}>已核对，解除限制</Button></DialogFooter></DialogContent></Dialog>

    {feedback && <div role="status" className="fixed bottom-5 right-5 z-50 flex max-w-md items-start gap-3 rounded-xl border bg-card p-4 text-sm shadow-2xl"><CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /><span className="leading-6">{feedback}</span><Button variant="ghost" size="icon" className="-mr-2 -mt-2 size-8" onClick={() => setFeedback('')} aria-label="关闭反馈"><X className="size-4" /></Button></div>}
  </div>
}
