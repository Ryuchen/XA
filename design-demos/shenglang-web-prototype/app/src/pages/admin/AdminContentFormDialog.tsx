import { useMemo, useState } from 'react'
import { CalendarClock, CircleAlert, Image as ImageIcon, Info, Save, Smartphone } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { adminContentKindLabels, type AdminContentItem, type AdminContentKind } from '@/pages/admin/admin-content-data'

interface AdminContentFormDialogProps {
  open: boolean
  item: AdminContentItem | null
  existingCodes: string[]
  initialError?: string
  saveUnknown?: boolean
  onClose: () => void
  onSave: (item: AdminContentItem) => void
}

interface ContentDraft {
  id: number
  code: string
  kind: AdminContentKind
  title: string
  imageUrl: string
  content: string
  active: boolean
  startsAt: string
  endsAt: string
  sortOrder: string
  createdAt: string
  updatedAt: string
}

function toDraft(item: AdminContentItem | null): ContentDraft {
  return item ? { ...item, sortOrder: String(item.sortOrder) } : {
    id: 0, code: '', kind: 'HOT', title: '', imageUrl: '', content: '', active: false,
    startsAt: '', endsAt: '', sortOrder: '0', createdAt: '', updatedAt: '',
  }
}

function dateInput(value: string) { return value ? value.replace(' ', 'T') : '' }
function storedDate(value: string) { return value ? value.replace('T', ' ') : '' }

