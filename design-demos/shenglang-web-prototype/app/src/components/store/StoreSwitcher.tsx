import { useState } from 'react'
import { Check, ChevronDown, Store } from 'lucide-react'
import { useStore } from '@/components/store/store-context'
import type { StoreInfo, StoreProduct } from '@/components/store/store-data'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'

export function StoreSwitcher({ product, compact = false, className }: { product: StoreProduct; compact?: boolean; className?: string }) {
  const { store, stores, switchStore } = useStore()
  const [pending, setPending] = useState<StoreInfo | null>(null)
  const productLabel = product === 'boss' ? '老板端' : product === 'provider' ? '陪玩端' : '管理后台'
  const destination = product === 'boss' ? '目标店首页' : product === 'provider' ? '目标店接单工作台' : '目标店后台登录页'

  return <>
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant={compact ? 'ghost' : 'outline'} className={cn('h-11 min-w-0 gap-2 px-2.5', !compact && 'px-3', className)} aria-label={`当前${productLabel}门店：${store.name}，点击切换`}>
          <Store className="size-4 shrink-0 text-primary" />
          <span className="max-w-28 truncate text-xs font-medium sm:max-w-36">{compact ? store.shortName : store.name}</span>
          <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        <DropdownMenuLabel>
          <span className="block">切换{productLabel}门店</span>
          <span className="mt-1 block text-xs font-normal leading-5 text-muted-foreground">每店拥有独立入口、账号关系与业务数据</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {stores.map(item => <DropdownMenuItem key={item.slug} disabled={item.slug === store.slug} onSelect={() => setPending(item)} className="min-h-16">
          <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary"><Store className="size-4" /></span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-2 text-sm font-medium"><span className="truncate">{item.name}</span>{item.slug === store.slug && <Badge variant="secondary" className="shrink-0 text-xs">当前</Badge>}</span>
            <span className="mt-0.5 block text-xs text-muted-foreground">{item.slug} · 独立业务库</span>
          </span>
          {item.slug === store.slug && <Check className="size-4 text-primary" />}
        </DropdownMenuItem>)}
      </DropdownMenuContent>
    </DropdownMenu>

    <AlertDialog open={pending !== null} onOpenChange={open => !open && setPending(null)}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>切换至{pending?.name}？</AlertDialogTitle>
          <AlertDialogDescription className="leading-6">
            切换后将离开当前页面并进入{destination}，本页未提交的选品、草稿与操作状态不会带入。
            {product === 'admin' ? '目标店需要使用该店授权账号重新登录。' : '目标店的订单、钱包、优惠与消息均独立，不会与当前店合并。'}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="rounded-lg border bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">
          原型通过完整页面导航销毁旧店页面状态；正式环境还必须使旧店令牌、缓存、文件、实时连接与异步任务失效或保持原店隔离。
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>留在{store.shortName}</AlertDialogCancel>
          <AlertDialogAction onClick={() => pending && switchStore(pending.slug, product)}>确认切店</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </>
}
