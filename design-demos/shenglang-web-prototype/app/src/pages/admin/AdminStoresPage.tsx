import { useState } from 'react'
import { Building2, Check, Copy, Database, ExternalLink, KeyRound, Plus, ShieldCheck, Store } from 'lucide-react'
import { prototypeStores, storeEntryUrl, type StoreInfo, type StoreProduct, type StoreStatus } from '@/components/store/store-data'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

const statusLabels: Record<StoreStatus, string> = { ACTIVE: '营业中', PROVISIONING: '初始化中', DISABLED: '已停用' }
const productLabels: Record<StoreProduct, string> = { boss: '老板端', provider: '陪玩端', admin: '管理后台' }

function blankStore(slug: string, name: string, shortName: string, status: StoreStatus): StoreInfo {
  return {
    slug, name, shortName, city: shortName, status, databaseAlias: `xa_store_${slug}`,
    boss: { accountId: '—', nickname: '尚未创建', memberName: '店内独立', balance: '0.00', frozen: '0.00', points: 0 },
    provider: { accountId: '—', nickname: '尚未创建', balance: '0.00', frozen: '0.00', monthIncome: '0.00', todayOrders: '0', completion: '0', rating: '0.00', points: 0 },
    metrics: { todayOrders: '0', waiting: '0', inService: '0', net: '0', accounts: '0', bosses: '0', providers: '0' },
    announcement: '尚未发布', activity: '尚未创建',
  }
}

