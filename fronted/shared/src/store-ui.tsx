import { createContext, useContext, useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { ApiError, createApiClient } from "./client.ts";
import { clearSession, useSession, type Role } from "./session.ts";
import { adoptLegacyStoreStorage, canonicalizeStoreUrl, getStoreSlug, isAdminEntry, registerStoreCurrency, storeBrandLogo, switchStore, validStoreSlug } from "./store.ts";
import type { H5Entitlement, H5Branding } from "./saas";

export interface StoreInfo {
  id: number;
  slug: string;
  name: string;
  currency_name?: string;
  entity_prefix?: string | null;
  provider_name_prefix?: string;
  logo_url?: string;
  status: "ACTIVE" | "PROVISIONING" | "CONFIGURING" | "DISABLED";
  is_active: boolean;
  boss_url: string;
  provider_url: string;
  admin_url: string;
  can_manage_stores?: boolean;
  is_platform_admin?: boolean;
  capabilities?: string[];
  boss_h5?: { entitlement: H5Entitlement; branding: H5Branding };
}
const StoreContext = createContext<StoreInfo | null>(null);
const neutralAdminStore: StoreInfo = {
  id: 0, slug: "", name: "电竞运营管理平台", status: "ACTIVE", is_active: true,
  boss_url: "", provider_url: "", admin_url: "/", can_manage_stores: false,
};
function applyStoreFavicon(store: StoreInfo) {
  const href = storeBrandLogo(store, import.meta.env.BASE_URL);
  let link = document.querySelector<HTMLLinkElement>("link[rel~='icon']");
  if (!link) {
    link = document.createElement("link");
    link.rel = "icon";
    document.head.appendChild(link);
  }
  link.type = href.split("?")[0].endsWith(".svg") ? "image/svg+xml" : "image/png";
  link.href = href;
}
export function useCurrentStore(): StoreInfo {
  const value = useContext(StoreContext);
  if (!value) throw new Error("门店尚未确认，请重新打开门店入口");
  return value;
}

export function useStoreDirectory(open: boolean) {
  const store = useCurrentStore();
  const session = useSession("admin");
  const scope = `${store.slug}:${session.revision}`;
  const canRead = open && !!session.token && store.can_manage_stores === true;
  const [result, setResult] = useState<{ scope: string; stores: StoreInfo[]; loading: boolean; error: string }>({ scope: "", stores: [], loading: false, error: "" });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!canRead) return;
    const controller = new AbortController();
    setResult(previous => ({ scope, stores: previous.scope === scope ? previous.stores : [], loading: true, error: "" }));
    void createApiClient("admin").get<{ results: StoreInfo[] }>("/stores/available/", undefined, { signal: controller.signal })
      .then(value => {
        if (!Array.isArray(value?.results)) throw new Error("门店目录暂时无法读取，请重试");
        if (!controller.signal.aborted) setResult({ scope, stores: value.results.filter(row => validStoreSlug(row.slug) && row.is_active && row.status === "ACTIVE"), loading: false, error: "" });
      })
      .catch(cause => { if (!controller.signal.aborted) setResult(previous => ({ ...previous, scope, loading: false, error: cause instanceof Error ? cause.message : "门店目录暂时无法读取，请重试" })); });
    return () => controller.abort();
  }, [canRead, scope, revision]);
  return {
    stores: canRead && result.scope === scope ? result.stores : [],
    loading: canRead && (result.scope !== scope || result.loading),
    error: canRead && result.scope === scope ? result.error : "",
    reload: () => setRevision(value => value + 1),
  };
}

