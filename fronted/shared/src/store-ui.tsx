import { createContext, useContext, useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { ApiError, createApiClient } from "./client.ts";
import { clearSession, useSession, type Role } from "./session.ts";
import { adoptLegacyStoreStorage, canonicalizeStoreUrl, getStoreSlug, hostStoreBrandName, hostStoreSlug, isAdminEntry, registerStoreCurrency, storeBrandLogo, switchStore, validStoreSlug } from "./store.ts";
import type { H5Entitlement, H5Branding } from "./saas";
import {
  disabledStoreAnnouncement,
  StoreEntryStatusPage,
  type StoreEntryIssue,
  type StoreEntryIssueKind,
} from "./store-closed";

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
// The same 403 is also used for routing and database failures.
const closedStoreMessage = "门店尚未开放或已停用，请联系门店管理员";

export function storeEntryStatusIssueKind(value: { status: unknown; is_active?: unknown }): StoreEntryIssueKind | null {
  if (value.status === "ACTIVE") return value.is_active === true ? null : "UNAVAILABLE";
  if (value.is_active !== false) return "UNAVAILABLE";
  if (value.status === "DISABLED") return "DISABLED";
  if (value.status === "PROVISIONING" || value.status === "CONFIGURING") return "NOT_OPEN";
  return "UNAVAILABLE";
}

export function storeEntryTransportIssueKind(cause: unknown): StoreEntryIssueKind {
  return cause instanceof ApiError && cause.status === 403 && cause.message === closedStoreMessage
    ? "NOT_OPEN"
    : "UNAVAILABLE";
}

const visitorStoreCheckTimeout = 10_000;
const entryNames = { admin: "管理后台", boss: "老板服务中心", provider: "陪玩工作台" };
const neutralAdminStore: StoreInfo = {
  id: 0, slug: "", name: "电竞运营管理平台", status: "ACTIVE", is_active: true,
  boss_url: "", provider_url: "", admin_url: "/", can_manage_stores: false,
};
function applyStoreFavicon(store: Pick<StoreInfo, "slug" | "logo_url">) {
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
  const scope = `${role}:${slug}:${session.revision}`;
  const [failure, setFailure] = useState<(StoreEntryIssue & {
    scope: string; attempt: number;
  }) | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const refresh = () => setRetry((value) => value + 1);
    window.addEventListener("xa:store-updated", refresh);
    return () => window.removeEventListener("xa:store-updated", refresh);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    function fail(message: string, kind: StoreEntryIssueKind = "UNAVAILABLE") {
      setStore(null);
      setFailure({ scope, attempt: retry, message, kind, checkedAt: new Date() });
      const entryStatus = kind === "DISABLED" ? "门店已停用" : kind === "NOT_OPEN" ? "门店暂未开放" : "暂时无法进入门店";
      const title = `${entryStatus} · ${entryNames[role]}`;
      const brandName = role === "admin" ? undefined : hostStoreBrandName(location.hostname);
      document.title = brandName ? `${brandName} · ${title}` : title;
      if (kind !== "UNAVAILABLE" && role !== "admin" && hostStoreSlug(location.hostname) === slug) applyStoreFavicon({ slug });
    }
    if (neutralAdmin) {
      setStore(null);
      setFailure(null);
      canonicalizeStoreUrl();
      document.title = `${neutralAdminStore.name} · 管理后台`;
      applyStoreFavicon(neutralAdminStore);
      return;
    }
    if (!validStoreSlug(slug)) { fail("门店入口无效，请联系门店获取正确链接"); return; }
    try { canonicalizeStoreUrl(); adoptLegacyStoreStorage(); }
    catch { fail("无法保存门店状态或恢复原交易请求，请检查浏览器存储后重试"); return; }
    const timeout = role === "admin" ? undefined : window.setTimeout(() => {
      controller.abort();
      fail("门店状态查询超时，请重试");
    }, visitorStoreCheckTimeout);
    void createApiClient(role).get<StoreInfo>("/stores/current/", undefined, { signal: controller.signal, public: !session.token })
      .then((value) => {
        if (controller.signal.aborted) return;
        if (!value || value.slug !== slug) { fail("门店信息与当前入口不一致，请核对门店链接"); return; }
        const configuringAdmin = role === "admin" && isAdminEntry() && !!session.token && value.status === "CONFIGURING";
        const issueKind = storeEntryStatusIssueKind(value);
        if (!configuringAdmin && issueKind !== null) {
          const message = issueKind === "DISABLED"
            ? disabledStoreAnnouncement
            : issueKind === "NOT_OPEN"
              ? closedStoreMessage
              : "无法确认门店营业状态，请稍后重试";
          fail(message, issueKind);
          return;
        }
        registerStoreCurrency(slug, value.currency_name);
        setFailure(null);
        setStore(value);
        document.title = `${value.name} · ${entryNames[role]}`;
        applyStoreFavicon(value);
      })
      .catch((cause) => {
        if (controller.signal.aborted) return;
        if (session.token && cause instanceof ApiError && cause.status === 401) {
          clearSession(role);
          return;
        }
        fail(
          cause instanceof Error ? cause.message : "门店暂时无法读取，请重试",
          storeEntryTransportIssueKind(cause),
        );
      })
      .finally(() => window.clearTimeout(timeout));
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [role, slug, scope, neutralAdmin, retry]);
  if (neutralAdmin) return <StoreContext.Provider key={`admin:neutral:${session.revision}`} value={neutralAdminStore}>{children}</StoreContext.Provider>;
  const issue = failure?.scope === scope ? failure : null;
  const error = issue?.attempt === retry ? issue.message : "";
  if (!store || store.slug !== slug || error) {
    if (role !== "admin") return <StoreEntryStatusPage role={role} entryName={entryNames[role]} issue={issue}
      checking={issue === null || issue.attempt !== retry} onRetry={() => setRetry(value => value + 1)} />;
    return <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center gap-4 px-6 py-12 text-foreground">
      <h1 className="text-xl font-semibold">{error ? "暂时无法进入门店" : "正在确认门店…"}</h1>
      {error && <><p role="alert" className="text-sm text-destructive">{error}</p><button className="min-h-10 self-start rounded-md border px-4 text-sm" onClick={() => setRetry((value) => value + 1)}>重试</button><p className="text-sm text-muted-foreground">如仍无法进入，请联系当前门店。</p></>}
    </main>;
  }
  return <StoreContext.Provider key={`${slug}:${session.revision}`} value={store}>{children}</StoreContext.Provider>;
}
