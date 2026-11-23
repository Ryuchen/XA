import { getStoreSlug, isAdminEntry, storeStorageKey, validStoreSlug } from "./store.ts";
import { randomUuid } from "./ids.ts";
import { readFundMarkers, updateFundMarker } from "./fund-marker-storage.ts";
import {
  clearSession,
  getSession,
  sessionChangedError,
  updateAccessToken,
  type Role,
} from "./session.ts";

export interface Envelope<T> {
  code: number;
  msg?: string;
  data: T;
  errorCode?: string;
  fundResult?: Envelope<T>;
}
export interface RequestOptions {
  method?: string;
  data?: unknown;
  query?: Record<string, unknown>;
  public?: boolean;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}
export class ApiError extends Error {
  status: number;
  code: number | string;
  errorCode?: string;
  fundResult?: Envelope<any>;
  body: any;
  data: any;
  retry_after?: number;
  constructor(message: string, status = 0, body: any = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code ?? status;
    this.errorCode = body.errorCode;
    this.fundResult = body.fundResult;
    this.body = body;
    this.data = body.data;
    this.retry_after = body.retry_after;
  }
}
export const apiErrorMessage = (error: unknown) =>
  error instanceof Error ? error.message : "操作未完成，请重试";
const refreshes = new Map<
  string,
  { revision: number; operation: Promise<string> }
>();
async function decode(response: Response) {
  if (response.status === 204) return { code: 0, data: null };
  try {
    return await response.json();
  } catch {
    throw new ApiError("服务返回异常，请稍后重试", response.status);
  }
}
export async function refreshSession(role: Role): Promise<string> {
  const current = getSession(role);
  const store = getStoreSlug();
  const neutralAdmin = role === "admin" && isAdminEntry();
  const refreshKey = `${store}:${role}`;
  if (neutralAdmin && !validStoreSlug(store)) throw new ApiError("请重新登录后台", 401);
  if (!current.refresh) throw new ApiError("登录已过期，请重新登录", 401);
  const existing = refreshes.get(refreshKey);
  if (existing?.revision === current.revision) return existing.operation;
  const operation = (async () => {
    const response = await fetch(
      role === "admin" ? "/api/admin/auth/refresh" : "/api/users/refresh/",
      {
        method: "POST",
        headers: neutralAdmin ? { "Content-Type": "application/json" } : { "Content-Type": "application/json", "X-Store-Slug": store },
        body: JSON.stringify({ refresh: current.refresh }),
      },
    );
    if (
      getStoreSlug() !== store ||
      getSession(role).revision !== current.revision
    )
      throw sessionChangedError();
    const body = await decode(response);
    if (
      getStoreSlug() !== store ||
      getSession(role).revision !== current.revision
    )
      throw sessionChangedError();
    if (!response.ok || body.code !== 0 || !body.data?.token) {
      if (response.status === 401) clearSession(role);
      throw new ApiError(
        body.msg || "登录状态暂时无法刷新，请稍后重试",
        response.status,
        body,
      );
    }
    if (neutralAdmin && body.data.store_slug !== store) {
      clearSession(role);
      throw new ApiError("后台登录门店已变化，请重新登录", 401);
    }
    updateAccessToken(role, body.data.token, current.revision);
    return body.data.token as string;
  })();
  refreshes.set(refreshKey, { revision: current.revision, operation });
  operation
    .finally(() => {
      if (refreshes.get(refreshKey)?.operation === operation)
        refreshes.delete(refreshKey);
    })
    .catch(() => {});
  return operation;
}
export function createApiClient(role: Role) {
  async function requestEnvelope<T>(
    path: string,
    options: RequestOptions = {},
  ): Promise<Envelope<T>> {
    const current = getSession(role);
    const revision = current.revision;
    const store = getStoreSlug();
    if (role === "admin" && isAdminEntry() && !options.public && (!current.token || !validStoreSlug(store)))
      throw new ApiError("请先使用所属门店的后台账号登录", 401);
    const query = new URLSearchParams();
    Object.entries(options.query || {}).forEach(([name, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        if (Array.isArray(value))
          value.forEach((item) => query.append(name, String(item)));
        else query.set(name, String(value));
      }
    });
    const target =
      (path.startsWith("/api/")
        ? path
        : "/api" + (path.startsWith("/") ? path : "/" + path)) +
      (query.size ? (path.includes("?") ? "&" : "?") + query : "");
    const multipart =
      typeof FormData !== "undefined" && options.data instanceof FormData;
    const send = (token: string) => {
      const headers: Record<string, string> = {
        ...options.headers,
      };
      if (store && !(role === "admin" && isAdminEntry() && options.public)) headers["X-Store-Slug"] = store;
      if (token && !options.public) headers.Authorization = `Bearer ${token}`;
      if (options.data !== undefined && !multipart)
        headers["Content-Type"] = "application/json";
      return fetch(target, {
        method: options.method || "GET",
        headers,
        signal: options.signal,
        body:
          options.data === undefined
            ? undefined
            : multipart
              ? (options.data as FormData)
              : JSON.stringify(options.data),
      });
    };
    const assertSession = () => {
      if (
        getStoreSlug() !== store ||
        (!options.public && getSession(role).revision !== revision)
      )
        throw sessionChangedError();
    };
    let response: Response;
    try {
      response = await send(current.token);
      assertSession();
      if (response.status === 401 && current.token && !options.public) {
        const token =
          getSession(role).token !== current.token
            ? getSession(role).token
            : await refreshSession(role);
        assertSession();
        response = await send(token);
        assertSession();
        if (response.status === 401) clearSession(role);
      }
    } catch (error) {
      assertSession();
      if (
        error instanceof ApiError ||
        (error instanceof Error &&
          ("code" in error || error.name === "AbortError"))
      )
        throw error;
      throw new ApiError(
        options.method && options.method !== "GET"
          ? "网络中断，操作结果尚未确认，请保留原请求并核对结果"
          : "网络连接失败，请检查网络后重试",
      );
    }
    const body = await decode(response);
    assertSession();
    if (!response.ok || (typeof body?.code === "number" && body.code !== 0)) {
      const message =
        response.status === 429
          ? `请求较多，请在 ${body.retry_after || 1} 秒后重试`
          : body?.msg || body?.detail || "操作未完成，请重试";
      throw new ApiError(message, response.status, body);
    }
    return body && typeof body === "object" && "code" in body
      ? (body as Envelope<T>)
      : { code: 0, data: body as T };
  }
  async function request<T>(
    path: string,
    options: RequestOptions = {},
  ): Promise<T> {
    return (await requestEnvelope<T>(path, options)).data;
  }
  return {
    request,
    requestEnvelope,
    get: <T = any>(
      path: string,
      query?: Record<string, unknown>,
      options: RequestOptions = {},
    ) => request<T>(path, { ...options, query }),
    post: <T = any>(
      path: string,
      data?: unknown,
      options: RequestOptions = {},
    ) => request<T>(path, { ...options, method: "POST", data }),
    put: <T = any>(
      path: string,
      data?: unknown,
      options: RequestOptions = {},
    ) => request<T>(path, { ...options, method: "PUT", data }),
    patch: <T = any>(
      path: string,
      data?: unknown,
      options: RequestOptions = {},
    ) => request<T>(path, { ...options, method: "PATCH", data }),
    delete: <T = any>(
      path: string,
      data?: unknown,
      options: RequestOptions = {},
    ) => request<T>(path, { ...options, method: "DELETE", data }),
    upload: <T = any>(
      path: string,
      data: FormData,
      options: RequestOptions = {},
    ) => request<T>(path, { ...options, method: "POST", data }),
  };
}
function stable(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(stable).join(",") + "]";
  if (value && typeof value === "object")
    return (
      "{" +
      Object.keys(value)
        .sort()
        .map(
          (key) =>
            JSON.stringify(key) +
            ":" +
            stable((value as Record<string, unknown>)[key]),
        )
        .join(",") +
      "}"
    );
  return JSON.stringify(value) ?? "null";
}
const funds = new Map<string, Promise<any>>();

