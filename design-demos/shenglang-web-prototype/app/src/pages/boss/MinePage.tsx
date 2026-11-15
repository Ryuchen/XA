import {
  AlertCircle,
  Award,
  Bell,
  CalendarCheck,
  CalendarDays,
  ChevronRight,
  CircleUserRound,
  FileText,
  Gift,
  Headphones,
  Heart,
  LogIn,
  Medal,
  ShieldAlert,
  ShieldCheck,
  Ticket,
  Trophy,
  UserRound,
  WalletCards,
  type LucideIcon,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { HeaderSupportButton } from '@/components/prototype/HeaderSupportButton'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { useStore } from '@/components/store/store-context'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { formatWaveAmount } from '@/lib/format'
import { cn } from '@/lib/utils'

export type MineScenario = 'default' | 'guest' | 'loading' | 'error' | 'unbound' | 'basic'
type OrderStatus = 'pending' | 'grabbed' | 'in_progress'

interface MinePageProps {
  scenario: MineScenario
  onLogin: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: (status?: OrderStatus) => void
  onWallet: () => void
  onGifts: () => void
  onCoupons: () => void
  onMembership: () => void
  onPoints: () => void
  onFavorites: () => void
  onCheckin: () => void
  onAchievements: () => void
  onAnnouncements: () => void
  onClubContent: () => void
  onRights: () => void
  onProfile: () => void
  onReservations: () => void
  onSecurity: () => void
  onSupport: () => void
  onRetry: () => void
}

const quickActions: { label: string; icon: LucideIcon }[] = [
  { label: '优惠券', icon: Ticket },
  { label: '我的收藏', icon: Heart },
  { label: '每日签到', icon: CalendarCheck },
  { label: '我的成就', icon: Award },
]

export function MinePage({ scenario, onLogin, onHome, onCompanions, onOrders, onWallet, onGifts, onCoupons, onMembership, onPoints, onFavorites, onCheckin, onAchievements, onAnnouncements, onClubContent, onRights, onProfile, onReservations, onSecurity, onSupport, onRetry }: MinePageProps) {
  const { store } = useStore()
  const bound = scenario !== 'unbound'
  const basic = scenario === 'basic'
  const memberName = basic ? `${store.shortName}会员` : store.boss.memberName
  const stats = basic ? { pending: 0, grabbed: 0, in_service: 0 } : { pending: 1, grabbed: 2, in_service: 1 }
  const quickHandlers = [onCoupons, onFavorites, onCheckin, onAchievements]

  function handleDockNavigate(key: string) {
    if (key === 'home') onHome()
    else if (key === 'companions') onCompanions()
    else if (key === 'orders') onOrders()
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={handleDockNavigate} />}>
      <main className="pb-32 md:pb-12">
        <header className="px-4 pb-6 pt-2 md:px-6 md:pt-4">
          <div className="flex items-start justify-between gap-4">
            <div><h1 className="text-[26px] font-semibold leading-[1.3] tracking-[-0.03em]">个人中心</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">管理账号、钱包与每一次服务记录。</p></div>
            <div className="flex shrink-0 items-center gap-2"><Badge variant="outline" className="mt-1 rounded-md text-xs">示例账号</Badge><HeaderSupportButton /></div>
          </div>
        </header>

        {scenario === 'guest' ? (
          <GuestState onLogin={onLogin} onSupport={onSupport} />
        ) : scenario === 'loading' ? (
          <div className="grid gap-6 px-4 md:grid-cols-[320px_minmax(0,1fr)] md:px-6" aria-busy="true" aria-label="正在加载个人中心"><Skeleton className="h-72 rounded-[12px]" /><div className="space-y-6"><Skeleton className="h-40 rounded-[12px]" /><Skeleton className="h-40 rounded-[12px]" /></div></div>
        ) : scenario === 'error' ? (
          <section className="px-4 md:px-6"><div role="alert" className="rounded-[12px] border border-destructive/25 bg-destructive/5 p-6 text-center md:p-8"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">个人中心暂时没有加载成功</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试。加载失败不会修改账号、订单或钱包数据。</p><Button type="button" className="mt-6 h-11 w-full rounded-[8px] md:max-w-64" onClick={onRetry}>重新加载</Button></div></section>
        ) : (
          <div className="grid items-start gap-6 px-4 pb-6 md:grid-cols-[320px_minmax(0,1fr)] md:px-6 xl:grid-cols-[360px_minmax(0,1fr)]">
            <aside className="space-y-6" aria-label="账号与钱包摘要">
              <section className="overflow-hidden rounded-[12px] border bg-card shadow-sm" aria-labelledby="mine-profile-name">
                <button type="button" className="flex min-h-[104px] w-full items-center gap-3 p-4 text-left transition-colors hover:bg-accent/40" onClick={onProfile} aria-label={`查看${store.shortName}个人与游戏资料，${store.boss.nickname}`}>
                  <Avatar className="size-14 border bg-primary/10"><AvatarFallback className="bg-primary/10 text-xl font-semibold text-primary">{store.boss.nickname.slice(0, 1)}</AvatarFallback></Avatar>
                  <span className="min-w-0 flex-1"><span id="mine-profile-name" className="block truncate text-lg font-semibold">{store.boss.nickname}</span><span className="mt-1 block text-xs text-muted-foreground">本店账号 ID {store.boss.accountId}</span><span className="mt-1 block text-xs text-muted-foreground">微信账号 · {store.shortName}示例</span></span>
                  <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                </button>
                <div className="border-t p-4">
                  <button type="button" className="flex min-h-11 w-full flex-wrap items-center gap-2 text-left" onClick={onMembership} aria-label={`${memberName}，设置名片标签`}>
                    <Badge className="rounded-md bg-primary/10 text-xs text-primary hover:bg-primary/10">{memberName}</Badge>
                    {!basic && <Badge variant="secondary" className="rounded-md text-xs">荣耀王者</Badge>}
                    <span className="ml-auto flex items-center text-xs font-medium text-primary">名片标签<ChevronRight className="size-4" /></span>
                  </button>
                  <button type="button" className="mt-2 flex min-h-11 w-full items-center gap-2 rounded-[8px] bg-muted/50 px-3 text-left" onClick={onSecurity} aria-label={bound ? '微信已绑定，查看账号安全' : '微信尚未绑定，查看账号安全'}>
                    {bound ? <ShieldCheck className="size-4 text-success" /> : <ShieldAlert className="size-4 text-warning" />}<span className={cn('text-xs', bound ? 'text-muted-foreground' : 'text-warning')}>{bound ? '微信已绑定' : '微信尚未绑定'}</span><span className="ml-auto text-xs font-medium text-primary">账号安全</span><ChevronRight className="size-4 text-muted-foreground" />
                  </button>
                </div>
              </section>

              <button type="button" className="w-full rounded-[12px] border border-slate-700 p-5 text-left text-white shadow-sm transition-colors hover:border-slate-500" style={{ backgroundColor: '#0F172A' }} onClick={onWallet} aria-label={`${store.shortName}钱包余额 ${formatWaveAmount(basic ? '0' : store.boss.balance)} 浪花，查看账单`}>
                <span className="flex items-center justify-between gap-3"><span className="flex items-center gap-2 text-sm text-slate-300"><WalletCards className="size-4 text-sky-300" />本店可用余额</span><span className="flex items-center text-xs text-sky-300">查看账单<ChevronRight className="size-4" /></span></span>
                <span className="mt-5 block text-[32px] font-semibold leading-none tabular-nums">{formatWaveAmount(basic ? '0' : store.boss.balance)}<span className="ml-2 text-sm font-normal text-slate-300">浪花</span></span><span className="mt-4 block text-xs text-slate-300">充值、支付与退款明细均在本店钱包核对</span>
              </button>
            </aside>

            <div className="space-y-6">
              <section className="overflow-hidden rounded-[12px] border bg-card shadow-sm" aria-labelledby="mine-orders-title">
                <div className="flex min-h-16 items-center justify-between gap-3 border-b px-4 py-3"><div><h2 id="mine-orders-title" className="text-base font-semibold">我的订单</h2><p className="mt-1 text-xs text-muted-foreground">服务进度一目了然</p></div><Button type="button" variant="ghost" className="h-11 rounded-[8px] px-3 text-xs" onClick={() => onOrders()}>全部订单<ChevronRight className="size-4" /></Button></div>
                <div className="grid grid-cols-[1fr_auto_1fr_auto_1fr] p-2"><OrderCount label="待接单" count={stats.pending} onClick={() => onOrders('pending')} /><span aria-hidden="true" className="w-px self-stretch bg-border" /><OrderCount label="已接单" count={stats.grabbed} onClick={() => onOrders('grabbed')} /><span aria-hidden="true" className="w-px self-stretch bg-border" /><OrderCount label="服务中" count={stats.in_service} onClick={() => onOrders('in_progress')} /></div>
              </section>

              <section aria-labelledby="mine-shortcuts-title"><div className="mb-3 flex items-center justify-between"><h2 id="mine-shortcuts-title" className="text-base font-semibold">常用功能</h2><span className="text-xs text-muted-foreground">优惠与成长记录</span></div><div className="grid grid-cols-4 overflow-hidden rounded-[12px] border bg-card p-2 shadow-sm">{quickActions.map((item, index) => <QuickAction key={item.label} label={item.label} icon={item.icon} onClick={quickHandlers[index]} />)}</div></section>

              <div className="grid gap-6">
                <MenuGroup title="会员与权益"><MenuRow icon={Gift} label="礼物单" note="送一份心意" onClick={onGifts} /><MenuRow icon={Medal} label="我的会员" note={memberName} onClick={onMembership} /><MenuRow icon={Trophy} label="积分与幸运转盘" note={`${basic ? 0 : store.boss.points} 积分`} onClick={onPoints} /><MenuRow icon={ShieldCheck} label="老板权益" onClick={onRights} /></MenuGroup>
                <MenuGroup title="账号与服务"><MenuRow icon={CalendarDays} label="我的预约" onClick={onReservations} /><MenuRow icon={CircleUserRound} label="个人与游戏资料" onClick={onProfile} /><MenuRow icon={bound ? ShieldCheck : ShieldAlert} label="账号与微信绑定" note={bound ? '已绑定' : '待绑定'} onClick={onSecurity} /><MenuRow icon={FileText} label="俱乐部规范" onClick={onClubContent} /><MenuRow icon={Bell} label="平台公告" onClick={onAnnouncements} /><MenuRow icon={Headphones} label="客服与帮助" onClick={onSupport} /></MenuGroup>
              </div>
            </div>
          </div>
        )}
      </main>
    </PhoneFrame>
  )
}

