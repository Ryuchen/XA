import type { ReactNode } from 'react'
import {
  AudioLines,
  Award,
  BookOpenText,
  CalendarCheck2,
  CalendarDays,
  Check,
  ChevronRight,
  CircleUserRound,
  ClipboardList,
  Crown,
  Dices,
  Gamepad2,
  Gift,
  Heart,
  LayoutDashboard,
  LifeBuoy,
  LockKeyhole,
  LogIn,
  Megaphone,
  MessageSquare,
  Moon,
  MonitorSmartphone,
  ReceiptText,
  ShieldCheck,
  Smartphone,
  Sun,
  Ticket,
  Trophy,
  UserRound,
  UsersRound,
  WalletCards,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import type { BossPage, ScenarioOption } from '@/components/prototype/prototype-config'
import { ViewportPreview } from '@/components/prototype/ViewportPreview'
import { StoreDesignPanel } from '@/components/store/StoreDesignPanel'

interface PrototypeShellProps {
  children: ReactNode
  dark: boolean
  onThemeChange: (dark: boolean) => void
  currentPage: BossPage
  onPageChange: (page: BossPage) => void
  scenario: string
  onScenarioChange: (scenario: string) => void
  scenarioOptions: ScenarioOption[]
}

export function PrototypeShell({
  children,
  dark,
  onThemeChange,
  currentPage,
  onPageChange,
  scenario,
  onScenarioChange,
  scenarioOptions,
}: PrototypeShellProps) {
  const pageLabel = { login: '登录', home: '首页', companions: '陪玩列表', 'companion-picker': '挑选陪玩', 'companion-detail': '陪玩详情', catalog: '服务目录', 'service-detail': '服务详情', checkout: '确认下单', orders: '订单列表', 'order-detail': '订单详情', wallet: '钱包', gifts: '礼物单', mine: '我的', messages: '消息', chat: '平台会话', support: '客服与帮助', security: '账号安全', profile: '个人资料', reservations: '我的预约', 'reservation-detail': '预约详情', 'reservation-create': '新建预约', coupons: '优惠券', membership: '我的会员', points: '积分与转盘', lottery: '幸运转盘', 'reward-claim': '奖品交付', favorites: '我的收藏', checkin: '每日签到', achievements: '我的成就', rankings: '声浪榜单', announcements: '平台公告', 'announcement-detail': '公告详情', 'club-content': '俱乐部规范', 'club-content-detail': '规范详情', activities: '俱乐部活动', 'activity-detail': '活动海报', rights: '老板权益', 'rights-detail': '权益详情', 'point-rules': '积分规则', 'point-rules-detail': '积分规则详情', 'message-detail': '消息详情', audition: '老板试音邀请' }[currentPage]

  return (
    <div className="min-h-screen bg-background text-foreground lg:grid lg:grid-cols-[248px_minmax(0,1fr)]">
      <aside className="sticky top-0 hidden h-screen flex-col border-r bg-sidebar-background lg:flex">
        <div className="flex h-[72px] items-center gap-3 px-5">
          <div className="grid size-10 place-items-center rounded-xl bg-primary/10">
            <img src="./brand.svg" alt="" className="size-7" />
          </div>
          <div>
            <div className="text-[15px] font-semibold tracking-tight">声浪电竞</div>
            <div className="text-xs text-muted-foreground">产品设计原型</div>
          </div>
        </div>
        <Separator />
        <div className="flex-1 overflow-y-auto px-3 py-5">
          <p className="px-3 text-[11px] font-medium uppercase tracking-[0.16em] text-muted-foreground">产品</p>
          <div className="mt-3 space-y-1">
            <Button variant="secondary" className="h-11 w-full justify-start px-3 text-sm">
              <Smartphone className="size-4" />
              老板端 H5
              <Badge className="ml-auto px-1.5 text-[10px]">当前</Badge>
            </Button>
            <Button variant="ghost" className="h-11 w-full justify-start px-3 text-sm" onClick={() => { window.location.hash = '/admin-login' }}>
              <LayoutDashboard className="size-4" />
              管理后台
              <ChevronRight className="ml-auto size-4 text-muted-foreground" />
            </Button>
            <Button variant="ghost" className="h-11 w-full justify-start px-3 text-sm" onClick={() => { window.location.hash = '/provider' }}>
              <MonitorSmartphone className="size-4" />
              陪玩工作台
              <ChevronRight className="ml-auto size-4 text-muted-foreground" />
            </Button>
          </div>

          <p className="mt-8 px-3 text-[11px] font-medium uppercase tracking-[0.16em] text-muted-foreground">老板端页面</p>
          <div className="mt-3 space-y-1">
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'login' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'login' ? 'page' : undefined}
              onClick={() => onPageChange('login')}
            >
              <LogIn className="size-4" />
              登录
              {currentPage === 'login' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'home' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'home' ? 'page' : undefined}
              onClick={() => onPageChange('home')}
            >
              <Smartphone className="size-4" />
              首页
              {currentPage === 'home' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'companions' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'companions' ? 'page' : undefined}
              onClick={() => onPageChange('companions')}
            >
              <UsersRound className="size-4" />
              陪玩列表
              {currentPage === 'companions' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'companion-picker' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'companion-picker' ? 'page' : undefined}
              onClick={() => onPageChange('companion-picker')}
            >
              <UsersRound className="size-4" />
              挑选陪玩
              {currentPage === 'companion-picker' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'companion-detail' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'companion-detail' ? 'page' : undefined}
              onClick={() => onPageChange('companion-detail')}
            >
              <UsersRound className="size-4" />
              陪玩详情
              {currentPage === 'companion-detail' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'catalog' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'catalog' ? 'page' : undefined}
              onClick={() => onPageChange('catalog')}
            >
              <Gamepad2 className="size-4" />
              服务目录
              {currentPage === 'catalog' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'service-detail' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'service-detail' ? 'page' : undefined}
              onClick={() => onPageChange('service-detail')}
            >
              <Gamepad2 className="size-4" />
              服务详情
              {currentPage === 'service-detail' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'checkout' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'checkout' ? 'page' : undefined}
              onClick={() => onPageChange('checkout')}
            >
              <ClipboardList className="size-4" />
              确认下单
              {currentPage === 'checkout' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'orders' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'orders' ? 'page' : undefined}
              onClick={() => onPageChange('orders')}
            >
              <ReceiptText className="size-4" />
              订单列表
              {currentPage === 'orders' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'order-detail' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'order-detail' ? 'page' : undefined}
              onClick={() => onPageChange('order-detail')}
            >
              <ReceiptText className="size-4" />
              订单详情
              {currentPage === 'order-detail' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'wallet' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'wallet' ? 'page' : undefined}
              onClick={() => onPageChange('wallet')}
            >
              <WalletCards className="size-4" />
              钱包
              {currentPage === 'wallet' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'gifts' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'gifts' ? 'page' : undefined}
              onClick={() => onPageChange('gifts')}
            >
              <Gift className="size-4" />
              礼物单
              {currentPage === 'gifts' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'coupons' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'coupons' ? 'page' : undefined}
              onClick={() => onPageChange('coupons')}
            >
              <Ticket className="size-4" />
              优惠券
              {currentPage === 'coupons' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'membership' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'membership' ? 'page' : undefined}
              onClick={() => onPageChange('membership')}
            >
              <Crown className="size-4" />
              我的会员
              {currentPage === 'membership' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'points' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'points' ? 'page' : undefined}
              onClick={() => onPageChange('points')}
            >
              <Crown className="size-4" />
              积分与转盘
              {currentPage === 'points' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'lottery' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'lottery' ? 'page' : undefined}
              onClick={() => onPageChange('lottery')}
            >
              <Dices className="size-4" />
              幸运转盘
              {currentPage === 'lottery' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'reward-claim' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'reward-claim' ? 'page' : undefined}
              onClick={() => onPageChange('reward-claim')}
            >
              <Gift className="size-4" />
              奖品交付
              {currentPage === 'reward-claim' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'favorites' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'favorites' ? 'page' : undefined}
              onClick={() => onPageChange('favorites')}
            >
              <Heart className="size-4" />
              我的收藏
              {currentPage === 'favorites' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'checkin' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'checkin' ? 'page' : undefined}
              onClick={() => onPageChange('checkin')}
            >
              <CalendarCheck2 className="size-4" />
              每日签到
              {currentPage === 'checkin' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'achievements' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'achievements' ? 'page' : undefined}
              onClick={() => onPageChange('achievements')}
            >
              <Award className="size-4" />
              我的成就
              {currentPage === 'achievements' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'rankings' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'rankings' ? 'page' : undefined}
              onClick={() => onPageChange('rankings')}
            >
              <Trophy className="size-4" />
              声浪榜单
              {currentPage === 'rankings' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'announcements' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'announcements' ? 'page' : undefined}
              onClick={() => onPageChange('announcements')}
            >
              <Megaphone className="size-4" />
              平台公告
              {currentPage === 'announcements' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'announcement-detail' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'announcement-detail' ? 'page' : undefined}
              onClick={() => onPageChange('announcement-detail')}
            >
              <Megaphone className="size-4" />
              公告详情
              {currentPage === 'announcement-detail' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'club-content' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'club-content' ? 'page' : undefined}
              onClick={() => onPageChange('club-content')}
            >
              <BookOpenText className="size-4" />
              俱乐部规范
              {currentPage === 'club-content' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'club-content-detail' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'club-content-detail' ? 'page' : undefined}
              onClick={() => onPageChange('club-content-detail')}
            >
              <BookOpenText className="size-4" />
              规范详情
              {currentPage === 'club-content-detail' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'activities' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'activities' ? 'page' : undefined}
              onClick={() => onPageChange('activities')}
            >
              <CalendarDays className="size-4" />
              俱乐部活动
              {currentPage === 'activities' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'activity-detail' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'activity-detail' ? 'page' : undefined}
              onClick={() => onPageChange('activity-detail')}
            >
              <CalendarDays className="size-4" />
              活动海报
              {currentPage === 'activity-detail' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button variant="ghost" className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'rights' && 'bg-accent text-accent-foreground hover:bg-accent/90')} aria-current={currentPage === 'rights' ? 'page' : undefined} onClick={() => onPageChange('rights')}><Crown className="size-4" />老板权益{currentPage === 'rights' && <ChevronRight className="ml-auto size-4" />}</Button>
            <Button variant="ghost" className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'rights-detail' && 'bg-accent text-accent-foreground hover:bg-accent/90')} aria-current={currentPage === 'rights-detail' ? 'page' : undefined} onClick={() => onPageChange('rights-detail')}><Crown className="size-4" />权益详情{currentPage === 'rights-detail' && <ChevronRight className="ml-auto size-4" />}</Button>
            <Button variant="ghost" className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'point-rules' && 'bg-accent text-accent-foreground hover:bg-accent/90')} aria-current={currentPage === 'point-rules' ? 'page' : undefined} onClick={() => onPageChange('point-rules')}><Trophy className="size-4" />积分规则{currentPage === 'point-rules' && <ChevronRight className="ml-auto size-4" />}</Button>
            <Button variant="ghost" className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'point-rules-detail' && 'bg-accent text-accent-foreground hover:bg-accent/90')} aria-current={currentPage === 'point-rules-detail' ? 'page' : undefined} onClick={() => onPageChange('point-rules-detail')}><Trophy className="size-4" />积分规则详情{currentPage === 'point-rules-detail' && <ChevronRight className="ml-auto size-4" />}</Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'messages' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'messages' ? 'page' : undefined}
              onClick={() => onPageChange('messages')}
            >
              <MessageSquare className="size-4" />
              消息
              {currentPage === 'messages' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button variant="ghost" className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'message-detail' && 'bg-accent text-accent-foreground hover:bg-accent/90')} aria-current={currentPage === 'message-detail' ? 'page' : undefined} onClick={() => onPageChange('message-detail')}><MessageSquare className="size-4" />消息详情{currentPage === 'message-detail' && <ChevronRight className="ml-auto size-4" />}</Button>
            <Button variant="ghost" className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'audition' && 'bg-accent text-accent-foreground hover:bg-accent/90')} aria-current={currentPage === 'audition' ? 'page' : undefined} onClick={() => onPageChange('audition')}><AudioLines className="size-4" />老板试音邀请{currentPage === 'audition' && <ChevronRight className="ml-auto size-4" />}</Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'chat' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'chat' ? 'page' : undefined}
              onClick={() => onPageChange('chat')}
            >
              <MessageSquare className="size-4" />
              平台会话
              {currentPage === 'chat' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'support' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'support' ? 'page' : undefined}
              onClick={() => onPageChange('support')}
            >
              <LifeBuoy className="size-4" />
              客服与帮助
              {currentPage === 'support' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'reservations' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'reservations' ? 'page' : undefined}
              onClick={() => onPageChange('reservations')}
            >
              <CalendarCheck2 className="size-4" />
              我的预约
              {currentPage === 'reservations' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'reservation-detail' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'reservation-detail' ? 'page' : undefined}
              onClick={() => onPageChange('reservation-detail')}
            >
              <CalendarCheck2 className="size-4" />
              预约详情
              {currentPage === 'reservation-detail' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'reservation-create' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'reservation-create' ? 'page' : undefined}
              onClick={() => onPageChange('reservation-create')}
            >
              <CalendarCheck2 className="size-4" />
              新建预约
              {currentPage === 'reservation-create' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'profile' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'profile' ? 'page' : undefined}
              onClick={() => onPageChange('profile')}
            >
              <CircleUserRound className="size-4" />
              个人资料
              {currentPage === 'profile' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'security' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'security' ? 'page' : undefined}
              onClick={() => onPageChange('security')}
            >
              <ShieldCheck className="size-4" />
              账号安全
              {currentPage === 'security' && <ChevronRight className="ml-auto size-4" />}
            </Button>
            <Button
              variant="ghost"
              className={cn('h-11 w-full justify-start px-3 text-sm', currentPage === 'mine' && 'bg-accent text-accent-foreground hover:bg-accent/90')}
              aria-current={currentPage === 'mine' ? 'page' : undefined}
              onClick={() => onPageChange('mine')}
            >
              <UserRound className="size-4" />
              我的
              {currentPage === 'mine' && <ChevronRight className="ml-auto size-4" />}
            </Button>
          </div>
        </div>
        <div className="m-4 rounded-xl border bg-card p-4">
          <div className="flex items-center gap-2 text-sm font-medium">
            <AudioLines className="size-4 text-primary" />
            澄海 · 任务优先
          </div>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">统一三端视觉与交互语言，逐页推进高保真设计。</p>
        </div>
      </aside>

      <div className="min-w-0">
        <header className="sticky top-0 z-40 flex min-h-[72px] flex-wrap items-center gap-3 border-b bg-background/85 px-4 py-3 backdrop-blur-xl md:px-6">
          <div className="flex min-w-0 items-center gap-3 lg:hidden">
            <img src="./brand.svg" alt="" className="size-8 shrink-0" />
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">声浪电竞</p>
              <p className="text-xs text-muted-foreground">老板端 · {pageLabel}</p>
            </div>
          </div>
          <div className="hidden items-center gap-2 text-sm lg:flex">
            <span className="text-muted-foreground">老板端 H5</span>
            <ChevronRight className="size-4 text-muted-foreground" />
            <span className="font-medium">{pageLabel}</span>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Badge variant="outline" className="h-7 border-warning/40 bg-warning/10 px-2.5 text-xs font-medium text-foreground">示例数据</Badge>
            <Button
              variant="outline"
              size="icon"
              className="size-11 rounded-full"
              onClick={() => onThemeChange(!dark)}
              aria-label={dark ? '切换为浅色模式' : '切换为深色模式'}
            >
              {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </Button>
          </div>
        </header>

        <main className="prototype-canvas min-h-[calc(100dvh-72px)] px-4 py-6 md:px-6 md:py-8 xl:px-10">
          <div className="mx-auto flex max-w-[1440px] flex-col items-center gap-8 xl:grid xl:grid-cols-[minmax(0,1fr)_280px] xl:items-start xl:gap-10">
            <section className="flex w-full min-w-0 flex-col items-center" aria-label="页面预览">
              <ViewportPreview dark={dark} pageLabel={pageLabel} fitToContainer>{children}</ViewportPreview>
            </section>

            <aside className="w-full max-w-[390px] xl:sticky xl:top-[104px] xl:max-w-none" aria-label="原型场景控制">
              <div className="rounded-2xl border bg-card p-5 shadow-sm">
                <div className="flex items-center gap-2">
                  <UsersRound className="size-4 text-primary" />
                  <h2 className="text-sm font-semibold">页面状态</h2>
                </div>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">切换业务场景，检查每一层反馈。</p>
                <div className="mt-4 xl:hidden">
                  <Select value={scenario} onValueChange={onScenarioChange}>
                    <SelectTrigger className="h-11 w-full" aria-label={`选择${pageLabel}页面状态`}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {scenarioOptions.map((option) => (
                        <SelectItem key={option.value} value={option.value} className="min-h-11">{option.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="mt-4 hidden space-y-1 xl:block">
                  {scenarioOptions.map((option) => {
                    const selected = scenario === option.value
                    return (
                      <button
                        key={option.value}
                        type="button"
                        className={cn(
                          'flex min-h-[58px] w-full items-center gap-3 rounded-lg px-3 text-left transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring',
                          selected && 'bg-accent text-accent-foreground',
                        )}
                        onClick={() => onScenarioChange(option.value)}
                        aria-pressed={selected}
                      >
                        <span className={cn('grid size-5 shrink-0 place-items-center rounded-full border', selected && 'border-primary bg-primary text-primary-foreground')}>
                          {selected && <Check className="size-3" />}
                        </span>
                        <span>
                          <span className="block text-sm font-medium">{option.label}</span>
                          <span className="mt-0.5 block text-[11px] leading-4 text-muted-foreground">{option.description}</span>
                        </span>
                      </button>
                    )
                  })}
                </div>
              </div>

              <StoreDesignPanel product="boss" pageKey={currentPage} />

              <div className="mt-4 rounded-2xl border bg-card p-5">
                <div className="flex items-center gap-2 text-sm font-semibold">
                  <LockKeyhole className="size-4 text-primary" />
                  原型边界
                </div>
                <ul className="mt-3 space-y-2 text-xs leading-5 text-muted-foreground">
                  <li>不连接真实微信、账号或手机号</li>
                  <li>不读取订单、钱包及聊天数据</li>
                  <li>客服入口仅演示联系与反馈</li>
                </ul>
              </div>
            </aside>
          </div>
        </main>
      </div>
    </div>
  )
}
