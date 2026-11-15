import { useMemo, useState } from 'react'
import { BadgePercent, CalendarClock, Check, CircleAlert, Coins, Crown, Info, RotateCcw, Save, Settings2, ShieldAlert, Sparkles } from 'lucide-react'
import type { AdminOperationConfigScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { formatAdminOperationTime, initialAdminOperationConfig, type AdminOperationConfig } from '@/pages/admin/admin-operation-config-data'

interface AdminOperationConfigPageProps {
  scenario: AdminOperationConfigScenario
  onScenarioChange: (scenario: AdminOperationConfigScenario) => void
}

interface OperationConfigDraft {
  pointsEnabled: boolean
  birthdayMultiplier: string
  vipEnabled: boolean
  seasonCode: string
  seasonEndsAt: string
}

function toDraft(config: AdminOperationConfig): OperationConfigDraft {
  return {
    pointsEnabled: config.pointsEnabled,
    birthdayMultiplier: String(config.birthdayMultiplier),
    vipEnabled: config.vipEnabled,
    seasonCode: config.seasonCode,
    seasonEndsAt: config.seasonEndsAt ? config.seasonEndsAt.slice(0, 16) : '',
  }
}

export function AdminOperationConfigPage({ scenario, onScenarioChange }: AdminOperationConfigPageProps) {
  const [saved, setSaved] = useState<AdminOperationConfig>(initialAdminOperationConfig)
  const [draft, setDraft] = useState<OperationConfigDraft>(() => toDraft(initialAdminOperationConfig))
  const [previewBirthday, setPreviewBirthday] = useState(true)
  const [error, setError] = useState(scenario === 'save-error' ? '保存未完成：请求被拒绝，请核对输入和 engagement:edit 权限。' : '')
  const [notice, setNotice] = useState('')
  const [saveOpen, setSaveOpen] = useState(false)
  const [resetOpen, setResetOpen] = useState(false)
  const [saveReviewActive] = useState(scenario === 'save-review')
  const [reviewSearched, setReviewSearched] = useState(false)
  const [releaseOpen, setReleaseOpen] = useState(false)
  const canEdit = scenario !== 'readonly'
  const saving = scenario === 'saving'
  const operationsLocked = !canEdit || saving || saveReviewActive || scenario === 'refresh-error'
  const dirty = useMemo(() => {
    const before = toDraft(saved)
    return draft.pointsEnabled !== before.pointsEnabled
      || draft.vipEnabled !== before.vipEnabled
      || draft.seasonCode.trim() !== before.seasonCode
      || draft.seasonEndsAt !== before.seasonEndsAt
      || Number(draft.birthdayMultiplier) !== Number(before.birthdayMultiplier)
  }, [draft, saved])
  const multiplier = /^\d+$/.test(draft.birthdayMultiplier) ? Number(draft.birthdayMultiplier) : 0
  const previewPoints = draft.pointsEnabled ? 268 * (previewBirthday ? multiplier : 1) : 0
  const pendingChanges = useMemo(() => {
    const before = toDraft(saved)
    const changes: { label: string; before: string; after: string }[] = []
    if (draft.pointsEnabled !== before.pointsEnabled) changes.push({ label: '消费积分', before: before.pointsEnabled ? '开启' : '关闭', after: draft.pointsEnabled ? '开启' : '关闭' })
    if (Number(draft.birthdayMultiplier) !== Number(before.birthdayMultiplier)) changes.push({ label: '生日消费倍率', before: `${before.birthdayMultiplier} 倍`, after: `${draft.birthdayMultiplier || '未填写'} 倍` })
    if (draft.vipEnabled !== before.vipEnabled) changes.push({ label: 'VIP 自动升级', before: before.vipEnabled ? '开启' : '关闭', after: draft.vipEnabled ? '开启' : '关闭' })
    if (draft.seasonCode.trim() !== before.seasonCode) changes.push({ label: '当前赛季编号', before: before.seasonCode || '未设置', after: draft.seasonCode.trim() || '未设置' })
    if (draft.seasonEndsAt !== before.seasonEndsAt) changes.push({ label: '赛季截止时间', before: before.seasonEndsAt ? formatAdminOperationTime(before.seasonEndsAt) : '未设置', after: draft.seasonEndsAt ? formatAdminOperationTime(draft.seasonEndsAt) : '未设置' })
    return changes
  }, [draft, saved])

  function update(next: Partial<OperationConfigDraft>) {
    setDraft(current => ({ ...current, ...next }))
    setError('')
    setNotice('')
  }

  function validate() {
    if (!/^\d+$/.test(draft.birthdayMultiplier) || Number(draft.birthdayMultiplier) < 1 || Number(draft.birthdayMultiplier) > 10) return '生日消费倍率须为 1–10 的整数'
    if (draft.seasonCode.trim().length > 40) return '当前赛季编号最多 40 个字符'
    if (draft.seasonCode.trim() && !draft.seasonEndsAt) return '配置赛季编号时，请同时设置赛季结束时间'
    if (draft.seasonEndsAt && (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(draft.seasonEndsAt) || Number.isNaN(new Date(`${draft.seasonEndsAt}:00`).getTime()))) return '赛季截止时间格式不正确，请重新选择'
    return ''
  }

  function requestSave() {
    if (operationsLocked || !dirty) return
    const message = validate()
    if (message) { setError(message); return }
    setError('')
    setSaveOpen(true)
  }

  function save() {
    setSaveOpen(false)
    if (operationsLocked || !dirty) return
    if (scenario === 'save-error') { setError('保存未完成：请求被拒绝，请核对输入和 engagement:edit 权限。'); return }
    const next: AdminOperationConfig = {
      pointsEnabled: draft.pointsEnabled,
      birthdayMultiplier: Number(draft.birthdayMultiplier),
      vipEnabled: draft.vipEnabled,
      seasonCode: draft.seasonCode.trim(),
      seasonEndsAt: draft.seasonEndsAt ? `${draft.seasonEndsAt}:00` : '',
      updatedAt: '2026-11-04T12:00:00',
    }
    setSaved(next)
    setDraft(toDraft(next))
    setError('')
    setNotice('运营设置已在本地示例中保存；历史积分、会员与订单记录未重算。')
  }

  function resetDraft() {
    setDraft(toDraft(saved))
    setError('')
    setNotice('')
    setResetOpen(false)
  }

  function reviewConfig() {
    setDraft(toDraft(saved))
    setReviewSearched(true)
    setError('')
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="运营设置加载中"><Skeleton className="h-32 rounded-xl" /><Skeleton className="h-24 rounded-xl" /><div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]"><Skeleton className="h-[560px] rounded-xl" /><Skeleton className="h-[560px] rounded-xl" /></div></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">运营设置加载失败</CardTitle><CardDescription className="mt-2">当前没有可靠配置快照，不展示推测开关、倍率或赛季信息。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm">
      <div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
        <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">运营设置</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">积分 · VIP · 赛季</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">统一维护会员激励与赛季配置，保存前清楚核对新订单和新业务事件的影响。</p></div>
        <div className="border-l border-white/15 pl-4"><p className="text-[11px] text-blue-100/65">配置快照更新于</p><p className="mt-1 text-sm font-medium tabular-nums">{formatAdminOperationTime(saved.updatedAt)}</p><p className="mt-1 text-[11px] text-blue-100/65">单例配置 · 本地示例</p></div>
      </div>
    </section>

    {scenario === 'readonly' && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前为仅查看权限</AlertTitle><AlertDescription>可读取配置与订单积分试算；保存运营设置需要 engagement:edit 权限。</AlertDescription></Alert>}
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>配置刷新失败，当前显示上次成功读取的快照</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">保存与重置已暂时锁定。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {saveReviewActive && <Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>运营设置保存结果待核对</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">保存结果未确认，请重新读取并核对配置，避免重复提交。<Button variant="link" className="h-auto p-0" onClick={reviewConfig}>重新读取配置</Button><Button variant="link" className="h-auto p-0" disabled={!reviewSearched} onClick={() => setReleaseOpen(true)}>已核对，解除限制</Button></AlertDescription></Alert>}
    {error && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>配置尚未保存</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <Alert role="status" className="border-success/30 bg-success/10"><Check className="size-4 text-success" /><AlertDescription>{notice}</AlertDescription></Alert>}
    <Alert className="border-primary/25 bg-primary/5"><Info className="size-4 text-primary" /><AlertTitle>消费与优惠规则保持互斥</AlertTitle><AlertDescription>仅消费产生积分，充值本身不加分；已完成订单的折后实付累计 VIP，取消和退款不计入；VIP 折扣与折扣券取优，不叠加。</AlertDescription></Alert>

    <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <fieldset disabled={operationsLocked} className="flex min-w-0 flex-col gap-4">
          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Coins className="size-4 text-primary" />积分与会员</CardTitle><CardDescription>控制新订单和新履约事件的会员激励，不回写历史账务。</CardDescription></CardHeader>
            <CardContent className="space-y-0 pt-0">
              <div className="flex items-start justify-between gap-6 py-4">
                <div><Label htmlFor="operation-points" className="text-sm">消费积分</Label><p className="mt-1.5 max-w-xl text-xs leading-5 text-muted-foreground">关闭后不再累积新的消费积分；已有积分余额和流水仍保留。</p></div>
                <div className="flex items-center gap-2"><span className="text-xs text-muted-foreground">{draft.pointsEnabled ? '已开启' : '已关闭'}</span><Switch id="operation-points" checked={draft.pointsEnabled} onCheckedChange={value => update({ pointsEnabled: value })} /></div>
              </div>
              <Separator />
              <div className="grid gap-4 py-5 sm:grid-cols-[minmax(0,1fr)_180px] sm:items-center">
                <div><Label htmlFor="operation-birthday" className="text-sm">生日消费倍率</Label><p className="mt-1.5 text-xs leading-5 text-muted-foreground">老板生日当天的新订单按该整数倍率计算，范围为 1–10 倍。</p></div>
                <div className="relative"><Input id="operation-birthday" value={draft.birthdayMultiplier} inputMode="numeric" onChange={event => update({ birthdayMultiplier: event.target.value.trim() })} className="pr-10 text-right tabular-nums" /><span className="pointer-events-none absolute right-3 top-2.5 text-sm text-muted-foreground">倍</span></div>
              </div>
              <Separator />
              <div className="flex items-start justify-between gap-6 py-4">
                <div><Label htmlFor="operation-vip" className="flex items-center gap-1.5 text-sm"><Crown className="size-3.5 text-primary" />VIP 自动升级</Label><p className="mt-1.5 max-w-xl text-xs leading-5 text-muted-foreground">按当前业务周期内已完成订单的折后实付核对启用等级；关闭后不执行自动升级与周期权益发放。</p></div>
                <div className="flex items-center gap-2"><span className="text-xs text-muted-foreground">{draft.vipEnabled ? '已开启' : '已关闭'}</span><Switch id="operation-vip" checked={draft.vipEnabled} onCheckedChange={value => update({ vipEnabled: value })} /></div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2 text-base"><CalendarClock className="size-4 text-primary" />当前赛季</CardTitle><CardDescription>赛季编号可留空；填写编号时必须同时设置截止时间。</CardDescription></CardHeader>
            <CardContent className="grid gap-4 pt-0 sm:grid-cols-2">
              <div className="space-y-2"><Label htmlFor="operation-season">赛季编号</Label><Input id="operation-season" value={draft.seasonCode} maxLength={41} onChange={event => update({ seasonCode: event.target.value })} placeholder="例如 S8" /><p className="text-[11px] text-muted-foreground">{draft.seasonCode.length} / 40 字符</p></div>
              <div className="space-y-2"><Label htmlFor="operation-season-end">赛季截止时间</Label><Input id="operation-season-end" type="datetime-local" value={draft.seasonEndsAt} onChange={event => update({ seasonEndsAt: event.target.value })} /><p className="text-[11px] text-muted-foreground">保存时按当前运营时区解释。</p></div>
            </CardContent>
          </Card>
        </fieldset>

        <aside className="flex min-w-0 flex-col gap-4">
          <Card className="overflow-hidden">
            <div className="bg-[#10285b] p-5 text-white"><div className="flex items-center justify-between gap-3"><span className="text-xs text-blue-100/75">新订单积分试算</span><Badge className="border-white/20 bg-white/10 text-[10px] text-white hover:bg-white/10">示例</Badge></div><div className="mt-5 flex items-end gap-2"><strong className="text-4xl font-semibold tracking-[-0.04em] tabular-nums">{previewPoints}</strong><span className="pb-1 text-sm text-blue-100/75">积分</span></div><p className="mt-2 text-xs text-blue-100/70">示例订单实付 ¥268.00，履约完成后发放</p></div>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between gap-4"><Label htmlFor="operation-preview-birthday" className="text-xs">生日当天示例</Label><Switch id="operation-preview-birthday" checked={previewBirthday} onCheckedChange={setPreviewBirthday} /></div>
              <dl className="space-y-2.5 text-xs"><div className="flex justify-between gap-4"><dt className="text-muted-foreground">基础消费积分（示例充值批次 1 倍）</dt><dd className="tabular-nums">268</dd></div><div className="flex justify-between gap-4"><dt className="text-muted-foreground">生日倍率</dt><dd className="tabular-nums">{previewBirthday ? `${multiplier || '—'} 倍` : '不应用'}</dd></div><div className="flex justify-between gap-4"><dt className="text-muted-foreground">消费积分开关</dt><dd>{draft.pointsEnabled ? '开启' : '关闭'}</dd></div></dl>
              <p className="border-t pt-3 text-[11px] leading-5 text-muted-foreground">仅演示当前配置下的新订单，不产生真实积分，也不代表充值或历史订单。</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2 text-base"><BadgePercent className="size-4 text-primary" />保存前核对</CardTitle><CardDescription>当前配置对老板端的业务含义</CardDescription></CardHeader>
            <CardContent className="space-y-4 pt-0 text-xs leading-5">
              <div className="flex gap-3"><Sparkles className="mt-0.5 size-4 shrink-0 text-primary" /><p>消费积分{draft.pointsEnabled ? '开启' : '关闭'}，生日订单按 {multiplier || '—'} 倍试算。</p></div>
              <div className="flex gap-3"><Crown className="mt-0.5 size-4 shrink-0 text-primary" /><p>VIP 自动升级{draft.vipEnabled ? '开启，达到启用等级门槛时执行' : '关闭，不自动升级或发放周期权益'}。</p></div>
              <div className="flex gap-3"><Settings2 className="mt-0.5 size-4 shrink-0 text-primary" /><p>{draft.seasonCode.trim() ? `当前赛季 ${draft.seasonCode.trim()}，截止 ${draft.seasonEndsAt ? formatAdminOperationTime(draft.seasonEndsAt) : '未设置'}。` : '未设置当前赛季编号。'}</p></div>
            </CardContent>
          </Card>
        </aside>
    </div>

    <section className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card p-4 shadow-sm">
      <div><p className="text-sm font-medium">{!canEdit ? '仅查看配置快照' : saveReviewActive ? '保存结果待核对' : saving ? '正在保存配置' : dirty ? '有未保存的修改' : '配置已与本地快照同步'}</p><p className="mt-1 text-[11px] text-muted-foreground">配置、试算与保存结果均为本地示例，不修改真实会员或订单。</p></div>
      {canEdit && <div className="flex gap-2"><Button variant="outline" disabled={!dirty || operationsLocked} onClick={() => setResetOpen(true)}><RotateCcw className="size-4" />重置</Button><Button disabled={!dirty || operationsLocked} onClick={requestSave}><Save className="size-4" />{saving ? '保存中…' : '保存运营设置'}</Button></div>}
    </section>

    <AlertDialog open={saveOpen} onOpenChange={setSaveOpen}>
      <AlertDialogContent>
        <AlertDialogHeader><AlertDialogTitle>确认保存运营设置？</AlertDialogTitle><AlertDialogDescription>请核对以下 {pendingChanges.length} 项变更。保存只更新本地示例快照，不会重算历史积分、会员或订单记录。</AlertDialogDescription></AlertDialogHeader>
        <div className="overflow-hidden rounded-lg border">
          {pendingChanges.map((change, index) => <div key={change.label} className={`grid grid-cols-[112px_minmax(0,1fr)] gap-x-4 gap-y-1 p-3 text-xs ${index ? 'border-t' : ''}`}><span className="row-span-2 font-medium">{change.label}</span><span className="text-muted-foreground line-through decoration-muted-foreground/40">{change.before}</span><span className="font-medium text-foreground">{change.after}</span></div>)}
        </div>
        <Alert className="border-primary/25 bg-primary/5"><Info className="size-4 text-primary" /><AlertDescription>新配置用于后续业务事件；现有积分余额、历史流水和已完成订单保持不变。</AlertDescription></Alert>
        <AlertDialogFooter><AlertDialogCancel>返回修改</AlertDialogCancel><AlertDialogAction onClick={save}>确认保存</AlertDialogAction></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
    <AlertDialog open={resetOpen} onOpenChange={setResetOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>重置未保存的修改？</AlertDialogTitle><AlertDialogDescription>积分、VIP 与赛季字段将恢复到最近一次成功读取或保存的本地快照。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续编辑</AlertDialogCancel><AlertDialogAction onClick={resetDraft}>确认重置</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <AlertDialog open={releaseOpen} onOpenChange={setReleaseOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>解除运营设置保存限制？</AlertDialogTitle><AlertDialogDescription>请确认已核对当前配置及原请求处理情况，避免重复保存。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续核对</AlertDialogCancel><AlertDialogAction onClick={() => { setReleaseOpen(false); onScenarioChange('default') }}>已核对，解除限制</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>
}