function GuestState({ onLogin, onSupport }: { onLogin: () => void; onSupport: () => void }) {
  return <div className="mx-auto max-w-2xl space-y-6 px-4 md:px-6"><Card className="rounded-[12px] py-0 text-center shadow-sm"><CardContent className="p-6 md:p-8"><div className="mx-auto grid size-12 place-items-center rounded-[8px] bg-primary/10 text-primary"><UserRound className="size-6" /></div><h2 className="mt-4 text-xl font-semibold">你的游戏生活</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">登录后管理钱包、订单与账号资料，继续每一次默契配合。</p><Button type="button" className="mt-6 h-12 w-full rounded-[8px]" onClick={onLogin}><LogIn className="size-4" />微信快捷登录</Button></CardContent></Card><section className="rounded-[12px] border bg-card p-4 shadow-sm"><h2 className="text-base font-semibold">登录后可使用</h2><div className="mt-3 grid grid-cols-4 gap-1">{quickActions.map(item => <QuickAction key={item.label} label={item.label} icon={item.icon} onClick={onLogin} />)}</div></section><button type="button" className="flex min-h-12 w-full items-center gap-3 rounded-[8px] px-3 text-left text-sm transition-colors hover:bg-accent" onClick={onSupport}><Headphones className="size-4 text-primary" />客服与帮助<ChevronRight className="ml-auto size-4 text-muted-foreground" /></button></div>
}

