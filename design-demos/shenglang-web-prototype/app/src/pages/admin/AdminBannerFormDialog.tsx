import { useMemo, useState } from 'react'
import { CircleAlert, ExternalLink, ImagePlus, Info, RotateCcw, Save, Smartphone } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { adminBannerTargets, bannerLinkTypeLabels, type AdminBannerItem, type AdminBannerLinkType } from '@/pages/admin/admin-banner-data'
import { cn } from '@/lib/utils'

interface AdminBannerFormDialogProps {
  open: boolean
  item: AdminBannerItem | null
  initialError?: string
  initialTargetError?: boolean
  saveUnknown?: boolean
  onClose: () => void
  onSave: (item: AdminBannerItem) => void
}

interface BannerDraft {
  id: number
  title: string
  image: string
  linkType: AdminBannerLinkType
  target: string
  sortOrder: string
  active: boolean
}

function toDraft(item: AdminBannerItem | null): BannerDraft {
  return item ? { id: item.id, title: item.title, image: item.image, linkType: item.linkType, target: item.target, sortOrder: String(item.sortOrder), active: item.active } : { id: 0, title: '', image: '', linkType: 'NONE', target: '', sortOrder: '0', active: true }
}

export function AdminBannerFormDialog({ open, item, initialError = '', initialTargetError = false, saveUnknown = false, onClose, onSave }: AdminBannerFormDialogProps) {
  const [draft, setDraft] = useState<BannerDraft>(() => toDraft(item))
  const [initialSignature] = useState(() => JSON.stringify(toDraft(item)))
  const [error, setError] = useState(initialError)
  const [targetError, setTargetError] = useState(initialTargetError)
  const [targetSearch, setTargetSearch] = useState('')
  const [imageChecking, setImageChecking] = useState(false)
  const [discardOpen, setDiscardOpen] = useState(false)
  const dirty = useMemo(() => JSON.stringify(draft) !== initialSignature, [draft, initialSignature])
  const needsTargetDictionary = draft.linkType !== 'NONE' && draft.linkType !== 'URL'
  const selectedTarget = adminBannerTargets.find(target => target.type === draft.linkType && target.id === draft.target)
  const targetOptions = useMemo(() => adminBannerTargets.filter(target => target.type === draft.linkType && `${target.label} ${target.meta}`.toLocaleLowerCase().includes(targetSearch.trim().toLocaleLowerCase())), [draft.linkType, targetSearch])

  function update(next: Partial<BannerDraft>) {
    setDraft(current => ({ ...current, ...next }))
    setError('')
  }

  function requestClose() {
    if (dirty && !saveUnknown) setDiscardOpen(true)
    else onClose()
  }

  function chooseImage(file: File | undefined) {
    if (!file) return
    setImageChecking(true)
    setError('')
    const reader = new FileReader()
    reader.onerror = () => { setImageChecking(false); setError('图片读取失败，请重新选择本地文件') }
    reader.onload = () => {
      const source = String(reader.result || '')
      const image = new Image()
      image.onerror = () => { setImageChecking(false); setError('无法识别图片，请选择有效的 PNG、JPEG 或 WebP 文件') }
      image.onload = () => {
        setImageChecking(false)
        if (image.naturalWidth !== 750 || image.naturalHeight !== 320) {
          setError(`图片尺寸为 ${image.naturalWidth} × ${image.naturalHeight}，必须精确为 750 × 320`)
          return
        }
        update({ image: source })
      }
      image.src = source
    }
    reader.readAsDataURL(file)
  }

  function validate() {
    if (!draft.image) return '新增 Banner 必须选择图片，尺寸须精确为 750 × 320'
    if (draft.title.trim().length > 100) return '标题最多 100 字'
    if (!/^\d+$/.test(draft.sortOrder) || !Number.isSafeInteger(Number(draft.sortOrder))) return '排序须为不小于 0 的整数'
    if (draft.linkType !== 'NONE' && !draft.target.trim()) return '请选择跳转目标；不需要跳转时请选择“不跳转”'
    if (needsTargetDictionary && targetError) return '目标字典尚未恢复，请重试后再保存'
    if (draft.linkType === 'URL') {
      try {
        const url = new URL(draft.target.trim())
        if (url.protocol !== 'https:' || !url.hostname) return '外部链接仅允许完整的 HTTPS 地址'
      } catch {
        return '外部链接仅允许完整的 HTTPS 地址，例如 https://example.com/page'
      }
    }
    return ''
  }

  function save() {
    if (saveUnknown || imageChecking) return
    const message = validate()
    if (message) { setError(message); return }
    onSave({ id: draft.id, title: draft.title.trim(), image: draft.image, linkType: draft.linkType, target: draft.linkType === 'NONE' ? '' : draft.target.trim(), sortOrder: Number(draft.sortOrder), active: draft.active })
  }

  const previewTarget = draft.linkType === 'NONE' ? '仅展示，不跳转' : draft.linkType === 'URL' ? '点击后复制 HTTPS 链接' : selectedTarget?.label || draft.target || '尚未选择目标'

  return <>
    <Dialog open={open} onOpenChange={value => { if (!value) requestClose() }}>
      <DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-5xl" onInteractOutside={event => event.preventDefault()}>
        <DialogHeader><DialogTitle>{draft.id ? `编辑 Banner · ID ${draft.id}` : '新增首页 Banner'}</DialogTitle><DialogDescription>配置老板端首页轮播、跳转目标与启停状态；保存仅更新本地示例。</DialogDescription></DialogHeader>
        <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground"><span>固定比例 750:320 · 无独立投放排期</span>{dirty && !saveUnknown && <span role="status">有未保存的修改</span>}</div>
        <fieldset disabled={saveUnknown} className="min-w-0">
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_290px]">
            <div className="space-y-5">
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-3"><Label htmlFor="banner-image">轮播图片</Label><span className="text-[11px] text-muted-foreground">必须精确为 750 × 320</span></div>
                <label htmlFor="banner-image" className={cn('group relative flex aspect-[750/320] cursor-pointer items-center justify-center overflow-hidden rounded-xl border border-dashed bg-muted/40 transition-colors hover:border-primary hover:bg-primary/5', draft.image && 'border-solid')}>
                  {draft.image ? <img src={draft.image} alt="待保存 Banner 预览" className="h-full w-full object-cover" /> : <span className="flex flex-col items-center gap-2 text-sm text-muted-foreground"><ImagePlus className="size-7" /><span>选择本地图片</span><span className="text-[11px]">PNG、JPEG 或 WebP · 不请求外部图片</span></span>}
                  <span className="absolute bottom-3 right-3 rounded-md border bg-background/90 px-2.5 py-1.5 text-xs font-medium shadow-sm backdrop-blur">{imageChecking ? '正在校验…' : draft.image ? '更换图片' : '上传图片'}</span>
                </label>
                <input id="banner-image" type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" onChange={event => { chooseImage(event.target.files?.[0]); event.target.value = '' }} />
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2 sm:col-span-2"><div className="flex items-center justify-between"><Label htmlFor="banner-title">标题（可选）</Label><span className="text-[11px] text-muted-foreground">{draft.title.length} / 100</span></div><Input id="banner-title" value={draft.title} maxLength={100} onChange={event => update({ title: event.target.value })} placeholder="用于后台识别与老板端辅助说明" /></div>
                <div className="space-y-2"><Label>跳转类型</Label><Select value={draft.linkType} onValueChange={value => { update({ linkType: value as AdminBannerLinkType, target: '' }); setTargetSearch('') }}><SelectTrigger aria-label="Banner 跳转类型"><SelectValue /></SelectTrigger><SelectContent>{Object.entries(bannerLinkTypeLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
                <div className="space-y-2"><Label htmlFor="banner-sort">排序</Label><Input id="banner-sort" value={draft.sortOrder} inputMode="numeric" onChange={event => update({ sortOrder: event.target.value.trim() })} placeholder="非负整数，越小越靠前" /></div>
              </div>
              {draft.linkType === 'URL' && <div className="space-y-2"><Label htmlFor="banner-url">完整 HTTPS 链接</Label><Input id="banner-url" value={draft.target} onChange={event => update({ target: event.target.value.trim() })} placeholder="https://example.com/page" className="font-mono text-xs" /><p className="flex items-center gap-1.5 text-[11px] text-muted-foreground"><ExternalLink className="size-3.5" />老板端点击后在 H5 新窗口打开，外部链接需使用 HTTPS。</p></div>}
              {needsTargetDictionary && <div className="space-y-3 rounded-xl border bg-muted/20 p-4">
                <div className="flex items-center justify-between gap-3"><Label>跳转目标</Label>{selectedTarget && <Badge variant="outline" className={cn(!selectedTarget.available && 'border-amber-500/40 text-amber-600 dark:text-amber-400')}>{selectedTarget.available ? '当前可用' : '保留异常目标'}</Badge>}</div>
                {targetError ? <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>目标字典读取失败</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">已保留当前目标，不清空输入。<Button variant="link" className="h-auto p-0" onClick={() => { setTargetError(false); setError('') }}><RotateCcw className="size-3.5" />重试目标字典</Button></AlertDescription></Alert> : <>
                  <Input aria-label="搜索 Banner 跳转目标" value={targetSearch} onChange={event => setTargetSearch(event.target.value)} placeholder={`搜索${bannerLinkTypeLabels[draft.linkType]}名称或编号`} />
                  <Select value={draft.target} onValueChange={value => update({ target: value })}><SelectTrigger aria-label="选择 Banner 跳转目标"><SelectValue placeholder="请选择目标" /></SelectTrigger><SelectContent>{targetOptions.map(target => <SelectItem key={target.id} value={target.id}>{target.label} · {target.meta}{!target.available && ' · 当前不可用'}</SelectItem>)}</SelectContent></Select>
                  {targetOptions.length === 0 && <p className="rounded-lg border border-dashed p-3 text-center text-xs text-muted-foreground">没有匹配目标，请调整搜索词。</p>}
                </>}
                {selectedTarget && !selectedTarget.available && <Alert className="border-amber-500/40 bg-amber-500/10"><CircleAlert className="size-4 text-amber-600 dark:text-amber-400" /><AlertDescription>{selectedTarget.reason} 可保留记录并停用，或更换为可用目标。</AlertDescription></Alert>}
              </div>}
              {draft.linkType === 'NONE' && <Alert><Info className="size-4" /><AlertDescription>不跳转模式会清空目标，Banner 仅用于首页视觉展示。</AlertDescription></Alert>}
              <div className="flex items-start justify-between gap-4 rounded-xl border p-4"><div><Label htmlFor="banner-active">启用 Banner</Label><p className="mt-1 text-[11px] leading-5 text-muted-foreground">停用后老板端不再展示；没有独立投放开始、结束时间。</p></div><Switch id="banner-active" checked={draft.active} onCheckedChange={value => update({ active: value })} /></div>
            </div>
            <aside className="space-y-3">
              <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground"><Smartphone className="size-4" />老板端轮播预览</div>
              <div className="overflow-hidden rounded-2xl border bg-card shadow-sm">
                <div className="relative aspect-[750/320] bg-[#10285b]">
                  {draft.image ? <img src={draft.image} alt="" className="h-full w-full object-cover" /> : <div className="grid h-full place-items-center p-6 text-center text-xs text-blue-100/60">选择图片后预览实际比例</div>}
                  <span className="absolute bottom-2 right-2 rounded-full bg-black/35 px-2 py-0.5 text-[10px] text-white backdrop-blur">1 / 6</span>
                </div>
                <div className="space-y-3 p-4"><p className="line-clamp-2 min-h-10 text-sm font-medium leading-5">{draft.title || '无标题 Banner'}</p><div className="flex flex-wrap gap-2"><Badge variant="outline">{bannerLinkTypeLabels[draft.linkType]}</Badge><Badge variant="outline" className={draft.active ? 'border-success/30 text-success' : ''}>{draft.active ? '启用' : '停用'}</Badge></div><p className="break-words text-[11px] leading-5 text-muted-foreground">{previewTarget}</p></div>
              </div>
              <div className="rounded-xl border bg-muted/40 p-4 text-[11px] leading-5 text-muted-foreground"><p className="font-medium text-foreground">点击时仍会重新核对</p><p className="mt-2">商品展示与老板资格、公告和玩法启停、内容类型及自身时间窗口，都可能在保存后发生变化。</p></div>
            </aside>
          </div>
        </fieldset>
        {error && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{error}</AlertDescription></Alert>}
        <DialogFooter><Button variant="outline" onClick={requestClose}>{saveUnknown ? '关闭并核对' : '取消'}</Button><Button onClick={save} disabled={saveUnknown || imageChecking}><Save className="size-4" />保存 Banner</Button></DialogFooter>
      </DialogContent>
    </Dialog>
    <AlertDialog open={discardOpen} onOpenChange={setDiscardOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>放弃未保存的修改？</AlertDialogTitle><AlertDialogDescription>当前图片、标题、目标和状态尚未保存，关闭后本次修改将丢失。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续编辑</AlertDialogCancel><AlertDialogAction onClick={() => { setDiscardOpen(false); onClose() }}>放弃修改</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </>
}
