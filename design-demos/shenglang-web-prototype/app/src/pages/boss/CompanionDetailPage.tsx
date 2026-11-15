import { useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  AudioLines,
  BadgeCheck,
  CalendarClock,
  Check,
  ChevronRight,
  Gamepad2,
  Gift,
  Headphones,
  MapPin,
  Pause,
  Play,
  ShieldCheck,
  Sparkles,
  Star,
  Volume2,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

export type CompanionDetailScenario = 'default' | 'guest' | 'loading' | 'error' | 'unavailable' | 'service' | 'picker'

interface CompanionDetailPageProps {
  scenario: CompanionDetailScenario
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCatalog: () => void
  onGift: () => void
  onOrder: () => void
  onReserve: () => void
  onRetry: () => void
}

const qualifications = ['王者荣耀 · 娱乐陪玩', '手游 · 荣耀王者', '同范围高档可接低档']

export function CompanionDetailPage({ scenario, onLogin, onBack, onHome, onCatalog, onGift, onOrder, onReserve, onRetry }: CompanionDetailPageProps) {
  const [playing, setPlaying] = useState(false)
  const [voiceLoading, setVoiceLoading] = useState(false)
  const unavailable = scenario === 'unavailable'
  const serviceMode = scenario === 'service'
  const pickerMode = scenario === 'picker'

  function toggleVoice() {
    if (voiceLoading) {
      setVoiceLoading(false)
      return
    }
    if (playing) {
      setPlaying(false)
      return
    }
    setVoiceLoading(true)
    window.setTimeout(() => {
      setVoiceLoading(false)
      setPlaying(true)
    }, 500)
  }

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') return onBack()
  }

  const actionLabel = unavailable
    ? '暂不可约'
    : pickerMode
      ? '已选 · 返回列表'
      : serviceMode
        ? '选择 TA 下单'
        : '选择 TA 的服务'

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="companions" onNavigate={handleDockNavigate} />}>
      <main className="pb-40">
        <div className="flex items-center justify-between px-4 py-2">
          <Button type="button" variant="ghost" className="h-11 rounded-full px-2 text-xs" onClick={onBack} aria-label="返回陪玩列表">
            <ArrowLeft className="size-4" />陪玩列表
          </Button>
          <Badge variant="outline" className="border-primary/20 bg-primary/5 text-[10px] text-primary">声浪陪玩</Badge>
        </div>

        {scenario === 'guest' ? (
          <section className="px-4 pb-8 pt-5">
            <Card className="rounded-3xl py-0 text-center">
              <CardContent className="p-6">
                <div className="mx-auto grid size-16 place-items-center rounded-3xl bg-primary/10 text-primary"><Headphones className="size-8" /></div>
                <p className="mt-6 text-xs font-medium text-primary">先认识，再一起开局</p>
                <h1 className="mt-2 text-2xl font-semibold tracking-tight">陪玩详情</h1>
                <p className="mx-auto mt-3 max-w-[270px] text-sm leading-6 text-muted-foreground">登录后查看陪玩资料、可约状态与服务价格。</p>
                <Button type="button" className="mt-7 h-12 w-full rounded-xl" onClick={onLogin}>微信快捷登录</Button>
              </CardContent>
            </Card>
          </section>
        ) : scenario === 'loading' ? (
          <div className="space-y-4 px-4 pb-8" aria-label="正在加载陪玩详情" aria-busy="true">
            <div className="rounded-3xl border bg-card p-5"><Skeleton className="mx-auto size-24 rounded-full" /><Skeleton className="mx-auto mt-4 h-7 w-32" /><Skeleton className="mx-auto mt-2 h-4 w-44" /><Skeleton className="mt-6 h-9 w-full" /></div>
            <Skeleton className="h-24 w-full rounded-2xl" />
            <Skeleton className="h-28 w-full rounded-2xl" />
            <Skeleton className="h-32 w-full rounded-2xl" />
          </div>
        ) : scenario === 'error' ? (
          <section className="px-4 pb-8 pt-10">
            <div role="alert" className="rounded-3xl border border-destructive/25 bg-destructive/5 p-7 text-center">
              <AlertCircle className="mx-auto size-8 text-destructive" />
              <h1 className="mt-4 text-xl font-semibold">陪玩详情暂时没有加载成功</h1>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试，或返回列表重新选择陪玩。</p>
              <Button type="button" className="mt-6 h-11 w-full" onClick={onRetry}>重新加载</Button>
              <Button type="button" variant="ghost" className="mt-2 h-11 w-full" onClick={onBack}>返回陪玩列表</Button>
            </div>
          </section>
        ) : (
          <>
            <section className="login-aura px-4 pb-5">
              <div className="relative overflow-hidden rounded-3xl border border-primary/15 bg-card/85 p-5 text-center shadow-sm">
                <div className="pointer-events-none absolute inset-x-10 top-0 h-28 rounded-full bg-primary/10 blur-3xl" />
                <div className="relative">
                  <div className="relative mx-auto grid size-24 place-items-center rounded-full bg-gradient-to-br from-sky-400/35 via-blue-500/20 to-indigo-600/25 text-3xl font-semibold text-sky-100 ring-4 ring-primary/10">
                    澈
                    <span className={cn('absolute bottom-1 right-1 size-5 rounded-full border-4 border-card', unavailable ? 'bg-muted-foreground' : 'bg-success')} aria-label={unavailable ? '暂不可约' : '当前可约'} />
                  </div>
                  <div className="mt-4 flex items-center justify-center gap-2">
                    <h1 className="text-2xl font-semibold tracking-tight">小澈</h1>
                    <Badge variant={unavailable ? 'secondary' : 'outline'} className={cn('text-[10px]', !unavailable && 'border-success/25 bg-success/10 text-success')}>{unavailable ? '暂不可约' : '当前可约'}</Badge>
                  </div>
                  <p className="mt-1.5 text-xs text-muted-foreground">声浪王牌 · 女陪</p>
                  <div className="mt-2 flex items-center justify-center gap-3 text-[11px] text-muted-foreground">
                    <span className="flex items-center gap-1"><MapPin className="size-3.5" />杭州</span>
                    <span className="flex items-center gap-1"><ShieldCheck className="size-3.5" />荣耀王者</span>
                  </div>
                  <div className="mt-5 border-t pt-4 text-left">
                    <p className="text-[10px] font-medium text-muted-foreground">擅长服务</p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <Badge className="bg-primary/10 text-primary hover:bg-primary/10"><Gamepad2 className="size-3" />王者荣耀</Badge>
                      <Badge variant="secondary">娱乐双排</Badge>
                      <Badge variant="secondary">射手 / 辅助</Badge>
                    </div>
                  </div>
                  {unavailable && <div role="alert" className="mt-4 rounded-xl bg-muted px-3 py-2.5 text-left text-xs leading-5 text-muted-foreground">当前排期已满，暂时无法接新订单，请稍后再约。</div>}
                </div>
              </div>
            </section>

            <div className="space-y-3 px-4 pb-5">
              <Card className="rounded-2xl py-0">
                <CardContent className="flex items-center gap-3 p-4">
                  <div className={cn('grid size-11 shrink-0 place-items-center rounded-xl transition-colors', playing ? 'bg-primary text-primary-foreground' : 'bg-primary/10 text-primary')}><Volume2 className="size-5" /></div>
                  <div className="min-w-0 flex-1">
                    <h2 className="text-sm font-semibold">小澈的语音房间</h2>
                    <p className="mt-0.5 truncate text-[11px] text-muted-foreground">{voiceLoading ? '语音加载中' : playing ? '正在试听 · 原型无真实音频' : '试听声音，了解 TA 的风格'}</p>
                  </div>
                  <Button type="button" variant={playing ? 'default' : 'outline'} size="icon" className="size-11 shrink-0 rounded-full" onClick={toggleVoice} aria-label={voiceLoading ? '取消语音加载' : playing ? '停止试听' : '试听陪玩声音'} aria-pressed={playing}>
                    {voiceLoading ? <AudioLines className="size-4 animate-pulse" /> : playing ? <Pause className="size-4" /> : <Play className="size-4" />}
                  </Button>
                </CardContent>
              </Card>

              <Card className="overflow-hidden rounded-2xl border-primary/20 py-0">
                <CardContent className="p-4">
                  {serviceMode || pickerMode ? (
                    <div className="mb-3 flex items-start justify-between gap-3 border-b pb-3">
                      <div><p className="text-[10px] text-muted-foreground">当前选择的服务</p><p className="mt-1 text-sm font-semibold">王者荣耀 · 娱乐双排</p></div>
                      <Badge variant="secondary" className="shrink-0 text-[10px]">{pickerMode ? '多人服务' : '单人服务'}</Badge>
                    </div>
                  ) : null}
                  <div className="flex items-end justify-between gap-3">
                    <div><p className="text-[11px] text-muted-foreground">{serviceMode || pickerMode ? '当前服务价格' : '参考时价'}</p><p className="mt-1 text-[11px] text-muted-foreground">{serviceMode || pickerMode ? '下单前再次确认服务与价格' : '具体服务价格以选择为准'}</p></div>
                    <p className="shrink-0 text-2xl font-semibold tracking-tight text-primary">{serviceMode || pickerMode ? '68' : '48'}<span className="ml-1 text-[11px] font-normal text-muted-foreground">浪花 / {serviceMode || pickerMode ? '份' : '小时'}</span></p>
                  </div>
                </CardContent>
              </Card>

              <Card className="rounded-2xl py-0">
                <CardContent className="p-4">
                  <div className="flex items-center gap-2"><Sparkles className="size-4 text-primary" /><h2 className="text-sm font-semibold">关于 TA</h2></div>
                  <p className="mt-3 text-[13px] leading-6 text-muted-foreground">温柔指挥，擅长射手与辅助，娱乐上分都可以。会提前沟通位置和节奏，认真打完每一局。</p>
                </CardContent>
              </Card>

              <Card className="rounded-2xl py-0">
                <CardContent className="p-4">
                  <div className="flex items-center gap-2"><BadgeCheck className="size-4 text-primary" /><h2 className="text-sm font-semibold">接单信息</h2></div>
                  <div className="mt-4 flex items-end justify-between rounded-xl bg-muted/50 p-3">
                    <div><p className="text-[10px] text-muted-foreground">本月接单额</p><p className="mt-1 text-xl font-semibold tabular-nums">3,280<span className="ml-1 text-[10px] font-normal text-muted-foreground">浪花</span></p></div>
                    <div className="text-right"><p className="text-[10px] text-muted-foreground">擅长位置</p><p className="mt-1 text-xs font-medium">射手 · 辅助</p></div>
                  </div>
                  <div className="mt-3 space-y-2">
                    {qualifications.map((item) => <div key={item} className="flex min-h-8 items-center gap-2 text-xs text-muted-foreground"><Check className="size-3.5 shrink-0 text-success" />{item}</div>)}
                  </div>
                </CardContent>
              </Card>

              <Card className="rounded-2xl py-0">
                <CardContent className="p-4">
                  <div className="flex items-center gap-2"><Star className="size-4 text-primary" /><h2 className="text-sm font-semibold">专属标签</h2></div>
                  <div className="mt-3 flex flex-wrap gap-2"><Badge className="bg-primary/10 text-primary hover:bg-primary/10">声音清甜</Badge><Badge variant="secondary">耐心沟通</Badge><Badge variant="secondary">擅长辅助</Badge></div>
                  <Separator className="my-4" />
                  <div className="flex items-center gap-2"><Gift className="size-4 text-primary" /><h2 className="text-sm font-semibold">专属冠名</h2></div>
                  <div className="mt-3 rounded-xl border border-primary/15 bg-primary/5 px-3 py-2.5 text-xs"><span className="font-medium">星海守护者</span><span className="ml-2 text-muted-foreground">老板专属冠名示例</span></div>
                </CardContent>
              </Card>

              {serviceMode && (
                <Card className="rounded-2xl py-0">
                  <CardContent className="p-4">
                    <div className="flex items-center gap-2"><CalendarClock className="size-4 text-primary" /><h2 className="text-sm font-semibold">先约时间</h2></div>
                    <p className="mt-2 text-xs leading-5 text-muted-foreground">提交后等待陪玩确认；成单时再确认价格。</p>
                    <Button type="button" variant="outline" className="mt-3 h-11 w-full rounded-xl" onClick={onReserve}>预约 TA<ChevronRight className="size-4" /></Button>
                  </CardContent>
                </Card>
              )}
            </div>
          </>
        )}
      </main>

      {!['guest', 'loading', 'error'].includes(scenario) && (
        <div className="absolute inset-x-0 bottom-[76px] z-30 border-t bg-background/95 px-4 py-3 backdrop-blur-xl">
          <div className="grid grid-cols-[104px_minmax(0,1fr)] gap-2">
            <Button type="button" variant="outline" className="h-12 rounded-xl" onClick={onGift}><Gift className="size-4" />送礼物</Button>
            <Button type="button" className="h-12 rounded-xl" disabled={unavailable} onClick={() => pickerMode ? onBack() : !serviceMode ? onCatalog() : onOrder()}>{pickerMode && <Check className="size-4" />}{actionLabel}</Button>
          </div>
        </div>
      )}

    </PhoneFrame>
  )
}
