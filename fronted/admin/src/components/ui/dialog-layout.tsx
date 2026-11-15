import * as React from "react"
import { cn } from "cn"

type RegionProps = { children?: React.ReactNode; className?: string; style?: React.CSSProperties }
type RegionSlots = {
  header: React.ElementType
  footer: React.ElementType
  description: React.ElementType
  bodySlot: "dialog-body" | "alert-dialog-body"
  fallbackFooter?: React.ReactNode
}

function flatten(children: React.ReactNode): React.ReactNode[] {
  return React.Children.toArray(children).flatMap((child) =>
    React.isValidElement<RegionProps>(child) && child.type === React.Fragment
      ? flatten(child.props.children)
      : [child]
  )
}

/** Keep form ownership while giving only the content region a scroll container. */
export function dialogRegions(children: React.ReactNode, slots: RegionSlots): React.ReactNode {
  const headers: React.ReactNode[] = [], descriptions: React.ReactNode[] = [], body: React.ReactNode[] = [], footers: React.ReactNode[] = []
  for (const child of flatten(children)) {
    if (React.isValidElement<RegionProps>(child) && child.type === slots.header) {
      const heading: React.ReactNode[] = []
      for (const item of flatten(child.props.children)) {
        if (React.isValidElement<RegionProps>(item) && item.type === slots.description && !item.props.className?.includes("sr-only")) descriptions.push(item)
        else heading.push(item)
      }
      headers.push(React.cloneElement(child, undefined, heading))
    } else if (React.isValidElement(child) && child.type === slots.footer) footers.push(child)
    else body.push(child)
  }

  const forms = body.filter((child) => React.isValidElement(child) && child.type === "form")
  const hasOnlyForm = forms.length === 1 && body.length === 1
  let content: React.ReactNode = null
  if (hasOnlyForm) {
    const form = forms[0] as React.ReactElement<RegionProps>
    content = React.cloneElement(form, {
      className: cn(form.props.className, "min-h-0 w-full flex-1 space-y-0"),
      style: { ...form.props.style, display: "flex", flexDirection: "column", flex: "1 1 auto", minHeight: 0, overflow: "hidden", gap: 0, padding: 0, margin: 0 },
      children: dialogRegions([...descriptions.map((item, index) => React.isValidElement(item) ? React.cloneElement(item, { key: `dialog-summary-${index}` }) : item), ...flatten(form.props.children)], { ...slots, fallbackFooter: footers.length ? undefined : slots.fallbackFooter }),
    })
  } else if (body.length || descriptions.length) {
    content = <div key="dialog-body" data-slot={slots.bodySlot} className="min-w-0 space-y-4" style={{ minHeight: 0, flex: "1 1 auto", overflowX: "auto", overflowY: "auto", padding: "20px 24px" }}>{descriptions}{body}</div>
  }
  return <>{headers}{content}{footers.length ? footers : !hasOnlyForm ? slots.fallbackFooter : null}</>
}

export const dialogContentStyle: React.CSSProperties = { display: "flex", flexDirection: "column", gap: 0, padding: 0, overflow: "hidden", minHeight: 0 }
export const dialogFooterStyle: React.CSSProperties = { display: "flex", flexDirection: "row", flexWrap: "wrap", alignItems: "center", justifyContent: "flex-end", gap: 8, flexShrink: 0, padding: "16px 24px", borderTop: "1px solid var(--border)", background: "color-mix(in oklab, var(--muted) 30%, transparent)" }
