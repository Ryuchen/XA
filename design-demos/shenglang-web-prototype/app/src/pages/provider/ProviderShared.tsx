import type { ReactNode } from 'react'
import { AlertCircle, Inbox, RefreshCw } from 'lucide-react'
import { PageHeader } from '@/components/prototype/PageHeader'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { ProviderScenario } from '@/pages/provider/provider-data'

export function ProviderPage({ children }: { children: ReactNode }) {
  return <main className="pb-32 md:pb-12">{children}</main>
}

export function ProviderHeader({ title, description, action, compact = false }: { title: string; description: string; action?: ReactNode; compact?: boolean }) {
  return <PageHeader title={title} description={description} action={action} compact={compact} />
}

export function ProviderSection({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`px-4 pb-6 md:px-6 ${className}`}>{children}</section>
}

export function ProviderState({ scenario, onRetry, emptyTitle = '这里暂时没有内容', emptyDescription = '新的业务记录会在这里显示。', emptyAction }: { scenario: ProviderScenario; onRetry: () => void; emptyTitle?: string; emptyDescription?: string; emptyAction?: ReactNode }) {
  if (scenario === 'loading') return <ProviderSection><div className="grid gap-4 md:grid-cols-2" aria-busy="true" aria-label="正在加载页面">{[220, 220, 180, 180].map((height, index) => <Skeleton key={index} className="w-full rounded-[12px]" style={{ height }} />)}</div></ProviderSection>
  if (scenario === 'error') return <ProviderSection><div role="alert" className="rounded-[12px] border border-destructive/25 bg-destructive/5 p-6 text-center md:p-8"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">页面暂时没有加载成功</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试。示例操作不会影响真实订单或资金。</p><Button className="mt-6 h-11 rounded-[8px]" onClick={onRetry}><RefreshCw className="size-4" />重新加载</Button></div></ProviderSection>
  if (scenario === 'empty') return <ProviderSection><div className="rounded-[12px] border bg-card px-4 py-10 text-center"><Inbox className="mx-auto size-8 text-muted-foreground" /><h2 className="mt-4 text-lg font-semibold">{emptyTitle}</h2><p className="mx-auto mt-2 max-w-sm text-sm leading-6 text-muted-foreground">{emptyDescription}</p>{emptyAction && <div className="mt-6 flex justify-center">{emptyAction}</div>}</div></ProviderSection>
  return null
}

export function ProviderNotice({ children, tone = 'success' }: { children: ReactNode; tone?: 'success' | 'warning' }) {
  return <p role="status" className={`mb-4 rounded-[8px] border px-3 py-2.5 text-sm ${tone === 'success' ? 'border-success/20 bg-success/5 text-success' : 'border-warning/25 bg-warning/5 text-warning'}`}>{children}</p>
}

export function DetailGrid({ items }: { items: { label: string; value: ReactNode }[] }) {
  return <dl className="grid grid-cols-2 gap-3 rounded-[8px] bg-muted/40 p-3 text-xs">{items.map(item => <div key={item.label} className="min-w-0"><dt className="text-muted-foreground">{item.label}</dt><dd className="mt-1 break-words font-medium leading-5">{item.value}</dd></div>)}</dl>
}
