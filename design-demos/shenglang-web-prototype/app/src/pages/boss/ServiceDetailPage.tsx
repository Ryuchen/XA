import { useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  Check,
  ChevronRight,
  CircleUserRound,
  Clock3,
  Gamepad2,
  Headphones,
  Heart,
  MessageCircleMore,
  PackageCheck,
  ShieldCheck,
  Sparkles,
  Star,
  WalletCards,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { formatWaveAmount } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { CatalogService } from '@/pages/boss/ServiceCatalogPage'

export type ServiceDetailScenario = 'default' | 'guest' | 'loading' | 'error' | 'consult' | 'provider' | 'review-error'

interface ServiceDetailPageProps {
  scenario: ServiceDetailScenario
  service: CatalogService
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onCheckout: (price: number, variantName: string) => void
  onSupport: () => void
  onRetry: () => void
}

const reviews = [
  { id: 1, name: '林**', score: 5, provider: '小澈', content: '沟通很舒服，开局前确认了位置和节奏，整局都很轻松。', reply: '谢谢认可，下次继续一起开心上分。', date: '2026-10-28' },
  { id: 2, name: '周**', score: 5, provider: '阿屿', content: '服务内容与页面说明一致，时间安排也很顺利。', reply: '', date: '2026-10-21' },
]

export function ServiceDetailPage({ scenario, service, onLogin, onBack, onHome, onCompanions, onCheckout, onSupport, onRetry }: ServiceDetailPageProps) {
  const [variant, setVariant] = useState(0)
  const [favorited, setFavorited] = useState(false)
  const consult = scenario === 'consult' || service.consult
  const price = service.price + variant * 20
  const variantNames = (unit: string) => unit === '小时' ? ['1 小时', '2 小时', '3 小时', '4 小时'] : ['体验 1 局', '欢乐 2 局', '默契 3 局', '畅玩 4 局']
  const choices = variantNames(service.unit).slice(0, service.variants)

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') return onCompanions()
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="home" onNavigate={handleDockNavigate} />}>
      <main className="pb-44 md:pb-32">
        <div className="flex items-center justify-between px-4 py-2 md:px-6 md:py-3">
          <Button type="button" variant="ghost" className="h-11 rounded-[8px] px-2 text-sm" onClick={onBack}>
            <ArrowLeft className="size-4" />服务目录
          </Button>
          <Badge variant="outline" className="rounded-md border-primary/20 bg-primary/5 text-xs text-primary">声浪精选服务</Badge>
        </div>

        {scenario === 'guest' ? (
          <section className="px-4 pb-8 pt-5 md:px-6">
            <Card className="rounded-[12px] py-0 text-center">
              <CardContent className="p-6 md:p-8">
                <div className="mx-auto grid size-16 place-items-center rounded-[12px] bg-primary/10 text-primary"><Gamepad2 className="size-8" /></div>
                <h1 className="mt-6 text-2xl font-semibold">了解这项服务</h1>
                <p className="mt-3 text-sm leading-6 text-muted-foreground">请先登录，查看服务价格与完整说明。</p>
                <Button type="button" className="mt-6 h-12 w-full rounded-[8px]" onClick={onLogin}>微信快捷登录</Button>
              </CardContent>
            </Card>
          </section>
        ) : scenario === 'loading' ? (
          <div className="space-y-4 px-4 md:px-6" aria-busy="true" aria-label="正在加载服务详情">
            <Skeleton className="h-72 w-full rounded-[12px]" />
            <div className="grid gap-4 md:grid-cols-2"><Skeleton className="h-40 rounded-[12px]" /><Skeleton className="h-40 rounded-[12px]" /></div>
            <Skeleton className="h-44 w-full rounded-[12px]" />
          </div>
        ) : scenario === 'error' ? (
          <section className="px-4 pb-8 pt-10 md:px-6">
            <div role="alert" className="rounded-[12px] border border-destructive/25 bg-destructive/5 p-6 text-center md:p-8">
              <AlertCircle className="mx-auto size-8 text-destructive" />
              <h1 className="mt-4 text-xl font-semibold">服务详情暂时没有加载成功</h1>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试，或返回目录重新选择服务。</p>
              <Button type="button" className="mt-6 h-11 w-full rounded-[8px]" onClick={onRetry}>重新加载</Button>
              <Button type="button" variant="ghost" className="mt-3 h-11 w-full rounded-[8px]" onClick={onBack}>返回服务目录</Button>
            </div>
          </section>
        ) : (
          <>
            {scenario === 'provider' && (
              <section className="mx-4 mb-4 flex items-center gap-3 rounded-[12px] border border-primary/20 bg-primary/5 p-4 md:mx-6">
                <div className="grid size-10 shrink-0 place-items-center rounded-[8px] bg-primary/10 text-primary"><Headphones className="size-5" /></div>
                <div className="min-w-0 flex-1"><p className="text-sm font-semibold">已指定小澈</p><p className="mt-1 text-xs text-muted-foreground">确认服务时将保留这位陪玩</p></div>
                <Check className="size-5 text-success" />
              </section>
            )}

            <section className="px-4 md:px-6" aria-labelledby="service-detail-title">
              <div className="grid gap-4 md:grid-cols-[minmax(0,1.5fr)_minmax(260px,0.7fr)]">
                <div className={cn('relative overflow-hidden rounded-[12px] border border-primary/15 bg-gradient-to-br p-5 shadow-sm md:p-6', service.tone)} style={{ backgroundColor: '#0F172A' }}>
                  <div className="pointer-events-none absolute -right-10 -top-12 size-48 rounded-full border border-white/10" />
                  <div className="pointer-events-none absolute -right-2 -top-4 size-32 rounded-full border border-white/10" />
                  <div className="relative flex h-full flex-col">
                    <div className="flex items-center justify-between gap-3">
                      <div><p className="text-sm font-medium text-white/75">{service.game}</p><p className="mt-1 text-xs text-white/55">{service.tab === 'experience' ? '轻量体验 · 即刻开局' : service.tab === 'companion' ? '默契陪玩 · 技术提升' : '声浪精选 · 专属服务'}</p></div>
                      <div className="grid size-12 shrink-0 place-items-center rounded-[8px] border border-white/15 bg-white/10 text-white"><Gamepad2 className="size-6" /></div>
                    </div>
                    <h1 id="service-detail-title" className="mt-8 max-w-[520px] text-[26px] font-semibold leading-[1.3] tracking-[-0.03em] text-white">{service.name}</h1>
                    <p className="mt-3 max-w-[560px] text-sm leading-6 text-white/70">{service.description}</p>
                    <div className="mt-auto flex flex-col gap-4 pt-6 sm:flex-row sm:items-end sm:justify-between">
                      <p className="text-[32px] font-semibold leading-none tracking-tight tabular-nums text-white">{formatWaveAmount(price)}<span className="ml-2 text-xs font-normal text-white/65">浪花 / {service.unit}</span></p>
                      <Badge className="w-fit rounded-md border-white/15 bg-white/10 text-white hover:bg-white/10">{service.platform} · {service.level}</Badge>
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3 md:grid-cols-1 md:gap-4">
                  <div className="flex flex-col justify-between rounded-[12px] border bg-card p-4 md:p-5">
                    <div className="grid size-10 place-items-center rounded-[8px] bg-primary/10 text-primary"><ShieldCheck className="size-5" /></div>
                    <div className="mt-5"><p className="text-xs text-muted-foreground">接单要求</p><p className="mt-1 text-sm font-semibold leading-5">{service.level === '不限难度' ? '陪玩资格待确认' : `${service.level}起接单`}</p></div>
                  </div>
                  <div className="flex flex-col justify-between rounded-[12px] border bg-card p-4 md:p-5">
                    <div className="grid size-10 place-items-center rounded-[8px] bg-primary/10 text-primary"><PackageCheck className="size-5" /></div>
                    <div className="mt-5"><p className="text-xs text-muted-foreground">服务记录</p><p className="mt-1 text-sm font-semibold leading-5">已完成 <span className="tabular-nums">{128 + service.id * 17}</span> 单</p></div>
                  </div>
                </div>
              </div>
            </section>

            <div className="mt-4 space-y-4 px-4 pb-6 md:grid md:grid-cols-2 md:gap-4 md:space-y-0 md:px-6">
              {choices.length > 1 && (
                <Card className="rounded-[12px] py-0 md:col-span-2">
                  <CardContent className="p-4 md:p-5">
                    <div className="flex items-center justify-between"><div><h2 className="text-base font-semibold">选择套餐</h2><p className="mt-1 text-xs text-muted-foreground">切换档位后同步核对价格</p></div><Badge variant="secondary" className="rounded-md text-xs">{choices.length} 个档位</Badge></div>
                    <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
                      {choices.map((name, index) => (
                        <button key={name} type="button" aria-pressed={variant === index} className={cn('min-h-[84px] rounded-[8px] border bg-muted/35 p-3 text-left transition-colors hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring', variant === index && 'border-primary bg-primary/10')} onClick={() => setVariant(index)} aria-label={`${name}，${formatWaveAmount(service.price + index * 20)}浪花每${service.unit}`}>
                          <span className="block text-sm font-medium">{name}</span>
                          <span className="mt-2 block text-lg font-semibold tabular-nums text-primary">{formatWaveAmount(service.price + index * 20)}<span className="ml-1 text-xs font-normal text-muted-foreground">浪花 / {service.unit}</span></span>
                        </button>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              )}

              <Card className="rounded-[12px] py-0">
                <CardContent className="p-4 md:p-5">
                  <div className="flex items-center gap-2"><Sparkles className="size-4 text-primary" /><h2 className="text-base font-semibold">服务亮点</h2></div>
                  <div className="mt-4 space-y-3">{['开局前确认位置、时间与沟通方式', '服务过程认真陪伴，不敷衍每一局', '订单进度与履约状态可随时查看'].map((item) => <div key={item} className="flex items-start gap-2 text-sm leading-6 text-muted-foreground"><Check className="mt-1 size-4 shrink-0 text-success" />{item}</div>)}</div>
                </CardContent>
              </Card>

              {consult && (
                <Card className="rounded-[12px] border-warning/25 bg-warning/5 py-0">
                  <CardContent className="p-4 md:p-5"><div className="flex items-center gap-2"><MessageCircleMore className="size-4 text-warning" /><h2 className="text-base font-semibold">下单前确认</h2></div><p className="mt-3 text-sm leading-6 text-muted-foreground">该服务需由客服确认目标、陪玩安排与最终方案，页面价格为示例参考。</p></CardContent>
                </Card>
              )}

              <Card className="rounded-[12px] py-0">
                <CardContent className="p-4 md:p-5">
                  <div className="flex items-center gap-2"><PackageCheck className="size-4 text-primary" /><h2 className="text-base font-semibold">附加项目</h2></div>
                  <p className="mt-2 text-xs text-muted-foreground">需关联主套餐，由客服确认办理</p>
                  <button type="button" className="mt-4 flex min-h-12 w-full items-center gap-3 rounded-[8px] border px-3 text-left transition-colors hover:bg-accent" onClick={onSupport}><span className="flex-1 text-sm font-medium">额外加时 / 加局</span><span className="text-sm text-primary">20 浪花 / 份</span><ChevronRight className="size-4 text-muted-foreground" /></button>
                </CardContent>
              </Card>

              <Card className="rounded-[12px] py-0">
                <CardContent className="p-4 md:p-5"><div className="flex items-center gap-2"><ShieldCheck className="size-4 text-primary" /><h2 className="text-base font-semibold">服务规则</h2></div><p className="mt-3 text-sm leading-7 text-muted-foreground">下单前请确认服务内容、时间安排及游戏要求。临时调整以双方确认记录为准；未开始的服务变更与售后请通过订单联系客服。</p></CardContent>
              </Card>

              {!consult && (
                <Card className="rounded-[12px] py-0 md:col-span-2">
                  <CardContent className="p-4 md:p-5">
                    <h2 className="text-base font-semibold">服务如何开始</h2>
                    <div className="mt-5 grid gap-5 md:grid-cols-3 md:gap-4">
                      {[
                        ['01', '填写游戏资料', '选择平台派单或指定陪玩', Gamepad2],
                        ['02', '核对订单', '确认优惠、浪花余额与支付信息', WalletCards],
                        ['03', '等待服务开始', '接单与履约进度在订单中查看', Clock3],
                      ].map(([number, title, detail, Icon]) => {
                        const StepIcon = Icon as typeof Gamepad2
                        return <div key={number as string} className="flex items-center gap-3 rounded-[8px] bg-muted/35 p-3"><div className="relative grid size-10 shrink-0 place-items-center rounded-[8px] bg-primary/10 text-primary"><StepIcon className="size-4" /><span className="absolute -right-1 -top-1 rounded-md bg-card px-1 text-xs font-semibold">{number as string}</span></div><div><p className="text-sm font-medium">{title as string}</p><p className="mt-1 text-xs leading-5 text-muted-foreground">{detail as string}</p></div></div>
                      })}
                    </div>
                  </CardContent>
                </Card>
              )}

              <Card className="rounded-[12px] py-0 md:col-span-2">
                <CardContent className="p-4 md:p-5">
                  <div className="flex items-center justify-between gap-4"><div><h2 className="text-base font-semibold">服务评价 <span className="ml-1 text-sm font-normal text-muted-foreground">{scenario === 'review-error' ? '—' : '26'}</span></h2><p className="mt-1 text-xs text-muted-foreground">来自已完成订单的真实体验</p></div>{scenario !== 'review-error' && <div className="flex items-center gap-1 text-primary"><Star className="size-4 fill-current" /><span className="text-xl font-semibold tabular-nums">4.9</span><span className="text-xs text-muted-foreground">/ 5</span></div>}</div>
                  {scenario === 'review-error' ? <button type="button" className="mt-4 flex min-h-12 w-full items-center justify-center gap-2 rounded-[8px] border border-destructive/20 bg-destructive/5 px-3 text-sm text-destructive" onClick={onRetry}><AlertCircle className="size-4" />评价暂未加载，点击重试</button> : <div className="mt-5 grid gap-5 md:grid-cols-2 md:gap-6">{reviews.map((review, index) => <div key={review.id}>{index > 0 && <Separator className="mb-5 md:hidden" />}<div className="flex items-center justify-between"><div className="flex items-center gap-2"><div className="grid size-9 place-items-center rounded-full bg-muted"><CircleUserRound className="size-4 text-muted-foreground" /></div><span className="text-sm font-medium">{review.name}</span></div><Badge variant="secondary" className="rounded-md text-xs text-primary">{review.score} 分</Badge></div><p className="mt-2 text-xs text-muted-foreground">陪玩 · {review.provider}</p><p className="mt-3 text-sm leading-6">{review.content}</p>{review.reply && <p className="mt-3 rounded-[8px] bg-muted/60 p-3 text-xs leading-5 text-muted-foreground"><span className="font-medium text-foreground">陪玩回复：</span>{review.reply}</p>}<p className="mt-3 text-xs text-muted-foreground">{review.date}</p></div>)}</div>}
                </CardContent>
              </Card>
            </div>
          </>
        )}
      </main>

      {!['guest', 'loading', 'error'].includes(scenario) && (
        <div className="absolute inset-x-0 bottom-[84px] z-30 border-t bg-background/95 px-4 py-3 backdrop-blur-xl md:bottom-0 md:px-6">
          <div className="mx-auto max-w-[1120px] md:flex md:items-center md:justify-between md:gap-6">
            <div className="mb-3 flex items-center justify-between gap-3 md:mb-0 md:block">
              <p className="text-xs text-muted-foreground">{choices[variant] || service.name} · {service.platform}</p>
              <p className="text-xl font-semibold tabular-nums text-primary md:mt-1">{formatWaveAmount(price)}<span className="ml-1 text-xs font-normal text-muted-foreground">浪花 / {service.unit}</span></p>
            </div>
            <div className={cn('grid gap-2 md:flex', consult ? 'grid-cols-[88px_minmax(0,1fr)]' : 'grid-cols-[76px_104px_minmax(0,1fr)]')}>
              <Button type="button" variant="ghost" className={cn('h-12 flex-col gap-0 rounded-[8px] px-1 text-xs md:w-20', favorited && 'text-primary')} onClick={() => setFavorited(!favorited)} aria-label={favorited ? '取消收藏' : '收藏服务'} aria-pressed={favorited}><Heart className={cn('size-4', favorited && 'fill-current')} />{favorited ? '已收藏' : '收藏'}</Button>
              {!consult && <Button type="button" variant="outline" className="h-12 rounded-[8px] px-3 text-sm md:w-28" onClick={onCompanions}>挑选陪玩</Button>}
              <Button type="button" className="h-12 rounded-[8px] px-4 text-sm md:min-w-36" onClick={() => consult ? onSupport() : onCheckout(price, choices[variant] || service.name)}>{consult ? '确认方案' : '确认服务'}</Button>
            </div>
          </div>
        </div>
      )}

    </PhoneFrame>
  )
}
