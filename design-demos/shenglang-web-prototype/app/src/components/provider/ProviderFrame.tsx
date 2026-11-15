import { useState, type ReactNode } from 'react'
import { ClipboardCheck, FileCheck2, MessageSquareText, UserRound, WalletCards } from 'lucide-react'
import { NavLink, useLocation, useNavigate } from 'react-router'
import { MobileDock } from '@/components/prototype/MobileDock'
import { PhoneFrameContext } from '@/components/prototype/PhoneFrameContext'
import { useStore } from '@/components/store/store-context'
import { cn } from '@/lib/utils'

const navigation = [
  { to: '/provider', label: '接单', icon: ClipboardCheck, end: true },
  { to: '/provider/reports', label: '报单', icon: FileCheck2 },
  { to: '/provider/messages', label: '消息', icon: MessageSquareText, unread: 1 },
  { to: '/provider/wallet', label: '钱包', icon: WalletCards },
  { to: '/provider/mine', label: '我的', icon: UserRound },
]

export function ProviderFrame({ children }: { children: ReactNode }) {
  const [screen, setScreen] = useState<HTMLDivElement | null>(null)
  const location = useLocation()
  const navigate = useNavigate()
  const { store } = useStore()
  const active = navigation.find(item => item.end ? location.pathname === item.to : location.pathname.startsWith(item.to))?.to || '/provider'
  const dockItems = navigation.map(item => ({ key: item.to, label: item.label, icon: item.icon, unread: item.unread }))
  return (
    <PhoneFrameContext.Provider value={screen}>
      <div className="phone-frame" aria-label={`${store.name}陪玩工作台响应式原型`} data-store-scope={store.slug} data-store-page={location.pathname}>
        <div ref={setScreen} className="phone-screen">
          <div className="phone-scroll phone-scrollbar phone-scroll-with-dock" data-testid="provider-phone-scroll">
            <header className="sticky top-0 z-30 h-16 border-b bg-background/90 backdrop-blur-xl">
              <div className="mx-auto flex h-full max-w-[1120px] items-center gap-3 px-3 md:gap-5 md:px-6">
                <NavLink to="/provider" className="flex shrink-0 items-center gap-2 rounded-lg" aria-label={`${store.name}陪玩工作台首页`}>
                  <img src="./brand.svg" alt="" className="size-8" />
                  <span className="hidden min-[360px]:block"><span className="block max-w-24 truncate text-sm font-semibold leading-5">{store.shortName}</span><span className="block text-xs leading-4 text-muted-foreground">陪玩工作台</span></span>
                </NavLink>
                <nav className="hidden items-center gap-1 md:flex" aria-label="陪玩端桌面导航">
                  {navigation.map(item => {
                    const Icon = item.icon
                    const itemActive = item.end ? location.pathname === item.to : location.pathname.startsWith(item.to)
                    return <NavLink key={item.to} to={item.to} end={item.end} className={cn('flex h-11 items-center gap-2 rounded-lg px-3 text-sm text-muted-foreground hover:bg-accent hover:text-accent-foreground', itemActive && 'bg-primary/10 font-medium text-primary')}><Icon className="size-4" />{item.label}</NavLink>
                  })}
                </nav>
              </div>
            </header>
            <div className="boss-h5-content">{children}</div>
          </div>
          <MobileDock items={dockItems} active={active} ariaLabel="陪玩端底部导航" onNavigate={path => navigate(path)} />
        </div>
      </div>
    </PhoneFrameContext.Provider>
  )
}
