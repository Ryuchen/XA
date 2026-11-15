import { createApiClient } from "./client.ts";
import { getSession, sessionChangedError, type Role } from "./session.ts";
import { getStoreSlug } from "./store.ts";

/** Private media URLs may include their store query; renewal uses only the ticket. */
export function privateMediaTicket(source: string): string | null {
  const value = source.match(
    /\/api\/media\/private\/([^/?#]+)\/?(?:[?#].*)?$/,
  )?.[1];
  if (!value) return null;
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

export async function renewPrivateMedia(
  role: Role,
  source: string,
  isCurrent: () => boolean = () => true,
): Promise<string> {
  const ticket = privateMediaTicket(source);
  if (!ticket) throw new Error("图片地址无效");
  const result = await createApiClient(role).post<{ url: string }>(
    "/media/renew/",
    { ticket },
  );
  if (!isCurrent()) throw sessionChangedError();
  if (!result.url) throw new Error("图片暂不可读取");
  return result.url;
}

/** Each image gets one automatic renewal; explicit retry starts a new attempt. */
export function createPrivateMediaRecovery(role: Role, initialSource: string) {
  let source = initialSource,
    url = source,
    attempted = false,
    failed = false,
    generation = 0,
    active = true;
  let pending: Promise<{
    url: string;
    failed: boolean;
    stale?: boolean;
  }> | null = null;
  const store = getStoreSlug(),
    revision = getSession(role).revision;
  const current = (version: number) =>
    active &&
    version === generation &&
    store === getStoreSlug() &&
    revision === getSession(role).revision;
  const state = () => ({ url, failed });
  return {
    state,
    retry() {
      generation++;
      attempted = false;
      failed = false;
      url = source;
      pending = null;
      return state();
    },
    dispose() {
      active = false;
      generation++;
    },
    async recover(): Promise<{
      url: string;
      failed: boolean;
      stale?: boolean;
    }> {
      if (pending) return pending;
      if (attempted) {
        failed = true;
        return state();
      }
      attempted = true;
      const version = generation;
      pending = (async () => {
        try {
          const result = await renewPrivateMedia(role, source, () =>
            current(version),
          );
          if (!current(version)) return { ...state(), stale: true };
          url = result;
          return state();
        } catch {
          if (!current(version)) return { ...state(), stale: true };
          failed = true;
          return state();
        } finally {
          if (version === generation) pending = null;
        }
      })();
      return pending;
    },
  };
}
