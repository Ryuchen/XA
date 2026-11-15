import { useState, type ReactNode } from 'react'
import { ClipboardList, Headphones, Home, MessageSquare, UserRound } from 'lucide-react'
import { NavLink, useLocation } from 'react-router'
import { PhoneFrameContext } from '@/components/prototype/PhoneFrameContext'
import { useStore } from '@/components/store/store-context'
import { cn } from '@/lib/utils'

interface PhoneFrameProps {
  children: ReactNode
  bottomNavigation?: ReactNode
}

const navigation = [
  { to: '/home', label: '首页', icon: Home },
  { to: '/companions', label: '陪玩', icon: Headphones },
  { to: '/orders', label: '订单', icon: ClipboardList },
  { to: '/messages', label: '消息', icon: MessageSquare },
  { to: '/mine', label: '我的', icon: UserRound },
]

export function PhoneFrame({ children, bottomNavigation }: PhoneFrameProps) {
  const [screen, setScreen] = useState<HTMLDivElement | null>(null)
  const location = useLocation()
  const { store } = useStore()
  const loginPage = location.pathname === '/login'

  return (
    <PhoneFrameContext.Provider value={screen}>
      <div className="phone-frame" aria-label={`${store.name}老板端 H5 响应式原型`} data-store-scope={store.slug} data-store-page={location.pathname}>
        <div ref={setScreen} className="phone-screen">
          <div className={cn('phone-scroll phone-scrollbar', bottomNavigation && 'phone-scroll-with-dock')} data-testid="phone-scroll">
            <header className="sticky top-0 z-30 h-16 border-b bg-background/90 backdrop-blur-xl">
              <div className="mx-auto flex h-full max-w-[1120px] items-center gap-3 px-3 md:gap-5 md:px-6">
                <NavLink to="/home" className="flex shrink-0 items-center gap-2 rounded-lg focus-visible:ring-2 focus-visible:ring-ring" aria-label={`${store.name}老板端首页`}>
                  <img src="./brand.svg" alt="" className="size-8" />
                  <span className="hidden min-[360px]:block">
                    <span className="block max-w-24 truncate text-sm font-semibold leading-5">{store.shortName}</span>
                    <span className="block text-xs leading-4 text-muted-foreground">老板端 H5</span>
                  </span>
                </NavLink>
                {!loginPage && (
                  <nav className="hidden items-center gap-1 md:flex" aria-label="老板端桌面导航">
                    {navigation.map((item) => {
                      const Icon = item.icon
                      return (
                        <NavLink key={item.to} to={item.to} className={({ isActive }) => cn('flex h-11 items-center gap-2 rounded-lg px-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:ring-2 focus-visible:ring-ring', isActive && 'bg-primary/10 font-medium text-primary')}>
                          <Icon className="size-4" />
                          {item.label}
                        </NavLink>
                      )
                    })}
                  </nav>
                )}
              </div>
            </header>
            <div className="boss-h5-content">{children}</div>
          </div>
          {bottomNavigation}
        </div>
      </div>
    </PhoneFrameContext.Provider>
  )
}
