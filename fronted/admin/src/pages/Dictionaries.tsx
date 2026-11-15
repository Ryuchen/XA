import { useLayoutEffect, useRef, useState, type ReactNode } from "react"
import { Link, useLocation, useSearchParams } from "react-router"
import { useCurrentStore } from "@xa/shared"
import { ArrowRight, ChartColumn, ChevronRight, ClipboardCheck, Package, Send, Users, Wallet, type LucideIcon } from "lucide-react"
import { request } from "@/api/request"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { useLoad, type Row } from "@/components/business"
import { DataRegion, ReadErrorNotice } from "@/components/page-data-state"
import PointsRedemptionConfig from "@/components/PointsRedemptionConfig"
import PointsUsageConfig from "@/components/PointsUsageConfig"
import DefaultCommission from "@/components/CommissionEditor"
import { useAdminAuth } from "@/lib/auth"
import { checklistEditor, dictionaryTypes, quickConfigGroups, type ConfigEditor, type QuickCatalog, type Shortcut } from "@/lib/quick-config"
import { SourcePageHeader } from "@/pages/Catalog"
import ConfigurationChecklist from "@/pages/ConfigurationChecklist"
import DictionaryItems from "@/pages/DictionaryItems"
import GameCommissionDraft from "@/pages/GameCommissionDraft"
import OrderConfig from "@/pages/OrderConfig"
import OrderAccountFields from "@/pages/OrderAccountFields"
import { WithdrawConfig } from "@/pages/Financials"

const groupIcons: Record<string, LucideIcon> = {
  "商品与下单": Package, "派单与接单": Send, "会员与陪玩": Users, "资金与结算": Wallet, "薪资与经营": ChartColumn,
}

function ShortcutEntry({ item, open }: { item: Shortcut; open: (editor: ConfigEditor) => void }) {
  const className = "group flex w-full items-center justify-between gap-3 rounded-md px-3 py-3 text-left hover:bg-accent focus-visible:bg-accent focus-visible:outline-2 focus-visible:outline-ring"
  const content = <>
    <span className="min-w-0">
      <span className="block text-sm font-semibold text-foreground group-hover:text-primary">{item.title}</span>
      <span className="mt-1 block text-xs leading-5 text-muted-foreground">{item.description}</span>
    </span>
    <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-muted-foreground group-hover:text-primary" />
  </>
  return "to" in item
    ? <Link to={item.to} className={className}>{content}</Link>
    : <button type="button" aria-haspopup="dialog" onClick={() => open(item.editor)} className={className}>{content}</button>
}

function CommissionDraftEntry({ onClose }: { onClose: () => void }) {
  const draft = useLoad(async () => (await request<Row>({ url: "/config/game-commissions/", silent: true })).data)
  if (draft.data?.capability === "GAME_COMMISSION_DRAFT_V1")
    return <GameCommissionDraft catalog={draft.data} error={draft.error} onClose={onClose} onSaved={draft.reload} />
  return <Dialog open onOpenChange={open => { if (!open) onClose() }}>
    <DialogContent>
      <DialogHeader><DialogTitle>游戏抽成草稿</DialogTitle><DialogDescription>读取本店的游戏抽成配置。</DialogDescription></DialogHeader>
      <DataRegion label="游戏抽成配置" loading={draft.loading} hasSnapshot={!!draft.data} error={draft.error} retry={draft.reload}>
        <p className="text-sm">当前门店接口尚未提供游戏抽成草稿。</p>
      </DataRegion>
    </DialogContent>
  </Dialog>
}

