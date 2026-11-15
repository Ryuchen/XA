import { useMemo, useState } from 'react'
import { ArrowDownWideNarrow, CircleAlert, Clock3, Edit3, Info, Megaphone, RotateCcw, Search, ShieldAlert, ShieldCheck, SlidersHorizontal } from 'lucide-react'
import type { AdminBroadcastRulesScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { AdminBroadcastRuleFormDialog } from '@/pages/admin/AdminBroadcastRuleFormDialog'
import { broadcastKindLabels, initialAdminBroadcastRules, type AdminBroadcastRuleItem } from '@/pages/admin/admin-broadcast-rule-data'
import { cn } from '@/lib/utils'

interface AdminBroadcastRulesPageProps {
  scenario: AdminBroadcastRulesScenario
  onScenarioChange: (scenario: AdminBroadcastRulesScenario) => void
}

export function AdminBroadcastRulesPage({ scenario, onScenarioChange }: AdminBroadcastRulesPageProps) {
  const [rules, setRules] = useState<AdminBroadcastRuleItem[]>(initialAdminBroadcastRules)
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [kindFilter, setKindFilter] = useState('ALL')
  const [statusFilter, setStatusFilter] = useState('ALL')
  const opensForm = scenario === 'save-error' || scenario === 'save-review'
  const [formOpen, setFormOpen] = useState(opensForm)
  const [editing, setEditing] = useState<AdminBroadcastRuleItem>(initialAdminBroadcastRules[0])
  const [saveReviewActive, setSaveReviewActive] = useState(false)
  const [reviewSearched, setReviewSearched] = useState(false)
  const [releaseOpen, setReleaseOpen] = useState(false)
  const [notice, setNotice] = useState('')
  const [actionError, setActionError] = useState('')
  const canEdit = scenario !== 'readonly'
  const operationsLocked = saveReviewActive || scenario === 'refresh-error'
  const saveUnknown = scenario === 'save-review' && formOpen
  const enabledCount = rules.filter(item => item.enabled).length
  const maskedCount = rules.filter(item => item.maskNickname).length
  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    return rules.filter(item => {
      if (keyword && !`${item.id} ${item.kind} ${broadcastKindLabels[item.kind]} ${item.template}`.toLocaleLowerCase().includes(keyword.toLocaleLowerCase())) return false
      if (kindFilter !== 'ALL' && item.kind !== kindFilter) return false
      if (statusFilter === 'ENABLED' && !item.enabled) return false
      if (statusFilter === 'DISABLED' && item.enabled) return false
      return true
    })
  }, [rules, scenario, keyword, kindFilter, statusFilter])

  function applySearch() { setKeyword(keywordInput.trim()) }
  function clearFilters() {
    setKeywordInput(''); setKeyword(''); setKindFilter('ALL'); setStatusFilter('ALL')
    if (scenario === 'empty') onScenarioChange('default')
  }
  function openEdit(item: AdminBroadcastRuleItem) {
    if (!canEdit || operationsLocked) return
    setEditing(item); setFormOpen(true); setNotice(''); setActionError('')
  }
  function closeForm() {
    setFormOpen(false)
    if (saveUnknown) setSaveReviewActive(true)
  }
  function saveRule(item: AdminBroadcastRuleItem) {
    setRules(current => current.map(rule => rule.id === item.id ? item : rule))
    setFormOpen(false)
    setNotice(`「${broadcastKindLabels[item.kind]}」已在本地示例中保存；已有播报记录未修改。`)
  }
  function toggleRule(item: AdminBroadcastRuleItem, enabled: boolean) {
    if (!canEdit || operationsLocked) return
    if (scenario === 'action-error') { setActionError(`「${broadcastKindLabels[item.kind]}」状态更新失败，请重试；当前仍保持${item.enabled ? '启用' : '停用'}。`); return }
    setRules(current => current.map(rule => rule.id === item.id ? { ...rule, enabled } : rule))
    setNotice(`「${broadcastKindLabels[item.kind]}」已${enabled ? '启用，新事件可生成动态' : '停用，不再生成此类新动态'}；历史动态保持不变。`)
    setActionError('')
  }
  function locateReview() {
    setKeywordInput(broadcastKindLabels[editing.kind]); setKeyword(broadcastKindLabels[editing.kind]); setKindFilter('ALL'); setStatusFilter('ALL'); setReviewSearched(true)
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="播报规则加载中"><Skeleton className="h-32 rounded-xl" /><Skeleton className="h-24 rounded-xl" /><Skeleton className="h-[560px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">播报规则加载失败</CardTitle><CardDescription className="mt-2">当前没有可靠规则快照，不展示推测模板、顺序或启停状态。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm"><div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6"><div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">播报规则</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">俱乐部动态生成规则</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">统一配置礼物、充值、升级和抽奖等事件的展示文案与隐私规则。</p></div><dl className="grid grid-cols-3 gap-4"><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{rules.length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">固定事件</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{enabledCount}</dd><dt className="mt-1 text-[11px] text-blue-100/65">启用规则</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{maskedCount}</dd><dt className="mt-1 text-[11px] text-blue-100/65">昵称脱敏</dt></div></dl></div></section>

    {scenario === 'readonly' && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前为仅查看权限</AlertTitle><AlertDescription>可筛选并阅读播报规则；配置与启停需要 engagement:edit 权限。</AlertDescription></Alert>}
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>规则刷新失败，当前显示上次成功读取的快照</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">配置与启停已暂时锁定。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {actionError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>规则操作未完成</AlertTitle><AlertDescription>{actionError}</AlertDescription></Alert>}
    {saveReviewActive && !formOpen && <Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>规则保存结果待核对</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">「{broadcastKindLabels[editing.kind]}」的保存结果未确认，请查询并核实，避免重复提交。<Button variant="link" className="h-auto p-0" onClick={locateReview}>查询规则</Button><Button variant="link" className="h-auto p-0" disabled={!reviewSearched} onClick={() => setReleaseOpen(true)}>已核对，解除限制</Button></AlertDescription></Alert>}
    {notice && !formOpen && <Alert role="status" className="border-success/30 bg-success/10"><Info className="size-4 text-success" /><AlertDescription>{notice}</AlertDescription></Alert>}
    <Alert className="border-primary/25 bg-primary/5"><Info className="size-4 text-primary" /><AlertTitle>这里配置文字动态，不控制客服提示音</AlertTitle><AlertDescription>10 类事件由业务系统固定，不提供新增或删除。停用仅阻止新动态生成；已生成内容请到播报记录单独管理。</AlertDescription></Alert>

    <Card className="min-w-0 overflow-hidden"><CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0"><div><CardTitle className="flex items-center gap-2 text-base"><Megaphone className="size-4 text-primary" />事件规则目录</CardTitle><CardDescription className="mt-1">本页 {rows.length} 项 · 按固定事件排列，动态列表按排序值倒序</CardDescription></div><Badge variant="outline" className="px-3 py-1.5"><ArrowDownWideNarrow className="size-3.5" />数值越大越靠前</Badge></CardHeader>
      <div data-admin-filter className="grid gap-3 border-t p-4 xl:grid-cols-[minmax(240px,1fr)_220px_180px_auto]"><div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="播报事件、模板或编号" placeholder="搜索事件、模板或规则编号" value={keywordInput} onChange={event => setKeywordInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') applySearch() }} className="pl-9" /></div><Select value={kindFilter} onValueChange={setKindFilter}><SelectTrigger aria-label="播报事件筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部事件</SelectItem>{Object.entries(broadcastKindLabels).map(([kind, label]) => <SelectItem key={kind} value={kind}>{label}</SelectItem>)}</SelectContent></Select><Select value={statusFilter} onValueChange={setStatusFilter}><SelectTrigger aria-label="播报规则状态筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部状态</SelectItem><SelectItem value="ENABLED">仅启用</SelectItem><SelectItem value="DISABLED">仅停用</SelectItem></SelectContent></Select><Button className="min-w-20 whitespace-nowrap" onClick={applySearch}><Search className="size-4" />查询</Button></div>
      <div className="overflow-x-auto border-t"><Table className="min-w-[1040px]"><TableHeader><TableRow><TableHead>播报事件</TableHead><TableHead>内容模板</TableHead><TableHead>展示时长</TableHead><TableHead>排序</TableHead><TableHead>昵称保护</TableHead><TableHead>状态</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader><TableBody>{rows.map(item => <TableRow key={item.id}><TableCell><div className="min-w-36"><div className="flex items-center gap-2"><h3 className="text-sm font-semibold">{broadcastKindLabels[item.kind]}</h3><span className="font-mono text-[10px] text-muted-foreground">#{item.id}</span></div><p className="mt-1 font-mono text-[10px] text-muted-foreground">{item.kind}</p></div></TableCell><TableCell><p className="max-w-[390px] whitespace-pre-wrap break-words text-xs leading-5 text-muted-foreground">{item.template}</p></TableCell><TableCell><span className="flex items-center gap-1.5 text-xs tabular-nums"><Clock3 className="size-3.5 text-muted-foreground" />{item.visibleHours} 小时</span></TableCell><TableCell><span className="font-mono text-xs">{item.sortOrder}</span></TableCell><TableCell>{item.maskNickname ? <Badge variant="secondary"><ShieldCheck className="size-3" />脱敏</Badge> : <span className="text-xs text-muted-foreground">完整昵称</span>}</TableCell><TableCell><div className="flex items-center gap-2"><Switch aria-label={`启停 ${broadcastKindLabels[item.kind]}`} checked={item.enabled} disabled={!canEdit || operationsLocked} onCheckedChange={value => toggleRule(item, value)} /><span className={cn('text-xs', item.enabled ? 'text-success' : 'text-muted-foreground')}>{item.enabled ? '启用' : '停用'}</span></div></TableCell><TableCell><div className="flex justify-end">{canEdit ? <Button variant="outline" size="sm" disabled={operationsLocked} onClick={() => openEdit(item)}><Edit3 className="size-3.5" />配置</Button> : <span className="text-xs text-muted-foreground">仅查看</span>}</div></TableCell></TableRow>)}</TableBody></Table>{rows.length === 0 && <div className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><SlidersHorizontal className="size-8 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有匹配的播报规则</h2><p className="mt-2 text-sm text-muted-foreground">清除关键词、事件和状态筛选后查看固定规则目录。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除筛选</Button></div>}</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>规则、预览和保存结果均为本地示例，不生成真实动态。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    {formOpen && <AdminBroadcastRuleFormDialog key={`${editing.id}-${formOpen}`} open={formOpen} item={editing} initialError={scenario === 'save-error' ? '保存未完成：请求被拒绝，请核对输入和权限。' : scenario === 'save-review' ? '保存结果未确认。请关闭并核对规则后再操作，避免重复提交。' : ''} saveFailed={scenario === 'save-error'} saveUnknown={saveUnknown} onClose={closeForm} onSave={saveRule} />}
    <AlertDialog open={releaseOpen} onOpenChange={setReleaseOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>解除规则保存限制？</AlertDialogTitle><AlertDialogDescription>请确认已核对当前规则及请求处理情况，原请求可能仍在处理。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续核对</AlertDialogCancel><AlertDialogAction onClick={() => { setReleaseOpen(false); setSaveReviewActive(false); onScenarioChange('default') }}>已核对，解除限制</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>
}