export type PendingFundRequest = {
  key: string;
  path: string;
  payload: Record<string, unknown>;
  requestId: string;
};

const fundStoragePrefix = (role: Role, userId: string | number) =>
  storeStorageKey(`xa:fronted:fund:${role}:${userId}:`);

function pendingFundRequest(key: string, requestId: string | null, prefix: string, matchesPath: (path: string) => boolean): PendingFundRequest | null {
  if (!key.startsWith(prefix) || !requestId || requestId.trim() !== requestId || requestId.length > 64) return null;
  const boundary = key.indexOf(":{", prefix.length);
  if (boundary === -1) return null;
  const path = key.slice(prefix.length, boundary);
  if (!matchesPath(path)) return null;
  try {
    const encoded = key.slice(boundary + 1);
    const payload = JSON.parse(encoded) as Record<string, unknown>;
    if (Object.hasOwn(payload, "client_request_id") || stable(payload) !== encoded) return null;
    return { key, path, payload, requestId };
  } catch {
    return null;
  }
}

export function listPendingFundRequests(role: Role, matchesPath: (path: string) => boolean): PendingFundRequest[] {
  const { user } = getSession(role);
  if (!user) return [];
  const prefix = fundStoragePrefix(role, user.id);
  const pending: PendingFundRequest[] = [];
  for (let index = 0; index < localStorage.length; index++) {
    const key = localStorage.key(index);
    if (!key?.startsWith(prefix)) continue;
    const item = pendingFundRequest(key, localStorage.getItem(key), prefix, matchesPath);
    if (item) pending.push(item);
  }
  return pending;
}