export default function DictionariesPage() {
  const store = useCurrentStore()
  const auth = useAdminAuth()
  const [params] = useSearchParams()
  const location = useLocation()
  const section = params.get("section")
  const setup = store.status === "CONFIGURING"
  const permissionScope = JSON.stringify([auth.profile?.is_superuser, auth.profile?.permissions])
  const catalog = useLoad(async () => (await request<Row & QuickCatalog>({ url: "/dictionaries/" })).data, [permissionScope, store.slug])
  const groups = quickConfigGroups(catalog.data, setup, auth.hasPerm)
  const [opened, setOpened] = useState<{ editor: ConfigEditor; scope: string } | null>(null)
  const scope = JSON.stringify([auth.revision, store.slug, store.status, permissionScope, location.key])
  const trigger = useRef<HTMLElement | null>(null)
  const previousSection = useRef(section)
  useLayoutEffect(() => {
    if (section === previousSection.current) return
    previousSection.current = section
    window.scrollTo({ top: 0, behavior: "instant" })
    document.getElementById("main-content")?.focus({ preventScroll: true })
  }, [section])
  const open = (editor: ConfigEditor) => {
    trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setOpened({ editor, scope })
  }
  const close = () => {
    setOpened(null)
    void catalog.reload()
    requestAnimationFrame(() => trigger.current?.focus({ preventScroll: true }))
  }
  const type = dictionaryTypes.find(item => item.code === section)
  const showChecklist = !catalog.data || catalog.data.setup_evidence_capability === "SELECTED_RULES_V1"
  const fromChecklist = section === "checklist" && showChecklist
  const editor = opened?.scope === scope ? opened.editor : null
  const allowed = groups.some(group => group.entries.some(entry => "editor" in entry && entry.editor === editor))
  // Checklist evidence supplies its own scoped access during store setup and for authorized reviewers.
  const allowedByChecklist = fromChecklist && catalog.data?.setup_evidence?.some((row: Row) =>
    row.status !== "RESTRICTED" && checklistEditor(row.key, setup, auth.hasPerm) === editor)
  const visibleEditor = (allowed || allowedByChecklist) ? editor : null
  let page: ReactNode
  if (type) {
    page = <>
      <ReadErrorNotice error={catalog.error} retry={catalog.reload} hasSnapshot={!!catalog.data} />
      <DictionaryItems key={type.code} type={type.code} title={type.title} description={type.description}
        catalogReady={!!catalog.data && !catalog.loading && !catalog.error} reloadCatalog={catalog.reload} />
    </>
  } else if (fromChecklist) {
    page = <ConfigurationChecklist catalog={catalog} openEditor={open} />
  } else if (section === "points") {
    page = <div className="space-y-4">
      <SourcePageHeader title="积分规则" description="核对本店积分的抵扣比例、单笔上限和适用范围。">
        <Button variant="outline" asChild><Link to="/dictionaries">返回快捷配置</Link></Button>
      </SourcePageHeader>
      <PointsRedemptionConfig /><PointsUsageConfig />
    </div>
  } else {
    page = <div className="admin-source-page space-y-4">
      <SourcePageHeader title="快捷配置" description="选择要调整的本店规则，直接进入对应业务页面或配置窗口。" />
      {showChecklist && <Link to="/dictionaries?section=checklist" className="flex items-center gap-4 rounded-lg border bg-accent/40 px-5 py-4 hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring">
        <ClipboardCheck aria-hidden="true" className="size-5 text-primary" />
        <span className="flex-1"><strong className="block text-sm">{setup ? "开店设置清单" : "核心配置核对"}</strong>
          <span className="mt-1 block text-xs leading-5 text-muted-foreground">查看接单、押金和提现等规则的保存与确认记录。</span></span>
        <ArrowRight aria-hidden="true" className="size-4 text-primary" />
      </Link>}
      <ReadErrorNotice error={catalog.error} retry={catalog.reload} hasSnapshot={!!catalog.data} />
      {catalog.loading && !catalog.data && <div role="status" aria-label="正在读取业务配置入口" className="h-12 animate-pulse rounded-lg bg-muted motion-reduce:animate-none" />}
      {groups.map(({ title, entries }) => {
        const Icon = groupIcons[title]
        return <section key={title} aria-label={title} className="rounded-lg border bg-card px-4 py-3 text-card-foreground">
        <h2 className="flex items-center gap-2 border-b px-3 pb-3 pt-1 text-sm font-semibold"><Icon className="size-4 text-primary" aria-hidden="true" />{title}</h2>
        <ul className="mt-1 grid grid-cols-2 gap-x-4 xl:grid-cols-3">
          {entries.map(item => <li key={item.title}><ShortcutEntry item={item} open={open} /></li>)}
        </ul>
      </section>})}
    </div>
  }
  return <>
    {page}
    {visibleEditor === "order-account-fields" && <OrderAccountFields onClose={close} />}
    {visibleEditor === "game-commission" && <CommissionDraftEntry onClose={close} />}
    {visibleEditor === "withdraw" && <WithdrawConfig onClose={close} />}
    {visibleEditor === "commission" && <DefaultCommission onClose={close} />}
    {(visibleEditor === "capacity" || visibleEditor === "grab" || visibleEditor === "timeout" || visibleEditor === "traffic") && <OrderConfig kind={visibleEditor} onClose={close} />}
  </>
}
