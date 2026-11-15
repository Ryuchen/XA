import { useState } from "react"
import { useNavigate } from "react-router"
import { toast } from "sonner"
import { Check, ChevronDown, LoaderCircle } from "@/components/ui/icons"
import { StoreLogo } from "@/components/StoreLogo"
import { isAdminEntry, persistSession, switchStore, useCurrentStore, useStoreDirectory, type StoreInfo } from "@xa/shared"
import { adminClient } from "@/api/request"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"

export function AdminStoreSwitcher({ expanded = true }: { expanded?: boolean }) {
  const store = useCurrentStore()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [pending, setPending] = useState<StoreInfo | null>(null)
  const canManageStores = store.can_manage_stores === true
  const directory = useStoreDirectory(open && canManageStores)
  const others = directory.stores.filter(item => item.slug !== store.slug)
  const [switching, setSwitching] = useState(false)

  if (!canManageStores) return <div className={`admin-store-trigger flex min-h-11 w-full items-center gap-2 rounded-md ${expanded ? "px-3" : "justify-center px-0"}`} aria-label={`当前管理后台门店：${store.name}，仅当前门店`} title={store.name}>
    <StoreLogo store={store} className="size-8" />
    <span className={expanded ? "min-w-0 flex-1 truncate text-sm font-medium" : "sr-only"}>{store.name}</span>
  </div>

  async function confirmSwitch() {
    const target = pending
    if (!target || store.can_manage_stores !== true || switching) return
    setSwitching(true)
    try {
      if (isAdminEntry()) {
        const result = await adminClient.post("/stores/switch/", { store_slug: target.slug })
        persistSession("admin", result)
        navigate("/dashboard")
      } else switchStore(target.slug)
      toast.success(`已切换至${target.name}`)
    } catch (cause: any) {
      toast.error(cause?.message || "门店切换失败，请重试")
    } finally {
      setSwitching(false)
    }
  }

  return <>
    <DropdownMenu onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" title={store.name} className={`admin-store-trigger min-h-11 w-full min-w-0 gap-2 ${expanded ? "justify-start px-3" : "justify-center px-0"}`} aria-label={`当前管理后台门店：${store.name}，点击切换`}>
          <StoreLogo store={store} className="size-8" />
          <span className={expanded ? "min-w-0 flex-1 truncate text-left text-sm font-medium" : "sr-only"}>{store.name}</span>
          {expanded && <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side={expanded ? "bottom" : "right"} sideOffset={8} className="w-80 max-w-[calc(100vw-24px)]">
        <DropdownMenuLabel>切换管理后台门店</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled className="min-h-16">
          <StoreLogo store={store} className="size-8 border-0 shadow-none" />
          <span className="min-w-0 flex-1"><span className="flex items-center gap-2 text-sm font-medium"><span className="truncate">{store.name}</span><Badge variant="secondary" className="shrink-0 text-xs">当前</Badge></span><span className="mt-1 block text-xs text-muted-foreground">{store.currency_name || "浪花"}</span></span>
          <Check className="size-4 text-primary" />
        </DropdownMenuItem>
        {others.map(item => <DropdownMenuItem key={item.slug} className="min-h-16" onSelect={() => setPending(item)}>
          <StoreLogo store={item} className="size-8 border-0 shadow-none" />
          <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium">{item.name}</span><span className="mt-1 block text-xs text-muted-foreground">{item.currency_name || "浪花"}</span></span>
        </DropdownMenuItem>)}
        {directory.loading && <DropdownMenuItem disabled><LoaderCircle className="size-4 animate-spin" />正在读取门店</DropdownMenuItem>}
        {directory.error && <DropdownMenuItem onSelect={event => { event.preventDefault(); directory.reload() }}>读取失败，点击重试</DropdownMenuItem>}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => navigate("/stores")}>管理门店</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
    <AlertDialog open={pending !== null} onOpenChange={value => !value && setPending(null)}>
      <AlertDialogContent>
        <AlertDialogHeader><AlertDialogTitle>切换至{pending?.name}？</AlertDialogTitle><AlertDialogDescription>切换后直接读取目标门店数据，不会携带当前门店的草稿或未确认请求。平台授权会在服务端再次核验。</AlertDialogDescription></AlertDialogHeader>
        <AlertDialogFooter><AlertDialogCancel>留在{store.name}</AlertDialogCancel><AlertDialogAction disabled={switching} onClick={confirmSwitch}>{switching ? "正在切换…" : "确认切换"}</AlertDialogAction></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </>
}
