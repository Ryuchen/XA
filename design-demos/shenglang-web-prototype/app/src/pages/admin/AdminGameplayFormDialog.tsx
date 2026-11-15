import { useMemo, useState } from 'react'
import { CircleAlert, Info, Save, Smartphone } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { gameplayThemes, type AdminGameplayItem, type AdminGameplayKind, type AdminGameplayTheme } from '@/pages/admin/admin-gameplay-data'
import { cn } from '@/lib/utils'

interface AdminGameplayFormDialogProps {
  open: boolean
  item: AdminGameplayItem | null
  items: AdminGameplayItem[]
  initialError?: string
  saveUnknown?: boolean
  onClose: () => void
  onSave: (item: AdminGameplayItem) => void
}

interface GameplayDraft {
  id: number
  name: string
  code: string
  kind: AdminGameplayKind
  subtitle: string
  theme: AdminGameplayTheme
  coverUrl: string
  rules: string
  details: string
  sortOrder: string
  active: boolean
  archived: boolean
  serviceCount: number
}

const blankDraft: GameplayDraft = {
  id: 0, name: '', code: '', kind: 'STANDARD', subtitle: '', theme: 'custom', coverUrl: '', rules: '', details: '', sortOrder: '0', active: true, archived: false, serviceCount: 0,
}

function toDraft(item: AdminGameplayItem | null): GameplayDraft {
  if (!item) return { ...blankDraft }
  return { id: item.id, name: item.name, code: item.code, kind: item.kind, subtitle: item.subtitle, theme: item.theme, coverUrl: item.coverUrl, rules: item.rules, details: item.details, sortOrder: String(item.sortOrder), active: item.active, archived: item.archived, serviceCount: item.serviceCount }
}

function validCoverUrl(value: string) {
  if (!value) return true
  try {
    const url = new URL(value)
    return url.protocol === 'https:' || url.protocol === 'http:'
  } catch {
    return false
  }
}

