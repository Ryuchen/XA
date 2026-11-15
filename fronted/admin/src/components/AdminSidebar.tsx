import { useEffect, useRef, useState } from "react"
import { NavLink } from "react-router"
import {
  Building2,
  ChartNoAxesCombined,
  ChevronDown,
  ClipboardList,
  Headphones,
  LayoutDashboard,
  MessagesSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Pin,
  PinOff,
  Search,
  Send,
  Settings2,
  ShoppingBag,
  Users,
  Wallet,
  X,
} from "lucide-react"
import type { StoreInfo } from "@xa/shared"
import { AdminStoreSwitcher } from "@/components/AdminStoreSwitcher"
import { StoreLogo } from "@/components/StoreLogo"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { searchNavigation, type NavigationDomain } from "@/lib/navigation"

// Domain artwork never depends on the first page visible to a role.
const domainPresentation = {
  overview: { icon: LayoutDashboard, label: "工作台" },
  orders: { icon: ClipboardList, label: "订单" },
  providers: { icon: Headphones, label: "陪玩" },
  customers: { icon: Users, label: "客户" },
  "catalog-marketing": { icon: ShoppingBag, label: "商品" },
  support: { icon: MessagesSquare, label: "客服" },
  finance: { icon: Wallet, label: "财务" },
  analytics: { icon: ChartNoAxesCombined, label: "经营" },
  system: { icon: Settings2, label: "设置" },
  "platform-stores": { icon: Building2, label: "平台" },
}

interface AdminSidebarProps {
  domains: NavigationDomain[]
  activeDomainId?: string
  currentPath: string
  store: StoreInfo
  brandName: string
  platformAdmin: boolean
  canDispatch: boolean
  onQuickDispatch: () => void
  expanded: boolean
  onExpandedChange: (expanded: boolean) => void
  locked: boolean
  onToggleLock: () => void
}