function OrderCount({ label, count, onClick }: { label: string; count: number; onClick: () => void }) {
  return <button type="button" className="flex min-h-[72px] flex-col items-center justify-center gap-1 rounded-[8px] transition-colors hover:bg-accent/50" onClick={onClick} aria-label={`${label}，${count} 单`}><span className="text-2xl font-semibold tabular-nums">{count}</span><span className="text-xs text-muted-foreground">{label}</span></button>
}

function QuickAction({ label, icon: Icon, onClick }: { label: string; icon: LucideIcon; onClick: () => void }) {
  return <button type="button" className="flex min-h-[88px] min-w-0 flex-col items-center justify-center gap-2 rounded-[8px] px-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground" onClick={onClick}><Icon className="size-5 text-primary" /><span className="truncate">{label}</span></button>
}

function MenuGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="overflow-hidden self-start rounded-[12px] border bg-card shadow-sm"><h2 className="border-b px-4 py-4 text-base font-semibold">{title}</h2><div>{children}</div></section>
}

function MenuRow({ icon: Icon, label, note, onClick }: { icon: LucideIcon; label: string; note?: string; onClick: () => void }) {
  return <button type="button" className="group flex min-h-[56px] w-full items-center gap-3 border-b px-4 text-left transition-colors last:border-0 hover:bg-accent/40" onClick={onClick}><Icon className="size-4 shrink-0 text-muted-foreground transition-colors group-hover:text-primary" /><span className="min-w-0 flex-1 truncate text-sm font-medium">{label}</span>{note && <span className="shrink-0 text-xs text-muted-foreground">{note}</span>}<ChevronRight className="size-4 shrink-0 text-muted-foreground" /></button>
}
