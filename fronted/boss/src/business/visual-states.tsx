import type { ReactNode } from "react"
import { Link } from "react-router"
import { CalendarDays, Gift, Headphones, Heart, Inbox, MessageSquareText, ShoppingBag, Trophy, WalletCards } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"

type EmptyCopy = [string, string]
const pageLabels: Record<string, string> = { home: "首页", catalog: "服务目录", companions: "陪玩列表", "companion-picker": "可选陪玩", "companion-detail": "陪玩详情", service: "服务详情", checkout: "下单信息", orders: "订单", "order-detail": "订单详情", wallet: "钱包", gifts: "礼物", mine: "个人中心", profile: "个人资料", security: "账号安全", messages: "消息", "message-detail": "通知详情", chat: "会话", support: "客服信息", reservations: "预约列表", "reservation-detail": "预约详情", "reservation-create": "预约信息", coupons: "优惠券", membership: "会员信息", points: "积分", "reward-claim": "交付信息", favorites: "收藏", checkin: "签到日历", achievements: "成就", rankings: "榜单", announcements: "公告", "announcement-detail": "公告详情", "club-content": "俱乐部内容", audition: "试音邀请", lottery: "抽奖活动" }
export const pageLabel = (page: string) => pageLabels[page] || "内容"
const emptyCopies: Record<string, EmptyCopy> = {
  catalog: ["没有符合条件的服务", "试试更换游戏、分类或清除筛选条件。"], companions: ["暂时没有符合条件的陪玩", "试试放宽游戏、语音或资格筛选。"], "companion-picker": ["暂时没有符合条件的陪玩", "可以调整筛选条件，或由平台安排。"], orders: ["还没有这类订单", "可以切换状态查看其他订单，或重新选择服务。"], reservations: ["还没有预约", "选好服务和陪玩后，就可以先确认合适的时间。"], wallet: ["暂无资金流水", "该分类下的资金变化会在这里记录。"], messages: ["还没有这类内容", "新的通知与俱乐部动态会在这里展示。"], chat: ["还没有会话消息", "可以发送文字与图片，补充需要核对的问题。"], coupons: ["暂无这类优惠券", "可以切换其他状态，或查看领券中心。"], favorites: ["还没有收藏的服务", "在服务详情点击收藏，常用的套餐就会出现在这里。"], gifts: ["暂无礼物", "礼物上架后会展示在这里，请稍后再来。"], points: ["暂无积分记录", "积分明细与兑换权益会在这里记录。"], achievements: ["成就内容准备中", "完成订单与净消费记录会继续保留。"], rankings: ["本期暂无上榜记录", "切换周期可查看其他时段的榜单。"], announcements: ["暂无平台公告", "新的服务通知与俱乐部活动会在这里发布。"], "club-content": ["暂无公开内容", "内容发布后会显示在这里，请稍后刷新。"], service: ["暂无服务评价", "完成服务后的评价会在这里展示。"], "reward-claim": ["奖品记录不存在", "请返回权益记录，或联系客服核对。"]
}
const icons = { wallet: WalletCards, gifts: Gift, orders: ShoppingBag, reservations: CalendarDays, messages: MessageSquareText, chat: MessageSquareText, points: Trophy, achievements: Trophy, favorites: Heart, companions: Headphones, "companion-picker": Headphones, "companion-detail": Headphones }
export function EmptySurface({ page, action, title, description }: { page: string; action?: ReactNode; title?: string; description?: string }) {
  const [defaultTitle, copy] = emptyCopies[page] || ["暂无相关内容", "新的内容会在这里展示。"], Icon = icons[page as keyof typeof icons] || Inbox
  return <section className="boss-empty-surface"><span><Icon size={28} /></span><h2>{title || defaultTitle}</h2><p>{description || copy}</p>{action || (["favorites", "orders", "reservations"].includes(page) ? <Button asChild><Link to="/catalog">去选择服务</Link></Button> : null)}</section>
}
export function PageSkeleton({ page, compact = false, detail = false, kind = "" }: { page: string; compact?: boolean; detail?: boolean; kind?: string }) {
  if (compact) return <div className="boss-skeleton-compact" aria-busy="true"><Skeleton className="h-4 w-2/3" /><Skeleton className="h-4 w-full" /><Skeleton className="h-4 w-1/2" /></div>
  const blocks = (heights: number[]) => heights.map((height, index) => <Skeleton key={index} className="w-full rounded-[12px]" style={{ height }} />)
  let content: ReactNode
  if (page === "home") content = <>{blocks([220, 144])}<div className="boss-skeleton-grid four">{blocks([88, 88, 88, 88])}</div></>
  else if (["catalog", "favorites"].includes(page)) content = <>{[1, 2, 3].map(id => <div className="boss-skeleton-service" key={id}><Skeleton className="h-28 w-24 shrink-0 rounded-[12px]" /><div>{blocks([20, 16, 16, 24])}</div></div>)}</>
  else if (page === "companion-picker") content = <div className="boss-skeleton-grid picker">{blocks([224, 224, 224, 224])}</div>
  else if (page === "companions") content = <><div className="boss-skeleton-grid two">{blocks([56, 56, 56, 56])}</div><div className="boss-skeleton-grid cards">{[1, 2, 3].map(id => <div className="boss-skeleton-provider" key={id}><div><Skeleton className="size-14 rounded-full" /><span>{blocks([20, 16])}</span></div>{blocks([32, 16, 20])}</div>)}</div></>
  else if (page === "orders") content = <div className="boss-skeleton-grid two">{blocks([236, 236, 220, 220])}</div>
  else if (page === "profile") content = <div className="boss-skeleton-account"><div>{blocks([384])}</div><div>{blocks([520])}</div></div>
  else if (page === "mine") content = <div className="boss-skeleton-account"><div>{blocks([288])}</div><div>{blocks([160, 160])}</div></div>
  else if (page === "chat") content = <div className="boss-skeleton-chat">{blocks([56, 88, 56])}</div>
  else if (page === "wallet") content = <>{blocks([208])}<div className="boss-skeleton-grid two">{blocks([384, 176])}</div></>
  else if (page === "messages") content = <>{blocks([96, 104, 104, 104])}</>
  else if (page === "checkin") content = <>{blocks([112, 128, 350])}</>
  else if (page === "lottery") content = <>{blocks([430, 128])}</>
  else if (page === "membership") content = <>{blocks([350, 256, 208])}</>
  else if (page === "points") content = <>{blocks([176, 176, 176])}</>
  else if (page === "reward-claim") content = <>{blocks([128, 176, 288])}</>
  else if (page === "achievements") content = <>{blocks([160, 192, 160, 160])}</>
  else if (page === "audition") content = <>{blocks([160, 330])}</>
  else if (page === "support") content = <>{blocks([128, 208, 176, 144])}</>
  else if (page === "security") content = <>{blocks([128])}<div className="boss-skeleton-grid two">{blocks([320, 320])}</div></>
  else if (page === "reservations") content = <>{blocks([144, 192, 192, 192])}</>
  else if (page === "gifts") content = <>{blocks([112])}<div className="boss-skeleton-grid two"><div className="boss-skeleton-grid picker">{blocks([220, 220, 220, 220])}</div><div>{blocks([256])}</div></div></>
  else if (page === "service") content = <>{blocks([288])}<div className="boss-skeleton-grid two">{blocks([160, 160])}</div>{blocks([176])}</>
  else if (page === "companion-detail") content = <><div className="boss-skeleton-portrait"><Skeleton className="size-24 rounded-full" /><Skeleton className="h-7 w-32" /><Skeleton className="h-4 w-44" /><Skeleton className="h-9 w-full" /></div>{blocks([96, 112, 128])}</>
  else if (page === "reservation-detail") content = <div className="boss-skeleton-reservation-form"><div>{blocks([208, 224, 192])}</div><div>{blocks([320])}</div></div>
  else if (page === "reservation-create") content = <div className="boss-skeleton-reservation-form"><div>{blocks([96, 320, 384])}</div><div>{blocks([320])}</div></div>
  else if (page === "checkout") content = <div className="boss-skeleton-grid two">{blocks([180, 250, 220, 180])}</div>
  else if (page === "message-detail") content = <>{blocks([360, 128])}</>
  else if (page === "announcements") content = <>{blocks([112, 320])}</>
  else if (["coupons", "gifts", "rankings"].includes(page)) content = <div className="boss-skeleton-grid two">{blocks(page === "coupons" ? [192, 192, 192] : page === "rankings" ? [320, 320] : [220, 220, 220, 220])}</div>
  else if (page === "club-content" && !detail) content = <>{blocks([kind === "RIGHTS" || kind === "POINT_RULES" ? 144 : 112, 108, 108, 108])}</>
  else if (page === "club-content" && detail) content = <>{blocks(kind === "HOT" ? [460, 44] : kind === "RIGHTS" || kind === "POINT_RULES" ? [192, 256, 288] : [160, 224, 320])}</>
  else if (["message-detail", "announcement-detail", "club-content"].includes(page)) content = <>{blocks([160, 320])}</>
  else if (["service", "order-detail", "reservation-detail", "companion-detail", "checkout", "reservation-create"].includes(page)) content = <>{blocks([208])}<div className="boss-skeleton-grid two">{blocks([256, 256, 180, 180])}</div></>
  else content = <>{blocks([128, 192, 160])}</>
  return <div className="boss-page-skeleton" data-skeleton-page={page} aria-busy="true" aria-label={"正在加载" + pageLabel(page)}>{content}</div>
}
