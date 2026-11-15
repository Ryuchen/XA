import { useState } from 'react'
import { Check, Copy, Database, Store } from 'lucide-react'
import { useStore } from '@/components/store/store-context'
import { storeEntryUrl, type StoreProduct } from '@/components/store/store-data'
import { getStorePageMeta } from '@/components/store/store-page-meta'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

export function StoreDesignPanel({ product, pageKey }: { product: StoreProduct; pageKey: string }) {
  const { store } = useStore()
  const meta = getStorePageMeta(product, pageKey)
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState(false)

  async function copyEntry() {
    try {
      await navigator.clipboard.writeText(storeEntryUrl(store, product))
      setCopied(true)
      setCopyError(false)
    } catch {
      setCopyError(true)
    }
  }

  return <div className="mt-4 rounded-2xl border bg-card p-5" data-store-design-panel={pageKey}>
    <div className="flex items-center gap-2">
      <Store className="size-4 text-primary" />
      <h2 className="text-sm font-semibold">本页门店设计</h2>
      <Badge variant="outline" className="ml-auto text-xs">已补充</Badge>
    </div>
    <p className="mt-3 text-xs leading-5 text-muted-foreground">{meta.design}</p>
    <div className="mt-4 rounded-lg bg-muted/45 p-3">
      <p className="text-xs font-medium">数据范围</p>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">{meta.scope}</p>
    </div>
    <p className="mt-3 text-xs leading-5 text-muted-foreground">{meta.guard}</p>
    <div className="mt-4 flex items-center gap-2 border-t pt-3 text-xs text-muted-foreground">
      <Database className="size-3.5 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{store.slug} · {store.databaseAlias}</span>
    </div>
    <Button variant="outline" className="mt-3 h-11 w-full text-xs" onClick={copyEntry}>
      {copied ? <Check className="size-4 text-success" /> : <Copy className="size-4" />}
      {copied ? '已复制本店独立入口' : '复制本店独立入口'}
    </Button>
    {copyError && <p role="alert" className="mt-2 text-xs leading-5 text-destructive">浏览器未允许复制，请从地址栏复制带 store={store.slug} 的入口。</p>}
  </div>
}
