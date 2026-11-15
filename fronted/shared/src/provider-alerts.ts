import { createApiClient } from "./client.ts";
import { getSession } from "./session.ts";
import { getStoreSlug } from "./store.ts";

export interface ProviderAlertOrder {
  id: number;
  status: string;
  reject_count?: number;
  dispatch_notification_enabled?: boolean;
}
export interface ProviderAlertAudio {
  isRunning(): boolean;
  unlock(): void | Promise<void>;
  play(): void;
  close?(): void | Promise<void>;
}

/** Current workbench alert policy; audio injection is not a hardware test. */
export function createProviderOrderAlerts({
  readPool,
  readAssignments,
  audio,
  isVisible,
  enabled: initialEnabled = true,
  persistEnabled,
  onEnabledChange,
}: {
  readPool?: () => Promise<ProviderAlertOrder[]>;
  readAssignments?: (
    after: number | null,
  ) => Promise<{ latest_id: number; pending: boolean }>;
  audio: ProviderAlertAudio;
  isVisible: () => boolean;
  enabled?: boolean;
  persistEnabled?: (enabled: boolean) => void;
  onEnabledChange?: (enabled: boolean) => void;
}) {
  const api = createApiClient("provider"),
    store = getStoreSlug(),
    revision = getSession("provider").revision;
  const fetchPool =
    readPool ||
    (() =>
      api.get<ProviderAlertOrder[]>("/orders/orders/", {
        status: "pending",
        role: "provider",
      }));
  const fetchAssignments =
    readAssignments ||
    ((after: number | null) =>
      api.get<{ latest_id: number; pending: boolean }>(
        "/messages/assignment-alerts/",
        { after: after ?? undefined },
      ));
  let enabled = initialEnabled,
    disposed = false,
    started = false,
    initialized = false,
    poolSequence = 0;
  let assignmentCursor: number | null = null,
    timer: ReturnType<typeof setInterval> | undefined;
  const seen = new Map<number, number>(),
    latestSeen = new Set<number>();
  const current = () =>
    !disposed &&
    store === getStoreSlug() &&
    revision === getSession("provider").revision;
  function play() {
    if (!current() || !enabled || !isVisible()) return;
    try {
      if (audio.isRunning()) audio.play();
    } catch {
      /* Sound never blocks accepting work. */
    }
  }
  async function preview() {
    if (!current() || !isVisible()) return;
    try {
      await audio.unlock();
      if (current() && isVisible() && audio.isRunning()) audio.play();
    } catch {
      /* A preview must not prevent the preference from changing. */
    }
  }
  async function pool() {
    if (!current() || !isVisible()) return;
    const sequence = ++poolSequence;
    try {
      const rows = await fetchPool();
      if (!current() || sequence !== poolSequence) return;
      let hasNew = false;
      for (const row of rows) {
        if (row.status !== "PENDING") continue;
        const previous = seen.get(row.id),
          version = row.reject_count || 0;
        if (
          (previous === undefined || version > previous) &&
          row.dispatch_notification_enabled !== false
        )
          hasNew = true;
        seen.set(row.id, version);
      }
      if (initialized && hasNew) play();
      initialized = true;
    } catch {
      /* Reconnect or the next poll reads again. */
    }
  }
  async function assignments() {
    if (!current()) return;
    const after = assignmentCursor;
    try {
      const value = await fetchAssignments(after);
      if (!current() || !Number.isSafeInteger(value.latest_id)) return;
      if (
        after !== null &&
        value.pending &&
        value.latest_id > (assignmentCursor || 0)
      )
        play();
      assignmentCursor = Math.max(assignmentCursor || 0, value.latest_id);
    } catch {
      /* The next reconnect reconciles the server cursor. */
    }
  }
  return {
    async start() {
      if (!current() || started) return;
      started = true;
      timer = setInterval(() => void pool(), 10000);
      await Promise.all([pool(), assignments()]);
    },
    async event(event: { type: string; data?: unknown }) {
      if (!current()) return;
      if (event.type === "order_status_update") await pool();
      if (["connected", "reconnected"].includes(event.type))
        await Promise.all([pool(), assignments()]);
      if (event.type === "site_message_update") {
        const id = Number(
          (event.data as { assignment_message_id?: number } | null)
            ?.assignment_message_id,
        );
        if (
          Number.isSafeInteger(id) &&
          id > 0 &&
          !latestSeen.has(id) &&
          (assignmentCursor === null || id > assignmentCursor)
        ) {
          latestSeen.add(id);
          assignmentCursor = id;
          play();
        }
      }
    },
    pool,
    assignments,
    async resume() {
      if (current() && isVisible()) await Promise.all([pool(), assignments()]);
    },
    async unlock() {
      if (!current()) return;
      try {
        await audio.unlock();
      } catch {
        /* Keep visual alerts if audio is unsupported. */
      }
    },
    toggle() {
      if (!current()) return enabled;
      enabled = !enabled;
      try {
        persistEnabled?.(enabled);
      } catch {
        /* The current-page mute still applies when storage is unavailable. */
      }
      onEnabledChange?.(enabled);
      void preview();
      return enabled;
    },
    enabled: () => enabled,
    dispose() {
      if (disposed) return;
      disposed = true;
      poolSequence++;
      if (timer) clearInterval(timer);
      seen.clear();
      latestSeen.clear();
      assignmentCursor = null;
      initialized = false;
      try {
        void Promise.resolve(audio.close?.()).catch(() => {});
      } catch {
        /* Already closed or unsupported. */
      }
    },
  };
}
