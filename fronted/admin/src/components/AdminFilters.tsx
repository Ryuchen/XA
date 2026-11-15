import { createContext, useId, useState, type ReactNode } from "react"
import { ChevronDown, Filter } from "@/components/ui/icons"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"

export const AdminFilterContext = createContext(false)

/** One table query, one set of controls, at most one advanced disclosure. */
export function AdminFilters({ primary, actions, children, title = "筛选条件", description, activeCount = 0, defaultOpen = false }: {
  primary: ReactNode
  actions?: ReactNode
  children?: ReactNode
  title?: string
  description?: string
  activeCount?: number
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const panelId = useId()
  return <AdminFilterContext.Provider value={true}><section className="admin-filters" aria-label={title} data-admin-filters>
    {description && <p className="admin-filters-description">{description}</p>}
    <div className="admin-filters-row">
      <div className="admin-filters-primary">{primary}</div>
      <div className="admin-filters-actions">
        {actions}
        {children && <Button type="button" variant="outline" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(value => !value)}>
          <Filter className="size-4" />{open ? "收起筛选" : "更多筛选"}
          {activeCount > 0 && <Badge variant="secondary">{activeCount}</Badge>}
          <ChevronDown className={`size-3.5 transition-transform motion-reduce:transition-none ${open ? "rotate-180" : ""}`} />
        </Button>}
      </div>
    </div>
    {children && open && <div id={panelId} className="admin-filters-advanced">{children}</div>}
  </section></AdminFilterContext.Provider>
}