export async function listCoordinatedPendingFundRequests(role: Role, matchesPath: (path: string) => boolean): Promise<PendingFundRequest[]> {
  const session = getSession(role), store = getStoreSlug();
  if (!session.user) return [];
  const prefix = fundStoragePrefix(role, session.user.id);
  const markers = await readFundMarkers(prefix).catch(() => new Map<string, string>());
  if (getStoreSlug() !== store || getSession(role).revision !== session.revision) return [];
  const pending = new Map<string, PendingFundRequest>();
  try {
    for (const item of listPendingFundRequests(role, matchesPath)) pending.set(item.key, item);
  } catch {
    // IndexedDB can still hold the exact original request if the mirror is lost.
  }
  for (const [key, id] of markers) {
    const item = pendingFundRequest(key, id, prefix, matchesPath);
    if (item) pending.set(key, item);
  }
  return [...pending.values()];
}

type FundRequestOptions<T> = RequestOptions & {
  keepOriginalOnFailure?: boolean;
  coordinateAcrossTabs?: boolean;
  acceptsResult?: (data: T) => boolean;
};

export function retryPendingFundRequest<T>(role: Role, pending: PendingFundRequest, accepts: (data: T) => boolean,
  options: { coordinateAcrossTabs?: boolean } = {}): Promise<T> {
  return fundRequest(role, pending.path, pending.payload, { ...options, acceptsResult: accepts }, pending);
}

export function fundRequest<T = any>(
  role: Role,
  path: string,
  payload: Record<string, unknown>,
  options: FundRequestOptions<T> = {},
  recovery?: PendingFundRequest,
): Promise<T> {
  const { keepOriginalOnFailure, coordinateAcrossTabs, acceptsResult, ...requestOptions } = options;
  const session = getSession(role);
  if (!session.user) return Promise.reject(new ApiError("请先登录", 401));
  const store = getStoreSlug();
  const key = `${fundStoragePrefix(role, session.user.id)}${path}:${stable(payload)}`;
  const changed = () => new ApiError("原操作记录已变化，请重新核对钱包。", 409, { errorCode: "FUND_RECOVERY_CHANGED" });
  const active = () => getStoreSlug() === store && getSession(role).revision === session.revision;
  const requireActive = () => { if (!active()) throw sessionChangedError(); };
  const storageFailure = (cause: unknown) => {
    requireActive();
    if (cause instanceof ApiError) throw cause;
    const message = "无法保存交易请求，请检查浏览器存储后重试";
    if (coordinateAcrossTabs) throw new ApiError(message, 400, { errorCode: "FUND_STORAGE_UNAVAILABLE" });
    throw new Error(message);
  };
  const inFlight = funds.get(key);
  if (inFlight) return recovery ? Promise.reject(changed()) : inFlight as Promise<T>;

  function reserve(canonical: string | null) {
    requireActive();
    const mirror = localStorage.getItem(key);
    if (canonical && mirror && canonical !== mirror) throw changed();
    const previous = canonical || mirror;
    if (recovery && (key !== recovery.key || previous !== recovery.requestId)) throw changed();
    const explicit = recovery?.requestId || String(payload.client_request_id || "");
    if (coordinateAcrossTabs && explicit && previous && previous !== explicit) throw changed();
    const id = explicit || previous || `fund-${randomUuid()}`;
    if (!recovery || coordinateAcrossTabs) localStorage.setItem(key, id);
    return { id, value: { id, hadPending: !!previous } };
  }

  function send({ id, hadPending }: { id: string; hadPending: boolean }) {
    requireActive();
    const removeMirror = () => { if (localStorage.getItem(key) === id) localStorage.removeItem(key); };
    async function discard() {
      try {
        if (coordinateAcrossTabs) {
          await updateFundMarker(key, current => {
            if (!active() || (current && current !== id)) return { id: current, value: undefined };
            removeMirror();
            return { id: null, value: undefined };
          });
        } else {
          removeMirror();
        }
      } catch {
        /* Reusing an old key is safer than losing a pending write. */
      }
    }
    async function complete(data: T) {
      if (acceptsResult && !acceptsResult(data)) throw new ApiError("原操作返回的结果暂不可核对，请保留原编号。");
      await discard();
      requireActive();
      return data;
    }
    return createApiClient(role).request<T>(path, {
      ...requestOptions,
      method: requestOptions.method || "POST",
      data: { ...payload, client_request_id: id },
    }).then(complete).catch(async (error: ApiError) => {
      requireActive();
      if (error.errorCode === "FUND_ALREADY_PROCESSED" && error.fundResult?.code === 0) {
        return complete(error.fundResult.data as T);
      }
      if (!keepOriginalOnFailure && !hadPending && typeof error.code === "number" && error.code >= 400
          && error.code < 500 && error.code !== 409 && error.code !== 429) await discard();
      throw error;
    });
  }

  let request: Promise<T>;
  try {
    if (coordinateAcrossTabs) {
      request = updateFundMarker(key, reserve).catch(storageFailure).then(send);
    } else {
      const { value } = reserve(null);
      request = send(value);
    }
  } catch (cause) {
    return Promise.reject(cause).catch(storageFailure);
  }
  const operation = request.finally(() => funds.delete(key));
  funds.set(key, operation);
  return operation;
}