export function AdminContentFormDialog({ open, item, existingCodes, initialError = '', saveUnknown = false, onClose, onSave }: AdminContentFormDialogProps) {
  const [draft, setDraft] = useState<ContentDraft>(() => toDraft(item))
  const [initialSignature] = useState(() => JSON.stringify(toDraft(item)))
  const [error, setError] = useState(initialError)
  const [discardOpen, setDiscardOpen] = useState(false)
  const dirty = useMemo(() => JSON.stringify(draft) !== initialSignature, [draft, initialSignature])
  const previewContent = useMemo(() => {
    if (!draft.content.includes('<')) return draft.content
    const document = new DOMParser().parseFromString(draft.content, 'text/html')
    return document.body.textContent || ''
  }, [draft.content])

  function update(next: Partial<ContentDraft>) {
    setDraft(current => ({ ...current, ...next }))
    setError('')
  }

  function requestClose() {
    if (dirty && !saveUnknown) setDiscardOpen(true)
    else onClose()
  }

  function validate() {
    if (!draft.title.trim()) return '请填写内容标题'
    if (draft.title.trim().length > 100) return '内容标题最多 100 字'
    if (!draft.code.trim()) return '请填写内容标识'
    if (draft.code.length > 80 || !/^[a-zA-Z0-9_-]+$/.test(draft.code)) return '内容标识最多 80 字，仅支持字母、数字、连字符和下划线'
    if (!draft.id && existingCodes.includes(draft.code)) return '内容标识已存在，请更换后再保存'
    if (draft.imageUrl && (draft.imageUrl.length > 500 || (!draft.imageUrl.startsWith('https://') && !draft.imageUrl.startsWith('./') && !draft.imageUrl.startsWith('/')))) return '海报须为已上传素材或完整 HTTPS 地址，且不超过 500 字'
    if (draft.startsAt && draft.endsAt && draft.startsAt >= draft.endsAt) return '结束时间必须晚于开始时间'
    if (!/^-?\d+$/.test(draft.sortOrder) || !Number.isSafeInteger(Number(draft.sortOrder))) return '展示排序须为整数'
    return ''
  }

  function save() {
    if (saveUnknown) return
    const message = validate()
    if (message) { setError(message); return }
    onSave({
      id: draft.id, code: draft.code, kind: draft.kind, title: draft.title.trim(), imageUrl: draft.imageUrl.trim(),
      content: draft.content, active: draft.active, startsAt: draft.startsAt, endsAt: draft.endsAt,
      sortOrder: Number(draft.sortOrder), createdAt: draft.createdAt, updatedAt: draft.updatedAt,
    })
  }

  return <>
    <Dialog open={open} onOpenChange={value => { if (!value) requestClose() }}>
      <DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-5xl" onInteractOutside={event => event.preventDefault()}>
        <DialogHeader><DialogTitle>{draft.id ? `配置运营内容 · ID ${draft.id}` : '新增运营内容'}</DialogTitle><DialogDescription>设置展示位置、上架时间与前台内容；保存仅更新本地示例。</DialogDescription></DialogHeader>
        <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground"><span>活动到期后保留归档，其他内容到期后隐藏</span>{dirty && !saveUnknown && <span role="status">有未保存的修改</span>}</div>
        <fieldset disabled={saveUnknown} className="min-w-0">
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_310px]">
            <div className="space-y-5">
              <Alert><Info className="size-4" /><AlertDescription>入场弹窗读取近期活动与下单须知；首页热点只展示当前活动。停用可隐藏所有公开入口，已结束活动仍保留在活动目录。</AlertDescription></Alert>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2"><div className="flex items-center justify-between"><Label htmlFor="content-title">内容标题</Label><span className="text-[11px] text-muted-foreground">{draft.title.length} / 100</span></div><Input id="content-title" value={draft.title} onChange={event => update({ title: event.target.value })} placeholder="清楚说明活动或规则主题" /></div>
                <div className="space-y-2"><Label htmlFor="content-code">内容标识</Label><Input id="content-code" value={draft.code} disabled={!!draft.id} onChange={event => update({ code: event.target.value.trim() })} placeholder="例如 november-duo-plan" /></div>
              </div>
              <div className="space-y-2"><Label>展示位置</Label><Select value={draft.kind} onValueChange={value => update({ kind: value as AdminContentKind })}><SelectTrigger aria-label="内容展示位置"><SelectValue /></SelectTrigger><SelectContent>{Object.entries(adminContentKindLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
              <div className="space-y-2"><div className="flex items-center justify-between gap-3"><Label htmlFor="content-image">海报地址（可选）</Label><Button type="button" variant="link" className="h-auto p-0 text-xs" onClick={() => update({ imageUrl: draft.kind === 'HOT' ? './activity-duo.svg' : './content-order-guide.svg' })}>使用示例海报</Button></div><Input id="content-image" value={draft.imageUrl} onChange={event => update({ imageUrl: event.target.value })} placeholder="已上传素材路径或 HTTPS 地址" /><p className="text-[11px] leading-5 text-muted-foreground">真实后台支持 PNG、JPEG、WebP 上传；原型仅使用本地示例素材，不请求外部图片。</p></div>
              {draft.kind !== 'HOT' && <div className="space-y-2"><Label htmlFor="content-body">正文（可选）</Label><Textarea id="content-body" value={draft.content} onChange={event => update({ content: event.target.value })} className="min-h-56 resize-y leading-6" placeholder="支持 p、h2、strong、ul、li、img 等白名单富文本标签" /><p className="text-[11px] leading-5 text-muted-foreground">真实后台过滤脚本、样式与 iframe；原型以纯文本预览阅读层级，不执行 HTML。</p></div>}
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2"><Label htmlFor="content-starts">上架时间</Label><Input id="content-starts" type="datetime-local" value={dateInput(draft.startsAt)} onChange={event => update({ startsAt: storedDate(event.target.value) })} /></div>
                <div className="space-y-2"><Label htmlFor="content-ends">{draft.kind === 'HOT' ? '活动结束时间' : '下架时间'}</Label><Input id="content-ends" type="datetime-local" value={dateInput(draft.endsAt)} onChange={event => update({ endsAt: storedDate(event.target.value) })} /></div>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                {draft.kind !== 'HOT' ? <div className="space-y-2"><Label htmlFor="content-sort">展示排序</Label><Input id="content-sort" inputMode="numeric" value={draft.sortOrder} onChange={event => update({ sortOrder: event.target.value.trim() })} placeholder="整数，越大越靠前" /></div> : <div className="rounded-xl border bg-muted/30 px-4 py-3 text-[11px] leading-5 text-muted-foreground">活动目录按上架时间倒序，不使用展示排序；详情只展示海报。</div>}
                <div className="flex items-center justify-between gap-4 rounded-xl border p-4"><div><Label htmlFor="content-active">启用内容</Label><p className="mt-1 text-[11px] leading-5 text-muted-foreground">停用后列表、详情及关联 Banner 均不可访问。</p></div><Switch id="content-active" checked={draft.active} onCheckedChange={value => update({ active: value })} /></div>
              </div>
            </div>
            <aside className="space-y-3">
              <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground"><Smartphone className="size-4" />老板端{draft.kind === 'HOT' ? '活动' : '内容'}预览</div>
              <div className="overflow-hidden rounded-2xl border bg-card shadow-sm">
                <div className="border-b bg-[#10285b] p-4 text-white"><div className="flex items-center justify-between gap-2"><span className="text-[11px] text-blue-100/80">声浪电竞 · {adminContentKindLabels[draft.kind]}</span><Badge className="border-white/20 bg-white/10 text-[10px] text-white hover:bg-white/10">{draft.active ? '已启用' : '未启用'}</Badge></div><h3 className="mt-3 text-lg font-semibold leading-7">{draft.title || '内容标题'}</h3></div>
                {draft.imageUrl.startsWith('./') || draft.imageUrl.startsWith('/') ? <div className="grid min-h-52 place-items-center bg-[#020617] p-3"><img src={draft.imageUrl} alt="本地示例海报预览" className="max-h-80 max-w-full rounded-lg object-contain" /></div> : <div className="grid min-h-40 place-items-center border-b bg-muted/30 p-6 text-center"><div><ImageIcon className="mx-auto size-7 text-muted-foreground" /><p className="mt-3 text-xs text-muted-foreground">{draft.imageUrl ? '外部海报不在原型中加载' : draft.kind === 'HOT' ? '活动海报准备中' : '当前内容没有海报'}</p></div></div>}
                {draft.kind !== 'HOT' && <div className="p-5"><div className="flex items-center gap-2 text-xs font-semibold"><CalendarClock className="size-3.5 text-primary" />内容正文</div><p className="mt-3 max-h-64 overflow-y-auto whitespace-pre-wrap text-sm leading-7 text-muted-foreground phone-scrollbar">{previewContent || '正文准备中'}</p></div>}
                <div className="border-t p-4 text-[11px] leading-5 text-muted-foreground">{draft.startsAt ? `${draft.startsAt} 上架` : '未设置上架时间'}{draft.endsAt ? ` · ${draft.endsAt} ${draft.kind === 'HOT' ? '结束' : '下架'}` : ''}</div>
              </div>
            </aside>
          </div>
        </fieldset>
        {error && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{error}</AlertDescription></Alert>}
        <DialogFooter><Button variant="outline" onClick={requestClose}>{saveUnknown ? '关闭并核对' : '取消'}</Button><Button onClick={save} disabled={saveUnknown}><Save className="size-4" />保存内容</Button></DialogFooter>
      </DialogContent>
    </Dialog>
    <AlertDialog open={discardOpen} onOpenChange={setDiscardOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>放弃未保存的修改？</AlertDialogTitle><AlertDialogDescription>当前标题、海报、正文、展示位置和时间尚未保存，关闭后本次修改将丢失。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续编辑</AlertDialogCancel><AlertDialogAction onClick={() => { setDiscardOpen(false); onClose() }}>放弃修改</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </>
}