export function AdminStoresPage() {
  const [stores, setStores] = useState<StoreInfo[]>(() => [...prototypeStores, blankStore('haishi', '海屿电竞 · 东港店', '海屿东港', 'DISABLED')])
  const [createOpen, setCreateOpen] = useState(false)
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [formError, setFormError] = useState('')
  const [pendingStatus, setPendingStatus] = useState<StoreInfo | null>(null)
  const [notice, setNotice] = useState('')
  const [copyError, setCopyError] = useState('')

  function createStore() {
    const normalized = slug.trim().toLowerCase()
    if (!name.trim()) return setFormError('请填写门店名称。')
    if (!/^[a-z][a-z0-9-]{2,31}$/.test(normalized)) return setFormError('门店标识须为 3–32 位小写字母、数字或连字符，并以字母开头。')
    if (stores.some(item => item.slug === normalized)) return setFormError('该门店标识已存在，请更换。')
    setStores(items => [...items, blankStore(normalized, name.trim(), name.trim().replace(/电竞.*/, ''), 'PROVISIONING')])
    setCreateOpen(false)
    setName('')
    setSlug('')
    setFormError('')
    setNotice(`已登记 ${name.trim()}：仅创建空白门店与独立业务库任务，不复制原店客户、订单或资金。`)
  }

  function changeStatus() {
    if (!pendingStatus) return
    const next = pendingStatus.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE'
    setStores(items => items.map(item => item.slug === pendingStatus.slug ? { ...item, status: next } : item))
    setNotice(next === 'ACTIVE' ? `${pendingStatus.name}已启用（本地原型状态）。店员仍需本店授权才能进入后台。` : `${pendingStatus.name}已停用（本地原型状态）。停用应阻止新会话，保留原店数据与审计记录。`)
    setPendingStatus(null)
  }

  async function copyEntry(item: StoreInfo, product: StoreProduct) {
    try {
      await navigator.clipboard.writeText(storeEntryUrl(item, product))
      setNotice(`已复制${item.name}${productLabels[product]}独立入口。`)
      setCopyError('')
    } catch {
      setCopyError('浏览器未允许复制，请手动复制地址栏中的 store 参数。')
    }
  }

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="rounded-xl border bg-card p-5 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div><div className="flex items-center gap-3"><Store className="size-6 text-primary" /><h1 className="text-2xl font-semibold tracking-tight">平台门店管理</h1><Badge variant="outline">平台级权限</Badge></div><p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">每家门店拥有独立老板端、陪玩端和店内管理后台。平台只维护门店元数据、入口、独立业务库与启停状态，不在这里混看业务明细。</p></div>
        <Dialog open={createOpen} onOpenChange={open => { setCreateOpen(open); if (!open) setFormError('') }}>
          <DialogTrigger asChild><Button className="h-11"><Plus className="size-4" />新增门店</Button></DialogTrigger>
          <DialogContent>
            <DialogHeader><DialogTitle>登记空白门店</DialogTitle><DialogDescription>创建独立租户标识和业务库初始化任务。门店权限、账号与经营数据从空白开始。</DialogDescription></DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2"><Label htmlFor="new-store-name">门店名称</Label><Input id="new-store-name" value={name} onChange={event => setName(event.target.value)} placeholder="例如：海屿电竞 · 东港店" /></div>
              <div className="space-y-2"><Label htmlFor="new-store-slug">唯一门店标识</Label><Input id="new-store-slug" value={slug} onChange={event => setSlug(event.target.value.toLowerCase())} placeholder="例如：haishi" /><p className="text-xs text-muted-foreground">用于入口、令牌主题、缓存、文件路径和异步任务的门店上下文。</p></div>
              <div className="rounded-lg border bg-muted/40 p-3 text-xs leading-5 text-muted-foreground"><Database className="mr-1 inline size-4 text-primary" />独立业务库：xa_store_{slug || '门店标识'}。不会复制默认店的客户、订单、钱包、履约或运营数据。</div>
              {formError && <p role="alert" className="text-sm text-destructive">{formError}</p>}
            </div>
            <DialogFooter><Button variant="outline" onClick={() => setCreateOpen(false)}>取消</Button><Button onClick={createStore}>创建初始化任务</Button></DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </section>

    <Alert className="border-primary/25 bg-primary/5"><ShieldCheck className="size-4 text-primary" /><AlertTitle>租户边界先于业务页面</AlertTitle><AlertDescription>平台门店管理授权与店内后台角色独立。平台可管理入口和生命周期，不因拥有平台权限而读取店内客户、订单、钱包、薪资或经营核算。</AlertDescription></Alert>
    {notice && <Alert role="status" className="border-success/30 bg-success/5"><Check className="size-4 text-success" /><AlertDescription>{notice}</AlertDescription></Alert>}
    {copyError && <Alert variant="destructive"><AlertDescription>{copyError}</AlertDescription></Alert>}

    <div className="grid gap-4 md:grid-cols-3">
      <Card><CardHeader><CardDescription>已登记门店</CardDescription><CardTitle className="text-2xl tabular-nums">{stores.length}</CardTitle></CardHeader><CardContent className="pt-0 text-xs text-muted-foreground">每店一个稳定租户标识</CardContent></Card>
      <Card><CardHeader><CardDescription>独立业务库</CardDescription><CardTitle className="text-2xl tabular-nums">{stores.length}</CardTitle></CardHeader><CardContent className="pt-0 text-xs text-muted-foreground">禁止跨店查询与资金操作</CardContent></Card>
      <Card><CardHeader><CardDescription>营业中</CardDescription><CardTitle className="text-2xl tabular-nums">{stores.filter(item => item.status === 'ACTIVE').length}</CardTitle></CardHeader><CardContent className="pt-0 text-xs text-muted-foreground">初始化或停用店不接收新业务</CardContent></Card>
    </div>

    <Card>
      <CardHeader><CardTitle className="text-base">门店与独立入口</CardTitle><CardDescription>复制入口保留门店参数；后台入口仍需目标店账号重新鉴权。新增门店只演示生命周期，不自动产生演示账号。</CardDescription></CardHeader>
      <CardContent className="pt-0">
        <Table>
          <TableHeader><TableRow><TableHead>门店 / 租户标识</TableHead><TableHead>独立业务库</TableHead><TableHead>状态</TableHead><TableHead>三端入口</TableHead><TableHead>生命周期</TableHead></TableRow></TableHeader>
          <TableBody>{stores.map(item => {
            const previewable = prototypeStores.some(seed => seed.slug === item.slug) && item.status === 'ACTIVE'
            return <TableRow key={item.slug}>
              <TableCell><div className="flex items-center gap-3"><span className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary"><Building2 className="size-4" /></span><div><p className="font-medium">{item.name}</p><p className="mt-1 font-mono text-xs text-muted-foreground">{item.slug}</p></div></div></TableCell>
              <TableCell><code className="rounded bg-muted px-2 py-1 text-xs">{item.databaseAlias}</code><p className="mt-1 text-xs text-muted-foreground">独立连接与迁移版本</p></TableCell>
              <TableCell><Badge variant={item.status === 'ACTIVE' ? 'secondary' : 'outline'} className={item.status === 'ACTIVE' ? 'bg-success/10 text-success' : item.status === 'PROVISIONING' ? 'bg-primary/10 text-primary' : ''}>{statusLabels[item.status]}</Badge></TableCell>
              <TableCell><div className="flex flex-wrap gap-1">{(['boss', 'provider', 'admin'] as const).map(product => <Button key={product} variant="ghost" size="sm" disabled={!previewable} onClick={() => copyEntry(item, product)} title={previewable ? `复制${productLabels[product]}入口` : '该示例门店暂无入口数据'}><Copy className="size-3.5" />{productLabels[product]}</Button>)}</div>{previewable && <Button variant="link" className="h-8 px-2 text-xs" onClick={() => window.location.assign(storeEntryUrl(item, 'admin'))}>进入本店后台登录<ExternalLink className="size-3" /></Button>}</TableCell>
              <TableCell>{item.status === 'PROVISIONING' ? <Button variant="outline" size="sm" onClick={() => { setStores(items => items.map(current => current.slug === item.slug ? { ...current, status: 'ACTIVE' } : current)); setNotice(`${item.name}初始化已完成（本地原型状态）；真实环境须验证空库、迁移与店内管理员授权。`) }}>完成示例初始化</Button> : <Button variant={item.status === 'ACTIVE' ? 'outline' : 'default'} size="sm" onClick={() => setPendingStatus(item)}>{item.status === 'ACTIVE' ? '停用门店' : '启用门店'}</Button>}</TableCell>
            </TableRow>
          })}</TableBody>
        </Table>
      </CardContent>
    </Card>

    <section className="grid gap-4 lg:grid-cols-3">
      <Card><CardHeader><KeyRound className="size-5 text-primary" /><CardTitle className="text-base">入口与鉴权</CardTitle></CardHeader><CardContent className="pt-0 text-sm leading-6 text-muted-foreground">门店参数只负责识别入口，不能作为授权凭证。老板和陪玩按店建立账号关系；后台按店重新登录并校验店内角色。</CardContent></Card>
      <Card><CardHeader><Database className="size-5 text-primary" /><CardTitle className="text-base">全链路隔离</CardTitle></CardHeader><CardContent className="pt-0 text-sm leading-6 text-muted-foreground">数据库连接、缓存键、文件路径、实时频道和异步任务都保留门店上下文。跨店访问统一拒绝并记录审计。</CardContent></Card>
      <Card><CardHeader><ShieldCheck className="size-5 text-primary" /><CardTitle className="text-base">空白初始化</CardTitle></CardHeader><CardContent className="pt-0 text-sm leading-6 text-muted-foreground">原默认店数据只留在原店。新增门店不得复制原店客户、订单、资金或接收人；公共字典只能显式引用，不共享运行数据。</CardContent></Card>
    </section>

    <AlertDialog open={pendingStatus !== null} onOpenChange={open => !open && setPendingStatus(null)}>
      <AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{pendingStatus?.status === 'ACTIVE' ? '停用' : '启用'}{pendingStatus?.name}？</AlertDialogTitle><AlertDialogDescription className="leading-6">{pendingStatus?.status === 'ACTIVE' ? '停用后应阻止该店新登录与新业务写入，进行中的会话按安全策略失效；数据保留在原店，不删除、不迁移。' : '启用前确认独立业务库、迁移版本和本店管理员授权均已就绪；不会获得其他门店的数据或权限。'}</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel><AlertDialogAction onClick={changeStatus}>确认{pendingStatus?.status === 'ACTIVE' ? '停用' : '启用'}</AlertDialogAction></AlertDialogFooter></AlertDialogContent>
    </AlertDialog>
  </div>
}