export function AdminSidebar({
  domains,
  activeDomainId,
  currentPath,
  store,
  brandName,
  platformAdmin,
  canDispatch,
  onQuickDispatch,
  expanded,
  onExpandedChange,
  locked,
  onToggleLock,
}: AdminSidebarProps) {
  const [browsedDomain, setBrowsedDomain] = useState<{
    id: string
    path: string
  } | null>(null)
  const [flyout, setFlyout] = useState(false)
  const [closedGroups, setClosedGroups] = useState<Set<string>>(() => new Set())
  const [searchOpen, setSearchOpen] = useState(false)
  const [search, setSearch] = useState("")
  const sidebarRef = useRef<HTMLElement>(null)
  const searchInputRef = useRef<HTMLInputElement>(null)
  const resultsRef = useRef<HTMLElement>(null)
  const searchTriggerRef = useRef<HTMLButtonElement>(null)
  const domainId =
    (browsedDomain?.path === currentPath ? browsedDomain.id : null) ||
    activeDomainId ||
    domains[0]?.id
  const openDomain =
    domains.find((domain) => domain.id === domainId) || domains[0]
  const flyoutVisible =
    !expanded && flyout && browsedDomain?.path === currentPath
  const panelVisible = expanded || flyoutVisible
  const searchResults = searchNavigation(domains, search)
  const showDispatchCommand = canDispatch && search.trim().split(/\s+/).every(term => "快捷派单 客服手动派单 创建订单".includes(term))
  const resultCount = searchResults.length + Number(showDispatchCommand)

  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        if (event.isComposing || (!searchOpen && document.querySelector('[role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"]'))) return
        event.preventDefault()
        setFlyout(false)
        setSearchOpen(true)
        searchInputRef.current?.focus()
      }
      if (event.key === "Escape" && flyoutVisible && !searchOpen) {
        setFlyout(false)
        sidebarRef.current
          ?.querySelector<HTMLButtonElement>(
            `[data-admin-domain="${domainId}"]`
          )
          ?.focus()
      }
    }
    const pointerdown = (event: PointerEvent) => {
      if (flyoutVisible && !sidebarRef.current?.contains(event.target as Node))
        setFlyout(false)
    }
    window.addEventListener("keydown", keydown)
    window.addEventListener("pointerdown", pointerdown)
    return () => {
      window.removeEventListener("keydown", keydown)
      window.removeEventListener("pointerdown", pointerdown)
    }
  }, [domainId, flyoutVisible, searchOpen])

  const selectPage = (path: string) => {
    const targetDomain = domains.find((domain) =>
      domain.centers.some((center) =>
        center.features.some((feature) => feature.path === path)
      )
    )
    const targetCenter = targetDomain?.centers.find((center) =>
      center.features.some((feature) => feature.path === path)
    )
    if (targetCenter)
      setClosedGroups((previous) => {
        const next = new Set(previous)
        next.delete(targetCenter.id)
        return next
      })
    if (!expanded && !searchOpen && targetDomain)
      sidebarRef.current
        ?.querySelector<HTMLButtonElement>(
          `[data-admin-domain="${targetDomain.id}"]`
        )
        ?.focus()
    setFlyout(false)
    setBrowsedDomain(null)
    setSearchOpen(false)
    setSearch("")
  }
  const primaryButton = (domain: NavigationDomain) => {
    const presentation =
      domainPresentation[domain.id as keyof typeof domainPresentation]
    const Icon = presentation?.icon || Settings2
    return (
      <Button
        key={domain.id}
        type="button"
        variant="ghost"
        data-admin-primary-nav={domain.centers[0].features[0].path}
        data-admin-domain={domain.id}
        aria-label={domain.title}
        title={domain.title}
        aria-current={activeDomainId === domain.id ? "true" : undefined}
        aria-expanded={panelVisible && openDomain?.id === domain.id}
        aria-controls="admin-domain-panel"
        className="admin-rail-item"
        onClick={() => {
          setSearchOpen(false)
          setBrowsedDomain({ id: domain.id, path: currentPath })
          if (!expanded)
            setFlyout(!(flyoutVisible && openDomain?.id === domain.id))
        }}
      >
        <Icon className="size-4" strokeWidth={1.75} aria-hidden="true" />
        <span>{presentation?.label || domain.title}</span>
      </Button>
    )
  }

  return (
    <aside
      ref={sidebarRef}
      className="admin-sidebar"
      aria-label="后台业务导航"
      data-admin-sidebar
      data-sidebar-expanded={expanded}
      data-sidebar-locked={locked}
      data-sidebar-flyout={flyoutVisible}
      data-sidebar-mode="rail-panel"
    >
      <div className="admin-sidebar-rail" data-admin-primary-rail>
        <div className="admin-rail-brand" data-admin-brand title={brandName}>
          <StoreLogo
            store={
              platformAdmin ? { name: brandName, slug: "platform" } : store
            }
            className="size-9"
          />
        </div>
        <div className="admin-rail-search">
          <Popover open={searchOpen} onOpenChange={setSearchOpen}>
            <PopoverTrigger asChild>
              <Button
                ref={searchTriggerRef}
                type="button"
                variant="ghost"
                className="admin-rail-item"
                data-admin-search-rail
                aria-label="搜索后台功能"
                title="搜索后台功能（Ctrl / ⌘ K）"
                onClick={() => setFlyout(false)}
              >
                <Search className="size-4" strokeWidth={1.75} />
                <span>搜索</span>
              </Button>
            </PopoverTrigger>
            <PopoverContent
              side="right"
              align="start"
              sideOffset={12}
              className="admin-nav-search"
              aria-label="后台功能搜索"
              onOpenAutoFocus={(event) => {
                event.preventDefault()
                searchInputRef.current?.focus()
              }}
            >
              <div className="admin-search-heading">
                <strong><label htmlFor="admin-function-search">搜索功能与操作</label></strong>
                <kbd>Ctrl / ⌘ K</kbd>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label="关闭功能搜索"
                  onClick={() => setSearchOpen(false)}
                >
                  <X className="size-4" />
                </Button>
              </div>
              <div className="admin-search-input" data-admin-sidebar-search>
                <Search className="size-4" aria-hidden="true" />
                <Input
                  id="admin-function-search"
                  ref={searchInputRef}
                  aria-label="搜索后台功能"
                  placeholder="搜索页面或操作"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.nativeEvent.isComposing) return
                    const firstResult = resultsRef.current?.querySelector<HTMLElement>("button, a")
                    if (event.key === "ArrowDown") {
                      event.preventDefault()
                      firstResult?.focus()
                    }
                    if (event.key === "Enter" && search.trim() && firstResult) {
                      event.preventDefault()
                      firstResult.click()
                    }
                  }}
                />
                {search && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="清空搜索"
                    onClick={() => {
                      setSearch("")
                      searchInputRef.current?.focus()
                    }}
                  >
                    <X className="size-4" />
                  </Button>
                )}
              </div>
              <div className="admin-search-summary" role="status">
                {search.trim()
                  ? `找到 ${resultCount} 项结果`
                  : "可用页面与操作"}
              </div>
              <nav
                className="admin-search-results"
                aria-label="功能搜索结果"
                data-admin-sidebar-search-results
                ref={resultsRef}
                onKeyDown={(event) => {
                  if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return
                  const current = (event.target as HTMLElement).closest("a, button")
                  if (!current) return
                  event.preventDefault()
                  const next = event.key === "ArrowDown" ? current.nextElementSibling : current.previousElementSibling
                  if (next instanceof HTMLElement) next.focus()
                  else if (event.key === "ArrowUp") searchInputRef.current?.focus()
                }}
              >
                {showDispatchCommand && <button type="button" onClick={() => {
                  selectPage("orders")
                  onQuickDispatch()
                }}>
                  <span>快捷派单</span>
                  <small>操作 · 打开派单表单</small>
                </button>}
                {searchResults.map((feature) => (
                  <NavLink
                    key={feature.path}
                    to={`/${feature.path}`}
                    onClick={() => selectPage(feature.path)}
                  >
                    <span>{feature.title}</span>
                    <small>
                      {feature.domain} / {feature.center}
                    </small>
                  </NavLink>
                ))}
                {!resultCount && (
                  <p>没有找到可访问的功能，请尝试其他关键词。</p>
                )}
              </nav>
              {resultCount > 0 && <small className="admin-search-summary">↑ ↓ 选择　Enter 打开　Esc 关闭</small>}
            </PopoverContent>
          </Popover>
        </div>
        <nav className="admin-navigation" aria-label="一级功能导航">
          {domains
            .filter((domain) => domain.id !== "platform-stores")
            .map(primaryButton)}
        </nav>
        <div className="admin-rail-bottom">
          {platformAdmin && (
            <section
              className="admin-platform-slot"
              aria-label="平台管理"
              data-admin-platform-navigation
            >
              {domains
                .filter((domain) => domain.id === "platform-stores")
                .map(primaryButton)}
            </section>
          )}
          {canDispatch && (
            <Button
              type="button"
              className="admin-rail-item admin-dispatch-button"
              data-admin-quick-dispatch
              aria-label="快捷派单"
              title="快捷派单"
              onClick={() => {
                setFlyout(false)
                setBrowsedDomain(null)
                onQuickDispatch()
              }}
            >
              <Send className="size-4" strokeWidth={1.75} />
              <span>派单</span>
            </Button>
          )}
          <Button
            type="button"
            variant="ghost"
            className="admin-rail-item admin-sidebar-toggle"
            data-admin-sidebar-toggle
            aria-label={expanded ? "收起菜单" : "展开菜单"}
            title={
              locked
                ? "取消锁定后可收起菜单"
                : expanded
                  ? "收起菜单"
                  : "展开菜单"
            }
            disabled={expanded && locked}
            onClick={() => {
              setFlyout(false)
              onExpandedChange(!expanded)
            }}
          >
            {expanded ? (
              <PanelLeftClose className="size-4" />
            ) : (
              <PanelLeftOpen className="size-4" />
            )}
            <span>{expanded ? "收起" : "展开"}</span>
          </Button>
        </div>
      </div>
      <div
        className="admin-sidebar-panel"
        data-admin-secondary-panel
        hidden={!panelVisible}
        onBlur={(event) => {
          if (
            !expanded &&
            event.relatedTarget &&
            !sidebarRef.current?.contains(event.relatedTarget as Node)
          )
            setFlyout(false)
        }}
      >
        <div className="admin-panel-brand" data-admin-panel-brand>
          <strong>{brandName}</strong>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            data-admin-sidebar-lock
            onClick={onToggleLock}
            aria-label={locked ? "取消锁定菜单面板" : "锁定菜单面板"}
            aria-pressed={locked}
            title={locked ? "取消锁定菜单面板" : "锁定菜单面板，刷新后保持展开"}
          >
            {locked ? (
              <PinOff className="size-3.5" />
            ) : (
              <Pin className="size-3.5" />
            )}
          </Button>
        </div>
        {platformAdmin && (
          <div className="admin-panel-store">
            <span>当前管理门店</span>
            <AdminStoreSwitcher expanded />
          </div>
        )}
        <div className="admin-panel-heading">
          <h2>{openDomain?.title || "功能导航"}</h2>
          {!expanded && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="关闭浮层菜单"
              onClick={() => {
                setFlyout(false)
                sidebarRef.current
                  ?.querySelector<HTMLButtonElement>(
                    `[data-admin-domain="${domainId}"]`
                  )
                  ?.focus()
              }}
            >
              <X className="size-4" />
            </Button>
          )}
        </div>
        <nav
          id="admin-domain-panel"
          className="admin-domain-panel"
          aria-label="二级功能组与三级页面"
        >
          {openDomain && (
            <div
              data-admin-secondary-nav={openDomain.id}
              className="admin-domain-menu"
            >
              {openDomain.centers.map((center) => {
                const closed = closedGroups.has(center.id)
                return (
                  <section
                    key={center.id}
                    aria-label={center.title}
                    data-admin-secondary-center={center.id}
                  >
                    <h3>
                      <button
                        type="button"
                        className="admin-group-toggle"
                        data-current-group={center.features.some(feature => `/${feature.path}` === currentPath)}
                        aria-expanded={!closed}
                        aria-controls={`admin-group-${center.id}`}
                        onClick={() =>
                          setClosedGroups((previous) => {
                            const next = new Set(previous)
                            if (next.has(center.id)) next.delete(center.id)
                            else next.add(center.id)
                            return next
                          })
                        }
                      >
                        <span>{center.title}</span>
                        <ChevronDown className="size-3.5" />
                      </button>
                    </h3>
                    <div
                      id={`admin-group-${center.id}`}
                      className="admin-group-pages"
                      hidden={closed}
                    >
                      {center.features.map((feature) => (
                        <NavLink
                          key={feature.path}
                          to={`/${feature.path}`}
                          data-admin-secondary-page={feature.path}
                          onClick={() => selectPage(feature.path)}
                          className="admin-page-link"
                        >
                          {feature.title}
                        </NavLink>
                      ))}
                    </div>
                  </section>
                )
              })}
            </div>
          )}
        </nav>
      </div>
    </aside>
  )
}
