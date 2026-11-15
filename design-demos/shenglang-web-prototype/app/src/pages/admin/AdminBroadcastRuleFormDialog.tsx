import { useMemo, useState } from 'react'
import { Award, CircleAlert, Clock3, Eye, Info, Save, ShieldCheck, Smartphone } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { broadcastKindLabels, type AdminBroadcastRuleItem } from '@/pages/admin/admin-broadcast-rule-data'

interface AdminBroadcastRuleFormDialogProps {
  open: boolean
  item: AdminBroadcastRuleItem
  initialError?: string
  saveFailed?: boolean
  saveUnknown?: boolean
  onClose: () => void
  onSave: (item: AdminBroadcastRuleItem) => void
}

interface BroadcastRuleDraft {
  template: string
  enabled: boolean
  visibleHours: string
  sortOrder: string
  maskNickname: boolean
}

const placeholders = [
  { value: '{nickname}', label: '老板昵称' },
  { value: '{reward}', label: '奖品／权益' },
  { value: '{level}', label: '会员等级' },
  { value: '{provider}', label: '陪玩昵称' },
]

export function AdminBroadcastRuleFormDialog({ open, item, initialError = '', saveFailed = false, saveUnknown = false, onClose, onSave }: AdminBroadcastRuleFormDialogProps) {
  const [draft, setDraft] = useState<BroadcastRuleDraft>({ template: item.template, enabled: item.enabled, visibleHours: String(item.visibleHours), sortOrder: String(item.sortOrder), maskNickname: item.maskNickname })
  const [initialSignature] = useState(() => JSON.stringify({ template: item.template, enabled: item.enabled, visibleHours: String(item.visibleHours), sortOrder: String(item.sortOrder), maskNickname: item.maskNickname }))
  const [error, setError] = useState(initialError)
  const [discardOpen, setDiscardOpen] = useState(false)
  const dirty = useMemo(() => JSON.stringify(draft) !== initialSignature, [draft, initialSignature])
  const preview = useMemo(() => {
    const reward = item.kind === 'NAMING' || item.kind === 'LOTTERY_NAMING' ? '星野专属冠名' : item.kind === 'LOTTERY_TAG' ? 'SVIP 专属 TAG' : item.kind === 'GIFT' ? '星河礼赞' : item.kind.startsWith('LOTTERY') ? '1 小时体验单' : '俱乐部权益'
    const values: Record<string, string> = { nickname: draft.maskNickname ? '岚**' : '山海岚', reward, level: 'VIP 4', provider: '星野' }
    return draft.template.replace(/\{(nickname|reward|level|provider)\}/g, key => values[key.slice(1, -1)]).slice(0, 255)
  }, [draft.template, draft.maskNickname, item.kind])

  function update(next: Partial<BroadcastRuleDraft>) {
    setDraft(current => ({ ...current, ...next }))
    setError('')
  }

  function requestClose() {
    if (dirty && !saveUnknown) setDiscardOpen(true)
    else onClose()
  }

  function validate() {
    if (!draft.template.trim()) return '请填写播报内容模板'
    if (draft.template.length > 255) return '内容模板最多 255 字'
    const tokens = draft.template.match(/\{[^{}]*\}/g) || []
    if (tokens.some(token => !placeholders.some(item => item.value === token)) || /[{}]/.test(draft.template.replace(/\{(nickname|reward|level|provider)\}/g, ''))) return '仅支持完整填写 {nickname}、{reward}、{level}、{provider} 占位符'
    if (!/^\d+$/.test(draft.visibleHours) || Number(draft.visibleHours) < 1 || Number(draft.visibleHours) > 8760) return '展示时长须为 1–8760 小时的整数'
    if (!/^-?\d+$/.test(draft.sortOrder) || !Number.isSafeInteger(Number(draft.sortOrder))) return '排序须为整数，数值越大越靠前'
    return ''
  }

  function save() {
    if (saveUnknown) return
    const message = validate()
    if (message) { setError(message); return }
    if (saveFailed) { setError(initialError); return }
    onSave({ ...item, template: draft.template.trim(), enabled: draft.enabled, visibleHours: Number(draft.visibleHours), sortOrder: Number(draft.sortOrder), maskNickname: draft.maskNickname })
  }

  return <>
    <Dialog open={open} onOpenChange={value => { if (!value) requestClose() }}>
      <DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-5xl" onInteractOutside={event => event.preventDefault()}>
        <DialogHeader><DialogTitle>配置{broadcastKindLabels[item.kind]}</DialogTitle><DialogDescription>事件类型由业务系统固定；修改只影响保存后新产生的俱乐部动态。</DialogDescription></DialogHeader>
        <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground"><span>规则 ID {item.id} · {item.kind}</span>{dirty && !saveUnknown && <span role="status">有未保存的修改</span>}</div>
        <fieldset disabled={saveUnknown} className="min-w-0">
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_310px]">
            <div className="space-y-5">
              <div className="space-y-2"><div className="flex items-center justify-between gap-3"><Label htmlFor="broadcast-template">内容模板</Label><span className="text-[11px] text-muted-foreground">{draft.template.length} / 255</span></div><Textarea id="broadcast-template" value={draft.template} onChange={event => update({ template: event.target.value })} className="min-h-32 resize-y leading-6" placeholder="使用固定占位符描述一条俱乐部动态" /><div className="flex flex-wrap gap-2">{placeholders.map(placeholder => <Button key={placeholder.value} type="button" variant="outline" size="sm" onClick={() => update({ template: `${draft.template}${placeholder.value}` })}>{placeholder.label}<span className="font-mono text-[10px] text-muted-foreground">{placeholder.value}</span></Button>)}</div></div>
              <div className="grid gap-4 sm:grid-cols-2"><div className="space-y-2"><Label htmlFor="broadcast-hours">展示时长（小时）</Label><Input id="broadcast-hours" value={draft.visibleHours} inputMode="numeric" onChange={event => update({ visibleHours: event.target.value.trim() })} placeholder="1–8760" /><p className="text-[11px] text-muted-foreground">到期后自动退出老板端动态列表。</p></div><div className="space-y-2"><Label htmlFor="broadcast-sort">动态排序</Label><Input id="broadcast-sort" value={draft.sortOrder} inputMode="numeric" onChange={event => update({ sortOrder: event.target.value.trim() })} placeholder="整数，越大越靠前" /><p className="text-[11px] text-muted-foreground">只决定新动态的初始排序值。</p></div></div>
              <div className="grid gap-3 sm:grid-cols-2"><div className="flex items-start justify-between gap-4 rounded-xl border p-4"><div><Label htmlFor="broadcast-mask" className="flex items-center gap-1.5"><ShieldCheck className="size-3.5" />昵称脱敏</Label><p className="mt-1 text-[11px] leading-5 text-muted-foreground">保留首字，其余显示为 **。</p></div><Switch id="broadcast-mask" checked={draft.maskNickname} onCheckedChange={value => update({ maskNickname: value })} /></div><div className="flex items-start justify-between gap-4 rounded-xl border p-4"><div><Label htmlFor="broadcast-enabled">启用规则</Label><p className="mt-1 text-[11px] leading-5 text-muted-foreground">停用后不再生成此类新动态。</p></div><Switch id="broadcast-enabled" checked={draft.enabled} onCheckedChange={value => update({ enabled: value })} /></div></div>
              <Alert><Info className="size-4" /><AlertDescription>模板、脱敏、时长和排序不会回写已有动态。特定业务事件本身要求匿名时，即使关闭昵称脱敏，也可能显示「一位老板」。</AlertDescription></Alert>
            </div>
            <aside className="space-y-3">
              <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground"><Smartphone className="size-4" />老板端俱乐部动态预览</div>
              <div className="overflow-hidden rounded-2xl border bg-card shadow-sm"><div className="border-b bg-[#10285b] p-5 text-white"><div className="flex items-center justify-between gap-3"><span className="text-[11px] text-blue-100/80">声浪电竞 · 俱乐部动态</span><Badge className="border-white/20 bg-white/10 text-[10px] text-white hover:bg-white/10">示例</Badge></div><h3 className="mt-4 text-xl font-semibold tracking-tight">{broadcastKindLabels[item.kind]}</h3></div><div className="p-4"><div className="flex items-start gap-3 rounded-xl border bg-muted/30 p-4"><span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary"><Award className="size-4" /></span><div className="min-w-0"><div className="flex items-center justify-between gap-2"><span className="text-xs font-semibold text-primary">{broadcastKindLabels[item.kind]}</span><span className="text-[10px] text-muted-foreground">刚刚</span></div><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6">{preview || '填写模板后预览播报内容'}</p></div></div><div className="mt-4 grid grid-cols-2 gap-2 text-[11px] text-muted-foreground"><span className="flex items-center gap-1.5"><Clock3 className="size-3.5" />展示 {draft.visibleHours || '—'} 小时</span><span className="flex items-center gap-1.5"><Eye className="size-3.5" />{draft.enabled ? '新动态已启用' : '新动态已停用'}</span></div></div></div>
              <p className="text-[11px] leading-5 text-muted-foreground">预览使用本地示例昵称、奖品和陪玩，不生成真实动态，也不发送消息。</p>
            </aside>
          </div>
        </fieldset>
        {error && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{error}</AlertDescription></Alert>}
        <DialogFooter><Button variant="outline" onClick={requestClose}>{saveUnknown ? '关闭并核对' : '取消'}</Button><Button onClick={save} disabled={saveUnknown}><Save className="size-4" />保存规则</Button></DialogFooter>
      </DialogContent>
    </Dialog>
    <AlertDialog open={discardOpen} onOpenChange={setDiscardOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>放弃未保存的修改？</AlertDialogTitle><AlertDialogDescription>当前模板、展示时长、排序、昵称脱敏和启停修改尚未保存。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续编辑</AlertDialogCancel><AlertDialogAction onClick={() => { setDiscardOpen(false); onClose() }}>放弃修改</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </>
}
