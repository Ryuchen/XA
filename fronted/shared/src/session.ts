import { useSyncExternalStore } from "react";
import { DEFAULT_STORE_SLUG, bindAdminStore, clearAdminStore, getStoreSlug, isAdminEntry, storeStorageKey, validStoreSlug } from "./store.ts";

export type Role = "admin" | "boss" | "provider";
export interface SessionUser {
  id: string | number;
  username?: string;
  nickname?: string;
  role?: string;
  permissions?: string[];
  is_superuser?: boolean;
  [key: string]: any;
}
export interface Session {
  token: string;
  refresh: string;
  user: SessionUser | null;
  /** Identifies one login across tabs; never used as an authentication credential. */
  sessionId: string;
  revision: number;
}
export interface LoginPayload {
  store_slug?: string;
  token?: string;
  refresh?: string;
  refreshToken?: string;
  user?: SessionUser;
  userInfo?: SessionUser;
  profile?: SessionUser;
}
const roles: Role[] = ["admin", "boss", "provider"];
const cache = new Map<string, Session>();
let sequence = 0;
const listeners = new Map<Role, Set<() => void>>();
const key = (role: Role) => storeStorageKey(`xa:fronted:${role}:session:v1`);
function newSessionId(): string {
  return globalThis.crypto?.randomUUID?.() ||
    `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}
function sameIdentity(left: SessionUser | null, right: SessionUser | null): boolean {
  return !!left && !!right && left.id === right.id && sameProfile(left.role, right.role);
}
function sameProfile(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
  if (Array.isArray(left) !== Array.isArray(right)) return false;
  const leftKeys = Object.keys(left), rightKeys = Object.keys(right);
  return leftKeys.length === rightKeys.length && leftKeys.every((name) =>
    Object.prototype.hasOwnProperty.call(right, name) &&
    sameProfile((left as Record<string, unknown>)[name], (right as Record<string, unknown>)[name]),
  );
}
function read(role: Role, previous?: Session): Session {
  try {
    const raw = localStorage.getItem(key(role));
    // Preserve the original store session once; other stores never read legacy keys.
    const legacyKey = `xa:fronted:${role}:session:v1`;
    const legacy = !raw && getStoreSlug() === DEFAULT_STORE_SLUG && !(role === "admin" && isAdminEntry()) ? localStorage.getItem(legacyKey) : null;
    const value = JSON.parse(raw || legacy || "null");
    if (value?.token && value?.user?.id) {
      const refresh = value.refresh || value.refreshToken || "";
      // Old records have no login ID. Keep continuity only when identity and the
      // existing refresh credential match; otherwise invalidate conservatively.
      const sameLegacyLogin = previous?.sessionId && sameIdentity(previous.user, value.user) &&
        (refresh ? refresh === previous.refresh : !previous.refresh && value.token === previous.token);
      const sessionId = typeof value.sessionId === "string" && value.sessionId
        ? value.sessionId : sameLegacyLogin ? previous.sessionId : newSessionId();
      const next = { token: value.token, refresh, user: value.user, sessionId, revision: ++sequence };
      if (legacy || !value.sessionId) {
        try {
          localStorage.setItem(key(role), JSON.stringify(next));
          if (legacy) localStorage.removeItem(legacyKey);
        } catch {
          /* Keep the readable session even if its storage migration is blocked. */
        }
      }
      return next;
    }
  } catch {
    /* A logged-out shell still works when browser storage is unavailable. */
  }
  return { token: "", refresh: "", user: null, sessionId: "", revision: ++sequence };
}
export function getSession(role: Role): Session {
  const scope = key(role);
  if (!cache.has(scope)) cache.set(scope, read(role));
  return cache.get(scope)!;
}
export function subscribeSession(role: Role, listener: () => void) {
  if (!listeners.has(role)) listeners.set(role, new Set());
  listeners.get(role)!.add(listener);
  return () => {
    listeners.get(role)?.delete(listener);
  };
}
function emit(role: Role, next: Session) {
  cache.set(key(role), next);
  listeners.get(role)?.forEach((listener) => listener());
}
export function persistSession(role: Role, payload: LoginPayload) {
  const user = payload.profile || payload.userInfo || payload.user;
  if (!payload.token || !user?.id)
    throw new Error("登录验证未完成，请重新登录");
  if (
    (role === "boss" && user.role !== "customer") ||
    (role === "provider" && user.role !== "provider")
  ) {
    throw new Error("该账号不属于当前端，请使用对应端登录");
  }
  const neutralAdmin = role === "admin" && isAdminEntry();
  if (neutralAdmin && !validStoreSlug(payload.store_slug || ""))
    throw new Error("登录未返回有效的所属门店，请重新登录");
  const targetKey = neutralAdmin
    ? storeStorageKey("xa:fronted:admin:session:v1", payload.store_slug!) : key(role);
  const next = {
    token: payload.token,
    refresh: payload.refresh || payload.refreshToken || "",
    user,
    sessionId: newSessionId(),
    revision: ++sequence,
  };
  try {
    const previousValue = localStorage.getItem(targetKey);
    localStorage.setItem(targetKey, JSON.stringify(next));
    if (neutralAdmin) {
      try { bindAdminStore(payload.store_slug!); }
      catch (cause) {
        if (previousValue === null) localStorage.removeItem(targetKey);
        else localStorage.setItem(targetKey, previousValue);
        throw cause;
      }
    }
  } catch {
    throw new Error("无法保存登录状态，请检查浏览器存储后重试");
  }
  emit(role, next);
}
export function clearSession(role: Role) {
  const revision = ++sequence;
  const previousKey = key(role);
  try {
    localStorage.removeItem(previousKey);
  } catch {
    /* Clear the in-memory session even if storage fails. */
  }
  const next = { token: "", refresh: "", user: null, sessionId: "", revision };
  cache.set(previousKey, next);
  if (role === "admin" && isAdminEntry()) clearAdminStore();
  emit(role, next);
}
export function updateProfile(role: Role, patch: Partial<SessionUser>) {
  const previous = getSession(role);
  if (!previous.user) return;
  const user = {
    ...previous.user,
    ...patch,
    id: previous.user.id,
    role: previous.user.role,
  };
  if (sameProfile(previous.user, user)) return;
  const next = { ...previous, user };
  try {
    localStorage.setItem(key(role), JSON.stringify(next));
  } catch {
    /* Keep the current tab's verified profile. */
  }
  emit(role, next);
}
export function updateAccessToken(role: Role, token: string, revision: number) {
  const current = getSession(role);
  if (current.revision !== revision) throw sessionChangedError();
  if (current.token === token) return;
  const next = { ...current, token };
  try {
    localStorage.setItem(key(role), JSON.stringify(next));
  } catch {
    throw new Error("无法保存登录凭证，请检查浏览器存储");
  }
  emit(role, next);
}
export function sessionChangedError() {
  return Object.assign(new Error("账号或登录状态已变化，请刷新后重新操作"), {
    code: "SESSION_CHANGED",
  });
}
export function useSession(role: Role): Session {
  return useSyncExternalStore(
    (callback) => subscribeSession(role, callback),
    () => getSession(role),
    () => getSession(role),
  );
}
if (typeof window !== "undefined")
  window.addEventListener("storage", (event) => {
    for (const role of roles)
      if (event.key === key(role) || event.key === null) {
        const previousKey = key(role);
        const previous = getSession(role);
        const next = read(role, previous);
        const sameLogin = previous.sessionId && previous.sessionId === next.sessionId &&
          sameIdentity(previous.user, next.user);
        const bothLoggedOut = !previous.token && !next.token && !previous.user && !next.user;
        if (sameLogin || bothLoggedOut) next.revision = previous.revision;
        if (previous.revision !== next.revision || previous.token !== next.token ||
          previous.refresh !== next.refresh || previous.sessionId !== next.sessionId ||
          !sameProfile(previous.user, next.user)) {
          if (role === "admin" && isAdminEntry() && !next.token) {
            cache.set(previousKey, next);
            clearAdminStore();
          }
          emit(role, next);
        }
      }
  });
