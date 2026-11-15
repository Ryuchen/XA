export const DEFAULT_STORE_SLUG = "shenglang";
export const STORE_HOSTS: Record<string, string> = {
  "sl.esports.club": "shenglang",
  "xa.esports.club": "xinan",
};
const ADMIN_STORE_KEY = "xa:fronted:admin:active-store:v1";
/** The platform console chooses its tenant only after account authentication. */
export function isAdminEntry(): boolean {
  if (typeof location === "undefined") return false;
  const hostname = (location.hostname || location.host || "").toLowerCase().split(":")[0].replace(/\.$/, "");
  const port = location.port || (location.host || "").split(":")[1] || "";
  return hostname === "admin.esports.club" ||
    (["localhost", "127.0.0.1", "[::1]"].includes(hostname) && port === "14103");
}
export function bindAdminStore(slug: string): void {
  if (!validStoreSlug(slug)) throw new Error("登录未返回有效的所属门店，请重新登录");
  sessionStorage.setItem(ADMIN_STORE_KEY, slug);
}
export function clearAdminStore(): void {
  try { sessionStorage.removeItem(ADMIN_STORE_KEY); } catch { /* Session logout still clears its credentials. */ }
}
function adminStoreSlug(): string {
  try {
    const selected = sessionStorage.getItem(ADMIN_STORE_KEY) || "";
    if (validStoreSlug(selected)) return selected;
  } catch { /* A neutral login does not require any persisted tenant. */ }
  return "";
}
export function hostStoreSlug(host: string): string | undefined {
  const hostname = host.toLowerCase().split(":")[0].replace(/\.$/, "");
  if (hostname in STORE_HOSTS) return STORE_HOSTS[hostname];
  // An unknown tenant domain must never fall back to the default business DB.
  if (hostname === "esports.club" || hostname.endsWith(".esports.club")) return "";
  return undefined;
}
export const validStoreSlug = (value: string) => /^[a-z][a-z0-9-]{0,47}$/.test(value);
const currencyNames = new Map<string, string>();

export function registerStoreCurrency(slug: string, name?: string): void {
  if (validStoreSlug(slug)) currencyNames.set(slug, name?.trim() || "店币");
}

export function coinUnit(): string {
  const slug = getStoreSlug();
  return currencyNames.get(slug) || (slug === DEFAULT_STORE_SLUG ? "浪花" : "店币");
}

/** Use only for literal interface copy; user names and API values stay intact. */
export function coinText(text: string): string {
  return text.replaceAll("浪花", coinUnit());
}

/** The store belongs to the URL, so independent tabs never change each other. */
export function getStoreSlug(): string {
  if (typeof location === "undefined") return DEFAULT_STORE_SLUG;
  if (isAdminEntry()) return adminStoreSlug();
  const bound = hostStoreSlug(location.hostname || location.host || "");
  if (bound !== undefined) return bound;
  const root = new URLSearchParams(location.search || "").get("store");
  const hash = new URLSearchParams((location.hash || "").split("?")[1] || "").get("store");
  return root ?? hash ?? DEFAULT_STORE_SLUG;
}

export function storeStorageKey(key: string, slug = getStoreSlug()): string {
  return `xa:store:${slug}:${key}`;
}

export function storeUrl(url: string, slug = getStoreSlug()): string {
  const target = new URL(url, typeof location === "undefined" ? "http://localhost" : location.href);
  if (target.hostname.toLowerCase() === "admin.esports.club" ||
      (["localhost", "127.0.0.1"].includes(target.hostname) && target.port === "14103")) {
    target.searchParams.delete("store");
    return target.href;
  }
  const bound = hostStoreSlug(target.hostname);
  if (bound !== undefined) {
    if (!bound || bound !== slug) throw new Error("门店网址与当前门店不一致");
    target.searchParams.delete("store");
  } else target.searchParams.set("store", slug);
  return target.href;
}

/** A document navigation disposes every old view, draft, socket and subscription. */
export function switchStore(slug: string): void {
  slug = slug.trim();
  if (!validStoreSlug(slug)) throw new Error("门店标识须以小写字母开头，仅含小写字母、数字和连字符，最多48位");
  const target = new URL(location.href);
  if (isAdminEntry()) throw new Error("请使用目标门店授权的后台账号重新登录");
  const bound = hostStoreSlug(target.hostname);
  if (bound !== undefined) {
    if (/^\/(boss|player)(\/|$)/.test(target.pathname)) throw new Error("请通过所属门店的专属入口登录");
    const hostname = Object.keys(STORE_HOSTS).find(host => STORE_HOSTS[host] === slug);
    if (!hostname) throw new Error("该门店尚未配置专属域名");
    target.hostname = hostname;
    target.searchParams.delete("store");
  } else target.searchParams.set("store", slug);
  // Never carry an old order, recipient, or credential challenge to another store.
  target.hash = "/login";
  location.assign(target.href);
}

export function canonicalizeStoreUrl(): void {
  if (typeof location === "undefined" || typeof history === "undefined") return;
  if (isAdminEntry() || hostStoreSlug(location.hostname || location.host || "") !== undefined) {
    const target = new URL(location.href);
    target.searchParams.delete("store");
    const [route, query = ""] = target.hash.split("?");
    const params = new URLSearchParams(query);
    params.delete("store");
    target.hash = route + (params.size ? `?${params}` : "");
    if (target.href !== location.href) history.replaceState(history.state, "", target.href);
    return;
  }
  if (!new URLSearchParams(location.search || "").has("store")) {
    history.replaceState(history.state, "", storeUrl(location.href));
  }
}

/** Move legacy pending writes only into the original store, preserving retries. */
export function adoptLegacyStoreStorage(): void {
  if (getStoreSlug() !== DEFAULT_STORE_SLUG) return;
  const prefixes = ["xa:fronted:fund:", "xa:fronted:admin:review:", "xa:fronted:admin:message-pending:", "xa:boss:web:intent:", "xa:boss:web:checkout:", "xa:boss:web:chat:", "xa:boss:web:reservation-pending:", "xa:provider:chat-pending:", "xa:provider:chat-draft:", "xa:provider:points-pending:"];
  for (const storage of [localStorage, sessionStorage]) {
    const pending: string[] = [];
    for (let index = 0; index < storage.length; index++) {
      const key = storage.key(index);
      if (key && prefixes.some((prefix) => key.startsWith(prefix))) pending.push(key);
    }
    for (const key of pending) {
      const value = storage.getItem(key);
      const existing = storage.getItem(storeStorageKey(key));
      if (value !== null && existing !== null && existing !== value)
        throw new Error("存在两份待核对交易，请先核对原门店的请求");
      if (value !== null && existing === null) storage.setItem(storeStorageKey(key), value);
      storage.removeItem(key);
    }
  }
}


/** SVG masters stay in source control; rendered brand slots use transparent PNGs. */
export function storeBrandLogo(store: { slug: string; logo_url?: string }, baseUrl: string): string {
  if (store.logo_url) return store.logo_url;
  const filename = !store.slug || store.slug === "platform"
    ? "platform.png"
    : store.slug === "xinan"
      ? "xinan.png"
      : "brand.svg";
  return `${baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`}${filename}`;
}