export function AdminGameplayFormDialog({ open, item, items, initialError = '', saveUnknown = false, onClose, onSave }: AdminGameplayFormDialogProps) {
  const [draft, setDraft] = useState<GameplayDraft>(() => toDraft(item))
  const [initialSignature] = useState(() => JSON.stringify(toDraft(item)))
  const [error, setError] = useState(initialError)
  const [discardOpen, setDiscardOpen] = useState(false)
  const dirty = useMemo(() => JSON.stringify(draft) !== initialSignature, [draft, initialSignature])
  const theme = gameplayThemes[draft.theme]

  function update(next: Partial<GameplayDraft>) {
    setDraft(current => ({ ...current, ...next }))
    setError('')
  }

  function requestClose() {
    if (dirty && !saveUnknown) setDiscardOpen(true)
    else onClose()
  }

  function validate() {
    const name = draft.name.trim()
    const code = draft.code.trim()
    if (!name || name.length > 40) return '请输入玩法名称，最多 40 字'
    if (!/^[a-zA-Z0-9_-]+$/.test(code) || code.length > 40) return '唯一标识须为 40 字以内的英文、数字、下划线或连字符'
    if (items.some(gameplay => gameplay.id !== draft.id && gameplay.name === name)) return '玩法名称已存在，请更换名称'
    if (items.some(gameplay => gameplay.id !== draft.id && gameplay.code === code)) return '唯一标识已存在，请更换标识'
    if (!/^\d+$/.test(draft.sortOrder)) return '排序须为不小于 0 的整数'
    if (!validCoverUrl(draft.coverUrl.trim())) return '自定义封面须为有效的 HTTP 或 HTTPS 地址'
    if (draft.subtitle.length > 100) return '一句话介绍最多 100 字'
    if (draft.rules.length > 5000) return '玩法规则最多 5000 字'
    if (draft.details.length > 100000) return '玩法详情最多 100000 字'
    if (item && item.serviceCount > 0 && draft.kind !== item.kind) return '已有商品的玩法不能更改类型，请新建玩法'
    if (draft.archived && draft.active) return '请先恢复已归档玩法，再启用'
    return ''
  }

  function save() {
    if (saveUnknown) return
    const message = validate()
    if (message) { setError(message); return }
    onSave({
      id: draft.id, name: draft.name.trim(), code: draft.code.trim(), kind: draft.kind, subtitle: draft.subtitle.trim(), theme: draft.theme,
      coverUrl: draft.coverUrl.trim(), rules: draft.rules.trim(), details: draft.details.trim(), sortOrder: Number(draft.sortOrder), active: draft.active,
      archived: draft.archived, serviceCount: draft.serviceCount,
    })
  }

  return <>
    <Dialog open={open} onOpenChange={value => { if (!value) requestClose() }}>
      <DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-4xl" onInteractOutside={event => event.preventDefault()}>
        <DialogHeader><DialogTitle>{draft.id ? `编辑玩法 · ID ${draft.id}` : '新增玩法'}</DialogTitle><DialogDescription>设置老板端目录入口、通用规则与详情；保存仅更新本地示例。</DialogDescription></DialogHeader>
        <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground"><span>{draft.kind === 'ROULETTE' ? '转盘玩法 · 奖池在商品内配置' : '普通玩法 · 统一组织所属商品'}</span>{dirty && !saveUnknown && <span role="status">有未保存的修改</span>}</div>
        <fieldset disabled={saveUnknown} className="min-w-0">
          <Tabs defaultValue="entry">
            <TabsList className="grid h-11 w-full grid-cols-2"><TabsTrigger value="entry">入口与展示</TabsTrigger><TabsTrigger value="content">规则与详情</TabsTrigger></TabsList>
            <TabsContent value="entry" className="mt-5">
              <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_250px]">
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2"><Label htmlFor="gameplay-name">玩法名称</Label><Input id="gameplay-name" value={draft.name} maxLength={40} onChange={event => update({ name: event.target.value })} placeholder="如：娱乐双排" /></div>
                  <div className="space-y-2"><Label htmlFor="gameplay-code">唯一标识</Label><Input id="gameplay-code" value={draft.code} maxLength={40} onChange={event => update({ code: event.target.value.trim() })} placeholder="companion-duo" className="font-mono text-xs" /></div>
                  <div className="space-y-2 sm:col-span-2"><Label>玩法类型</Label><RadioGroup value={draft.kind} onValueChange={value => update({ kind: value as AdminGameplayKind })} disabled={draft.serviceCount > 0} className="grid grid-cols-2 gap-3" aria-label="玩法类型">
                    <Label htmlFor="gameplay-standard" className={cn('flex min-h-16 cursor-pointer items-center gap-3 rounded-xl border p-3', draft.kind === 'STANDARD' && 'border-primary bg-primary/5')}><RadioGroupItem id="gameplay-standard" value="STANDARD" /><span><span className="block text-sm font-medium">普通玩法</span><span className="mt-1 block text-[11px] font-normal text-muted-foreground">按目录组织服务商品</span></span></Label>
                    <Label htmlFor="gameplay-roulette" className={cn('flex min-h-16 cursor-pointer items-center gap-3 rounded-xl border p-3', draft.kind === 'ROULETTE' && 'border-primary bg-primary/5')}><RadioGroupItem id="gameplay-roulette" value="ROULETTE" /><span><span className="block text-sm font-medium">转盘单</span><span className="mt-1 block text-[11px] font-normal text-muted-foreground">每单确定一个服务目标</span></span></Label>
                  </RadioGroup>{draft.serviceCount > 0 && <p className="text-[11px] text-muted-foreground">已关联 {draft.serviceCount} 个商品，类型已锁定。</p>}</div>
                  <div className="space-y-2 sm:col-span-2"><Label htmlFor="gameplay-subtitle">一句话介绍</Label><Input id="gameplay-subtitle" value={draft.subtitle} maxLength={100} onChange={event => update({ subtitle: event.target.value })} placeholder="说明玩法带给老板的体验" /></div>
                  <div className="space-y-2"><Label>封面主题</Label><Select value={draft.theme} onValueChange={value => update({ theme: value as AdminGameplayTheme })}><SelectTrigger aria-label="封面主题"><SelectValue /></SelectTrigger><SelectContent>{Object.entries(gameplayThemes).map(([value, item]) => <SelectItem key={value} value={value}>{item.name}</SelectItem>)}</SelectContent></Select></div>
                  <div className="space-y-2"><Label htmlFor="gameplay-sort">排序</Label><Input id="gameplay-sort" value={draft.sortOrder} inputMode="numeric" onChange={event => update({ sortOrder: event.target.value.trim() })} placeholder="非负整数" /></div>
                  <div className="space-y-2 sm:col-span-2"><Label htmlFor="gameplay-cover">自定义封面地址</Label><Input id="gameplay-cover" value={draft.coverUrl} onChange={event => update({ coverUrl: event.target.value.trim() })} placeholder="https://… 留空使用内置主题封面" /><p className="text-[11px] text-muted-foreground">原型不请求外部图片，仅保存地址并预览主题层级。</p></div>
                  <div className="flex items-start justify-between gap-4 rounded-xl border p-4 sm:col-span-2"><div><Label htmlFor="gameplay-active">启用玩法</Label><p className="mt-1 text-[11px] leading-5 text-muted-foreground">停用后隐藏入口并阻止新下单，历史订单继续按快照履约。</p></div><Switch id="gameplay-active" checked={draft.active} onCheckedChange={value => update({ active: value })} /></div>
                </div>
                <div className="space-y-3"><div className="flex items-center gap-2 text-xs font-medium text-muted-foreground"><Smartphone className="size-4" />老板端入口预览</div><div className="overflow-hidden rounded-2xl border bg-card shadow-sm"><div className={cn('relative flex aspect-[5/3] items-end overflow-hidden bg-gradient-to-br p-4 text-white', theme.className)}><span className="absolute right-4 top-2 text-6xl font-light text-white/25">{theme.symbol}</span><div className="relative"><p className="text-lg font-semibold">{draft.name || '玩法名称'}</p><p className="mt-1 text-xs text-white/80">{draft.subtitle || '一句话介绍玩法体验'}</p></div></div><div className="space-y-3 p-4"><div className="flex flex-wrap gap-2"><Badge variant="outline">{draft.kind === 'ROULETTE' ? '转盘单' : '普通玩法'}</Badge><Badge variant="outline" className={draft.active ? 'border-success/30 text-success' : ''}>{draft.active ? '启用' : '停用'}</Badge></div><p className="text-xs leading-5 text-muted-foreground">关联 {draft.serviceCount} 个商品 · 排序 {draft.sortOrder || '—'}</p></div></div><p className="text-[11px] leading-5 text-muted-foreground">入口是否真正展示，还取决于所属商品、游戏类目与服务分类状态。</p></div>
              </div>
            </TabsContent>
            <TabsContent value="content" className="mt-5 space-y-5">
              {draft.kind === 'ROULETTE' && <Alert className="border-primary/25 bg-primary/5"><Info className="size-4 text-primary" /><AlertDescription>在各商品中配置 2–8 个服务目标与概率权重。每单仅一个目标，余额支付成功时确定，报单审核后结算。</AlertDescription></Alert>}
              <div className="space-y-2"><div className="flex items-center justify-between gap-3"><Label htmlFor="gameplay-rules">玩法规则</Label><span className="text-[11px] text-muted-foreground">{draft.rules.length} / 5000</span></div><Textarea id="gameplay-rules" value={draft.rules} maxLength={5000} onChange={event => update({ rules: event.target.value })} className="min-h-36" placeholder="说明下单、履约和结算边界" /></div>
              <div className="space-y-2"><div className="flex items-center justify-between gap-3"><Label htmlFor="gameplay-details">通用玩法详情</Label><span className="text-[11px] text-muted-foreground">商品可独立覆盖</span></div><Textarea id="gameplay-details" value={draft.details} onChange={event => update({ details: event.target.value })} className="min-h-48" placeholder="填写展示在价目下方的通用详情" /><p className="text-[11px] leading-5 text-muted-foreground">原型以文本演示内容层级；真实后台支持标题、列表与 PNG、JPEG、WebP 图片，单张不超过 10MB。</p></div>
              <div className="rounded-xl border bg-muted/40 p-4"><p className="text-xs font-medium">详情内容预览</p><p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-muted-foreground">{draft.details || '暂无通用详情，所属商品仍可使用自己的独立详情。'}</p></div>
            </TabsContent>
          </Tabs>
        </fieldset>
        {error && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{error}</AlertDescription></Alert>}
        <DialogFooter><Button variant="outline" onClick={requestClose}>{saveUnknown ? '关闭并核对' : '取消'}</Button><Button onClick={save} disabled={saveUnknown}><Save className="size-4" />保存玩法</Button></DialogFooter>
      </DialogContent>
    </Dialog>
    <AlertDialog open={discardOpen} onOpenChange={setDiscardOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>放弃未保存的修改？</AlertDialogTitle><AlertDialogDescription>当前玩法资料尚未保存，关闭后本次修改将丢失。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续编辑</AlertDialogCancel><AlertDialogAction onClick={() => { setDiscardOpen(false); onClose() }}>放弃修改</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </>
}
