import { useMemo, useState } from 'react'
import { Bell, CircleAlert, Info, Pin, Save, Smartphone } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import type { AdminAnnouncementItem } from '@/pages/admin/admin-announcement-data'

interface AdminAnnouncementFormDialogProps {
  open: boolean
  item: AdminAnnouncementItem | null
  initialError?: string
  saveFailed?: boolean
  saveUnknown?: boolean
  onClose: () => void
  onSave: (item: AdminAnnouncementItem) => void
}

interface AnnouncementDraft {
  id: number
  title: string
  content: string
  pinned: boolean
  active: boolean
  sortOrder: string
  createdAt: string
  updatedAt: string
}

function toDraft(item: AdminAnnouncementItem | null): AnnouncementDraft {
  return item ? { id: item.id, title: item.title, content: item.content, pinned: item.pinned, active: item.active, sortOrder: String(item.sortOrder), createdAt: item.createdAt, updatedAt: item.updatedAt } : { id: 0, title: '', content: '', pinned: false, active: true, sortOrder: '0', createdAt: '', updatedAt: '' }
}

export function AdminAnnouncementFormDialog({ open, item, initialError = '', saveFailed = false, saveUnknown = false, onClose, onSave }: AdminAnnouncementFormDialogProps) {
  const [draft, setDraft] = useState<AnnouncementDraft>(() => toDraft(item))
  const [initialSignature] = useState(() => JSON.stringify(toDraft(item)))
  const [error, setError] = useState(initialError)
  const [discardOpen, setDiscardOpen] = useState(false)
  const dirty = useMemo(() => JSON.stringify(draft) !== initialSignature, [draft, initialSignature])
  const previewContent = useMemo(() => {
    if (!draft.content.includes('<')) return draft.content
    const document = new DOMParser().parseFromString(draft.content, 'text/html')
    return document.body.textContent || ''
  }, [draft.content])

  function update(next: Partial<AnnouncementDraft>) {
    setDraft(current => ({ ...current, ...next }))
    setError('')
  }

  function requestClose() {
    if (dirty && !saveUnknown) setDiscardOpen(true)
    else onClose()
  }

  function validate() {
    if (!draft.title.trim()) return '请填写公告标题'
    if (draft.title.trim().length > 100) return '公告标题最多 100 字'
    if (draft.content.length > 200000) return '公告正文不能超过 200000 字'
    if (!/^\d+$/.test(draft.sortOrder) || !Number.isSafeInteger(Number(draft.sortOrder))) return '排序须为不小于 0 的整数'
    return ''
  }

  function save() {
    if (saveUnknown) return
    const message = validate()
    if (message) { setError(message); return }
    if (saveFailed) { setError(initialError); return }
    onSave({ id: draft.id, title: draft.title.trim(), content: draft.content, pinned: draft.pinned, active: draft.active, sortOrder: Number(draft.sortOrder), createdAt: draft.createdAt, updatedAt: draft.updatedAt })
  }

  return <>
    <Dialog open={open} onOpenChange={value => { if (!value) requestClose() }}>
      <DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-5xl" onInteractOutside={event => event.preventDefault()}>
        <DialogHeader><DialogTitle>{draft.id ? `编辑公告 · ID ${draft.id}` : '新增平台公告'}</DialogTitle><DialogDescription>设置公告正文、置顶顺序和老板端展示状态；保存仅更新本地示例。</DialogDescription></DialogHeader>
        <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground"><span>置顶优先，其后按排序值与发布时间排列</span>{dirty && !saveUnknown && <span role="status">有未保存的修改</span>}</div>
        <fieldset disabled={saveUnknown} className="min-w-0">
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_310px]">
            <div className="space-y-5">
              <div className="space-y-2"><div className="flex items-center justify-between gap-3"><Label htmlFor="announcement-title">公告标题</Label><span className="text-[11px] text-muted-foreground">{draft.title.length} / 100</span></div><Input id="announcement-title" value={draft.title} onChange={event => update({ title: event.target.value })} placeholder="清楚说明服务调整、活动或使用规则" /></div>
              <div className="space-y-2"><div className="flex items-center justify-between gap-3"><Label htmlFor="announcement-content">公告正文（可选）</Label><span className="text-[11px] text-muted-foreground">{draft.content.length} / 200000</span></div><Textarea id="announcement-content" value={draft.content} onChange={event => update({ content: event.target.value })} className="min-h-64 resize-y leading-6" placeholder="填写完整公告正文，保留必要的业务结果与处理步骤" /><p className="text-[11px] leading-5 text-muted-foreground">真实后台会过滤脚本、样式和 iframe，仅保留白名单富文本；图片须使用已上传素材或 HTTPS 地址。</p></div>
              <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_180px]">
                <div className="space-y-2"><Label htmlFor="announcement-sort">排序</Label><Input id="announcement-sort" value={draft.sortOrder} inputMode="numeric" onChange={event => update({ sortOrder: event.target.value.trim() })} placeholder="非负整数，越小越靠前" /></div>
                <div className="rounded-xl border bg-muted/30 px-4 py-3 text-[11px] leading-5 text-muted-foreground">没有独立发布时间或下线排期；启停立即影响老板端可见性。</div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="flex items-start justify-between gap-4 rounded-xl border p-4"><div><Label htmlFor="announcement-pinned" className="flex items-center gap-1.5"><Pin className="size-3.5" />置顶公告</Label><p className="mt-1 text-[11px] leading-5 text-muted-foreground">置顶公告先于普通排序展示。</p></div><Switch id="announcement-pinned" checked={draft.pinned} onCheckedChange={value => update({ pinned: value })} /></div>
                <div className="flex items-start justify-between gap-4 rounded-xl border p-4"><div><Label htmlFor="announcement-active">启用公告</Label><p className="mt-1 text-[11px] leading-5 text-muted-foreground">停用后首页、列表和详情均不可见。</p></div><Switch id="announcement-active" checked={draft.active} onCheckedChange={value => update({ active: value })} /></div>
              </div>
              <Alert><Info className="size-4" /><AlertDescription>老板首页最多读取 8 条启用公告，公告列表最多读取 20 条。置顶和排序决定哪些公告优先进入首页。</AlertDescription></Alert>
            </div>
            <aside className="space-y-3">
              <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground"><Smartphone className="size-4" />老板端详情预览</div>
              <div className="overflow-hidden rounded-2xl border bg-card shadow-sm">
                <div className="border-b bg-[#10285b] p-5 text-white"><div className="flex items-center justify-between gap-3"><span className="text-[11px] text-blue-100/80">声浪电竞 · 官方公告</span>{draft.pinned && <Badge className="border-white/20 bg-white/10 text-[10px] text-white hover:bg-white/10">置顶</Badge>}</div><h3 className="mt-4 text-xl font-semibold leading-7 tracking-tight">{draft.title || '公告标题'}</h3><p className="mt-4 text-[11px] text-blue-100/70">发布时间 {draft.createdAt || '保存后生成'}</p></div>
                <div className="p-5"><div className="flex items-center gap-2 text-xs font-semibold"><Bell className="size-3.5 text-primary" />公告内容</div><p className="mt-3 max-h-80 overflow-y-auto whitespace-pre-wrap text-sm leading-7 text-muted-foreground phone-scrollbar">{previewContent || '暂无公告正文'}</p><div className="mt-5 border-t pt-4"><Badge variant="outline" className={draft.active ? 'border-success/30 text-success' : ''}>{draft.active ? '老板端可见' : '老板端不可见'}</Badge></div></div>
              </div>
              <p className="text-[11px] leading-5 text-muted-foreground">原型以纯文本预览阅读层级；真实老板端按白名单渲染富文本，并让图片适应公告阅读宽度。</p>
            </aside>
          </div>
        </fieldset>
        {error && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{error}</AlertDescription></Alert>}
        <DialogFooter><Button variant="outline" onClick={requestClose}>{saveUnknown ? '关闭并核对' : '取消'}</Button><Button onClick={save} disabled={saveUnknown}><Save className="size-4" />保存公告</Button></DialogFooter>
      </DialogContent>
    </Dialog>
    <AlertDialog open={discardOpen} onOpenChange={setDiscardOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>放弃未保存的修改？</AlertDialogTitle><AlertDialogDescription>当前标题、正文、置顶和展示状态尚未保存，关闭后本次修改将丢失。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续编辑</AlertDialogCancel><AlertDialogAction onClick={() => { setDiscardOpen(false); onClose() }}>放弃修改</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </>
}
