import { LockKeyhole } from "lucide-react"
import { SkipLink } from "./skip-link"
import "./account-login.css"
import { useId, type ReactNode } from "react"
import { Link } from "react-router"

export function AccountLoginField({
  label,
  htmlFor,
  children,
}: {
  label: string
  htmlFor: string
  children: ReactNode
}) {
  return <label className="grid gap-2 text-sm font-medium" htmlFor={htmlFor}>{label}{children}</label>
}

export function AccountLoginError({ message }: { message: string }) {
  if (!message) return null
  return <div role="alert" className="rounded-[8px] border border-destructive/30 bg-destructive/5 px-3 py-2.5 text-sm leading-6 text-destructive">{message}</div>
}

export function AccountLoginLayout({
  storeName,
  logoUrl,
  entryLabel,
  brandTo,
  brandAriaLabel,
  title,
  description,
  features = [],
  panelTitle,
  panelDescription,
  showBrandHeader = true,
  headerActions,
  children,
}: {
  storeName: string
  logoUrl?: string
  entryLabel: string
  brandTo: string
  brandAriaLabel: string
  title: ReactNode
  description: string
  features?: Array<{ icon: ReactNode; label: string }>
  panelTitle: string
  panelDescription: string
  showBrandHeader?: boolean
  headerActions?: ReactNode
  children: ReactNode
}) {
  const panelTitleId = useId()
  return <div className={`${showBrandHeader ? "min-h-dvh" : "min-h-[calc(100dvh-4rem)]"} bg-background text-foreground`}>
    <SkipLink />
    {showBrandHeader && <header className="sticky top-0 z-30 h-16 border-b bg-background/90 backdrop-blur-xl">
      <div className="mx-auto flex h-full max-w-[1120px] items-center px-4 md:px-6">
        <Link to={brandTo} className="account-login-brand flex items-center gap-2 rounded-[8px]" aria-label={brandAriaLabel}>
          <span className="account-login-logo-frame"><img src={logoUrl || `${import.meta.env.BASE_URL}brand.svg`} alt="" width={32} height={32} className="account-login-brand-mark size-8" /></span>
          <span><strong className="block text-sm font-semibold">{storeName}</strong><span className="block text-xs text-muted-foreground">{entryLabel}</span></span>
        </Link>
        {headerActions && <div className="ml-auto flex items-center">{headerActions}</div>}
      </div>
    </header>}
    <main id="main-content" tabIndex={-1} className="mx-auto grid w-full max-w-[1120px] items-center gap-6 px-4 py-6 sm:px-6 lg:min-h-[calc(100dvh-9rem)] lg:grid-cols-[minmax(0,1fr)_420px] lg:gap-16 lg:py-10">
      <section className="max-w-xl">
        <h1 className="text-3xl leading-tight font-semibold tracking-tight sm:text-4xl">{title}</h1>
        <p className="mt-4 max-w-md text-sm leading-7 text-muted-foreground">{description}</p>
        {features.length > 0 && <div className="mt-8 hidden grid-cols-3 gap-4 border-t pt-6 lg:grid">
          {features.map(feature => <span key={feature.label} className="flex items-center gap-2 text-sm"><span className="inline-flex text-primary">{feature.icon}</span>{feature.label}</span>)}
        </div>}
      </section>
      <section aria-labelledby={panelTitleId} className="w-full rounded-[12px] border bg-card p-5 shadow-sm sm:p-6 lg:max-w-[420px] lg:justify-self-end">
        <div className="flex items-start gap-3">
          <div className="account-login-panel-icon grid size-12 shrink-0 place-items-center rounded-[12px] border bg-card text-primary shadow-sm"><LockKeyhole className="size-5" /></div>
          <div className="min-w-0">
            <h2 id={panelTitleId} className="text-xl font-semibold">{panelTitle}</h2>
            <p className="mt-1.5 text-sm leading-6 text-muted-foreground">{panelDescription}</p>
          </div>
        </div>
        <div className="mt-5 grid gap-5">{children}</div>
      </section>
    </main>
  </div>
}
