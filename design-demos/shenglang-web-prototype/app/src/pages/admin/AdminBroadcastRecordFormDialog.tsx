import { useMemo, useState } from 'react'
import { Award, CircleAlert, Clock3, Eye, EyeOff, Info, Save, Smartphone } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { broadcastKindLabels } from '@/pages/admin/admin-broadcast-rule-data'
import { formatAdminBroadcastTime, getAdminBroadcastBlockReason, getAdminBroadcastPublicStatus, type AdminBroadcastRecordItem } from '@/pages/admin/admin-broadcast-record-data'

interface AdminBroadcastRecordFormDialogProps {
  open: boolean
  item: AdminBroadcastRecordItem
  initialError?: string
  saveFailed?: boolean
  saveUnknown?: boolean
  onClose: () => void
  onSave: (item: AdminBroadcastRecordItem) => void
}

interface BroadcastRecordDraft {
  isVisible: boolean
  expiresAt: string
}

export function AdminBroadcastRecordFormDialog({ open, item, initialError = '', saveFailed = false, saveUnknown = false, onClose, onSave }: AdminBroadcastRecordFormDialogProps) {
  const [draft, setDraft] = useState<BroadcastRecordDraft>({ isVisible: item.isVisible, expiresAt: item.expiresAt.slice(0, 16) })
  const [initialSignature] = useState(() => JSON.stringify({ isVisible: item.isVisible, expiresAt: item.expiresAt.slice(0, 16) }))
  const [error, setError] = useState(initialError)
  const [discardOpen, setDiscardOpen] = useState(false)
  const dirty = useMemo(() => JSON.stringify(draft) !== initialSignature, [draft, initialSignature])
  const previewItem = useMemo(() => ({ ...item, isVisible: draft.isVisible, expiresAt: draft.expiresAt ? `${draft.expiresAt}:00` : item.expiresAt }), [draft, item])
  const publicStatus = getAdminBroadcastPublicStatus(previewItem)
  const blockReason = getAdminBroadcastBlockReason(previewItem)

  function update(next: Partial<BroadcastRecordDraft>) {
    setDraft(current => ({ ...current, ...next }))
    setError('')
  }

  function requestClose() {
    if (dirty && !saveUnknown) setDiscardOpen(true)
    else onClose()
  }

  function validate() {
    if (!draft.expiresAt) return '请选择展示截止时间'
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(draft.expiresAt) || Number.isNaN(new Date(`${draft.expiresAt}:00`).getTime())) return '展示截止时间格式不正确，请重新选择'
    return ''
  }

  function save() {
    if (saveUnknown) return
    const message = validate()
    if (message) { setError(message); return }
    if (saveFailed) { setError(initialError); return }
    onSave({ ...item, isVisible: draft.isVisible, expiresAt: `${draft.expiresAt}:00` })
  }

  return <>
    <Dialog open={open} onOpenChange={value => { if (!value) requestClose() }}>
      <DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-5xl" onInteractOutside={event => event.preventDefault()}>
        <DialogHeader>
          <DialogTitle>管理播报记录 #{item.id}</DialogTitle>
          <DialogDescription>正文、事件与时间快照由业务系统生成；这里只调整前台显示和展示截止时间。</DialogDescription>
        </DialogHeader>
        <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
          <span>{item.kind} · 创建于 {formatAdminBroadcastTime(item.createdAt)}</span>
          {dirty && !saveUnknown && <span role="status">有未保存的修改</span>}
        </div>
        <fieldset disabled={saveUnknown} className="min-w-0">
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_310px]">
            <div className="space-y-5">
              <div className="rounded-xl border bg-muted/25 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary">{broadcastKindLabels[item.kind]}</Badge>
                  <span className="font-mono text-[11px] text-muted-foreground">只读正文</span>
                </div>
                <p className="mt-3 whitespace-pre-wrap break-words text-sm font-medium leading-6">{item.content}</p>
              </div>
              <dl className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-xl border p-4"><dt className="text-[11px] text-muted-foreground">发布时间</dt><dd className="mt-2 text-sm tabular-nums">{formatAdminBroadcastTime(item.publishedAt)}</dd></div>
                <div className="rounded-xl border p-4"><dt className="text-[11px] text-muted-foreground">开始展示</dt><dd className="mt-2 text-sm tabular-nums">{formatAdminBroadcastTime(item.startsAt)}</dd></div>
              </dl>
              <div className="space-y-2">
                <Label htmlFor="broadcast-record-expires">展示截止时间</Label>
                <Input id="broadcast-record-expires" type="datetime-local" value={draft.expiresAt} onChange={event => update({ expiresAt: event.target.value })} />
                <p className="text-[11px] leading-5 text-muted-foreground">允许保留或设置已经过去的截止时间；到期后记录仍保留在后台，但不会公开。</p>
              </div>
              <div className="flex items-start justify-between gap-4 rounded-xl border p-4">
                <div><Label htmlFor="broadcast-record-visible">前台显示</Label><p className="mt-1 text-[11px] leading-5 text-muted-foreground">关闭后立即退出公开动态；重新开启仍须满足发布、开始和截止条件。</p></div>
                <Switch id="broadcast-record-visible" checked={draft.isVisible} onCheckedChange={value => update({ isVisible: value })} />
              </div>
              <Alert className={publicStatus === 'PUBLIC' ? 'border-success/30 bg-success/5' : 'border-amber-500/30 bg-amber-500/5'}>
                {publicStatus === 'PUBLIC' ? <Eye className="size-4 text-success" /> : <EyeOff className="size-4 text-amber-600" />}
                <AlertDescription>{publicStatus === 'PUBLIC' ? '按当前修改保存后，这条记录会进入老板端公开动态。' : `按当前修改保存后仍不会公开：${blockReason}。`}</AlertDescription>
              </Alert>
              <Alert><Info className="size-4" /><AlertDescription>这里不能修改正文、事件、发布时间或开始时间，也不能删除记录。播报规则只影响新记录，不会回写此正文。</AlertDescription></Alert>
            </div>
            <aside className="space-y-3">
              <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground"><Smartphone className="size-4" />老板端俱乐部动态预览</div>
              <div className="overflow-hidden rounded-2xl border bg-card shadow-sm">
                <div className="border-b bg-[#10285b] p-5 text-white">
                  <div className="flex items-center justify-between gap-3"><span className="text-[11px] text-blue-100/80">声浪电竞 · 俱乐部动态</span><Badge className="border-white/20 bg-white/10 text-[10px] text-white hover:bg-white/10">示例</Badge></div>
                  <h3 className="mt-4 text-xl font-semibold tracking-tight">{broadcastKindLabels[item.kind]}</h3>
                </div>
                <div className="p-4">
                  {publicStatus === 'PUBLIC' ? <div className="flex items-start gap-3 rounded-xl border bg-muted/30 p-4">
                    <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary"><Award className="size-4" /></span>
                    <div className="min-w-0"><div className="flex items-center justify-between gap-2"><span className="text-xs font-semibold text-primary">{broadcastKindLabels[item.kind]}</span><span className="text-[10px] text-muted-foreground">公开中</span></div><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6">{item.content}</p></div>
                  </div> : <div className="flex min-h-36 flex-col items-center justify-center rounded-xl border border-dashed bg-muted/20 p-5 text-center"><EyeOff className="size-6 text-muted-foreground" /><p className="mt-3 text-sm font-medium">当前不会公开显示</p><p className="mt-1 text-[11px] leading-5 text-muted-foreground">{blockReason}</p></div>}
                  <div className="mt-4 flex items-center gap-1.5 text-[11px] text-muted-foreground"><Clock3 className="size-3.5" />截止 {draft.expiresAt ? formatAdminBroadcastTime(draft.expiresAt) : '未设置'}</div>
                </div>
              </div>
              <p className="text-[11px] leading-5 text-muted-foreground">预览使用固定原型时间 2026-11-04 12:00，不生成真实动态，也不发送消息。</p>
            </aside>
          </div>
        </fieldset>
        {error && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{error}</AlertDescription></Alert>}
        <DialogFooter><Button variant="outline" onClick={requestClose}>{saveUnknown ? '关闭并核对' : '取消'}</Button><Button onClick={save} disabled={saveUnknown}><Save className="size-4" />保存记录</Button></DialogFooter>
      </DialogContent>
    </Dialog>
    <AlertDialog open={discardOpen} onOpenChange={setDiscardOpen}>
      <AlertDialogContent><AlertDialogHeader><AlertDialogTitle>放弃未保存的修改？</AlertDialogTitle><AlertDialogDescription>前台显示与展示截止时间的修改尚未保存，记录正文不会受到影响。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续编辑</AlertDialogCancel><AlertDialogAction onClick={() => { setDiscardOpen(false); onClose() }}>放弃修改</AlertDialogAction></AlertDialogFooter></AlertDialogContent>
    </AlertDialog>
  </>
}