export function StoreChooser({ role, compact = false }: { role: Role; compact?: boolean }) {
  const store = useContext(StoreContext);
  const adminSession = useSession("admin");
  const inputId = useId();
  const [slug, setSlug] = useState(getStoreSlug);
  const [error, setError] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    if (role !== "admin" || !adminSession.token || store?.can_manage_stores !== true) return;
    try { switchStore(slug); } catch (cause) { setError((cause as Error).message); }
  }
  if (role !== "admin" || !adminSession.token || store?.can_manage_stores !== true || isAdminEntry()) return null;
  return <form onSubmit={submit} className={compact ? "flex flex-wrap items-center gap-2" : "space-y-2"}>
    <label className="text-sm" htmlFor={inputId}>门店标识</label>
    <input id={inputId} className="h-10 min-w-0 rounded-md border border-input bg-background px-3 text-sm text-foreground" value={slug} autoCapitalize="none" autoCorrect="off" spellCheck={false} maxLength={48} onChange={(event) => setSlug(event.target.value)} required />
    <button className="min-h-10 rounded-md border border-input bg-background px-3 text-sm font-medium text-foreground hover:bg-accent" type="submit">进入门店</button>
    {error && <p role="alert" className="w-full text-sm text-destructive">{error}</p>}
  </form>;
}

/** Resolve the actual store before any business component can read its APIs. */
export function StoreGate({ role, children }: { role: Role; children: ReactNode }) {
  const session = useSession(role);
  const slug = getStoreSlug();
  const neutralAdmin = role === "admin" && isAdminEntry() && !session.token;
  const [store, setStore] = useState<StoreInfo | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const refresh = () => setRetry((value) => value + 1);
    window.addEventListener("xa:store-updated", refresh);
    return () => window.removeEventListener("xa:store-updated", refresh);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    if (neutralAdmin) {
      setStore(null);
      canonicalizeStoreUrl();
      document.title = `${neutralAdminStore.name} · 管理后台`;
      applyStoreFavicon(neutralAdminStore);
      return;
    }
    if (!validStoreSlug(slug)) { setStore(null); setError("门店入口无效，请联系门店获取正确链接"); return; }
    try { canonicalizeStoreUrl(); adoptLegacyStoreStorage(); }
    catch { setStore(null); setError("无法保存门店状态或恢复原交易请求，请检查浏览器存储后重试"); return; }
    void createApiClient(role).get<StoreInfo>("/stores/current/", undefined, { signal: controller.signal, public: !session.token })
      .then((value) => {
        if (controller.signal.aborted) return;
        const configuringAdmin = role === "admin" && isAdminEntry() && !!session.token && value.status === "CONFIGURING";
        if (value.slug !== slug || (!configuringAdmin && (value.is_active === false || value.status !== "ACTIVE"))) throw new Error("该门店尚未开通或已停用，请联系门店负责人");
        registerStoreCurrency(slug, value.currency_name);
        setStore(value);
        document.title = `${value.name} · ${{ admin: "管理后台", boss: "老板服务中心", provider: "陪玩工作台" }[role]}`;
        applyStoreFavicon(value);
      })
      .catch((cause) => {
        if (controller.signal.aborted) return;
        if (session.token && cause instanceof ApiError && cause.status === 401) {
          clearSession(role);
          return;
        }
        setStore(null);
        setError(cause instanceof Error ? cause.message : "门店暂时无法读取，请重试");
      });
    return () => controller.abort();
  }, [role, slug, session.revision, neutralAdmin, retry]);
  if (neutralAdmin) return <StoreContext.Provider key={`admin:neutral:${session.revision}`} value={neutralAdminStore}>{children}</StoreContext.Provider>;
  if (!store || store.slug !== slug || error) return <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center gap-4 px-6 py-12 text-foreground">
    <h1 className="text-xl font-semibold">{error ? "暂时无法进入门店" : "正在确认门店…"}</h1>
    {error && <><p role="alert" className="text-sm text-destructive">{error}</p><button className="min-h-10 self-start rounded-md border px-4 text-sm" onClick={() => setRetry((value) => value + 1)}>重试</button><p className="text-sm text-muted-foreground">如仍无法进入，请联系当前门店。</p></>}
  </main>;
  return <StoreContext.Provider key={`${slug}:${session.revision}`} value={store}>{children}</StoreContext.Provider>;
}
