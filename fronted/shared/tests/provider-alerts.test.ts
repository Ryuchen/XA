import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import {
  createProviderOrderAlerts,
  type ProviderAlertOrder,
} from "../src/provider-alerts.ts";
import { clearSession, persistSession } from "../src/session.ts";

const values = new Map<string, string>();
Object.assign(globalThis, {
  localStorage: {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  },
  location: { search: "?store=alerts-a", hash: "" },
});
const originalFetch = globalThis.fetch;
const active: ReturnType<typeof createProviderOrderAlerts>[] = [];
const fixture = (token = "provider-a") =>
  persistSession("provider", { token, user: { id: 7, role: "provider" } });
function fakeAudio() {
  return {
    plays: 0,
    unlocks: 0,
    closed: 0,
    running: true,
    isRunning() {
      return this.running;
    },
    unlock() {
      this.unlocks++;
      this.running = true;
    },
    play() {
      this.plays++;
    },
    close() {
      this.closed++;
      this.running = false;
    },
  };
}
function make(
  options: Partial<Parameters<typeof createProviderOrderAlerts>[0]> = {},
) {
  const controller = createProviderOrderAlerts({
    readPool: async () => [],
    readAssignments: async () => ({ latest_id: 0, pending: false }),
    audio: fakeAudio(),
    isVisible: () => true,
    ...options,
  });
  active.push(controller);
  return controller;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((ready) => {
    resolve = ready;
  });
  return { promise, resolve };
}
afterEach(() => {
  active.splice(0).forEach((controller) => controller.dispose());
  globalThis.fetch = originalFetch;
  for (const store of ["alerts-a", "alerts-b"]) {
    (globalThis.location as any).search = "?store=" + store;
    clearSession("provider");
  }
  (globalThis.location as any).search = "?store=alerts-a";
  values.clear();
});

test("production alert reads and pool revisions only record new eligible fake-audio invocations", async () => {
  fixture();
  const audio = fakeAudio(),
    urls: URL[] = [];
  let rows: ProviderAlertOrder[] = [{ id: 1, status: "PENDING" }];
  globalThis.fetch = (async (input) => {
    const url = new URL(String(input), "http://local");
    urls.push(url);
    const data = url.pathname.includes("assignment-alerts")
      ? { latest_id: 10, pending: true }
      : rows;
    return new Response(JSON.stringify({ code: 0, data }));
  }) as typeof fetch;
  const controller = createProviderOrderAlerts({
    audio,
    isVisible: () => true,
  });
  active.push(controller);
  await controller.start();
  assert.equal(audio.plays, 0);
  assert.equal(
    urls
      .find((url) => url.pathname.includes("/orders/"))
      ?.searchParams.get("status"),
    "pending",
  );
  assert.equal(
    urls
      .find((url) => url.pathname.includes("assignment-alerts"))
      ?.searchParams.has("after"),
    false,
  );
  rows = [...rows, { id: 2, status: "PENDING" }];
  await controller.event({ type: "order_status_update" });
  assert.equal(audio.plays, 1);
  await controller.event({ type: "order_status_update" });
  assert.equal(audio.plays, 1);
  rows = [
    { id: 1, status: "PENDING", reject_count: 1 },
    rows[1],
    { id: 3, status: "PENDING", dispatch_notification_enabled: false },
    { id: 4, status: "GRABBED" },
  ];
  await controller.pool();
  assert.equal(audio.plays, 2);
  rows = [{ id: 3, status: "PENDING", dispatch_notification_enabled: true }];
  await controller.pool();
  assert.equal(
    audio.plays,
    2,
    "enabling an already-seen revision is not a new order",
  );
});

test("mute, hidden and user unlock gates preserve current-page policy without claiming hardware sound", async () => {
  fixture();
  const audio = fakeAudio();
  audio.running = false;
  let visible = true,
    reads = 0,
    rows: ProviderAlertOrder[] = [],
    latest = 0;
  const preferences: boolean[] = [];
  const controller = make({
    audio,
    isVisible: () => visible,
    readPool: async () => {
      reads++;
      return rows;
    },
    readAssignments: async () => ({ latest_id: latest, pending: true }),
    persistEnabled: (value) => {
      preferences.push(value);
      throw new Error("storage unavailable");
    },
  });
  await controller.start();
  rows = [{ id: 1, status: "PENDING" }];
  await controller.pool();
  assert.equal(audio.plays, 0);
  await controller.unlock();
  assert.equal(audio.unlocks, 1);
  assert.equal(controller.toggle(), false);
  rows.push({ id: 2, status: "PENDING" });
  await controller.pool();
  latest = 1;
  await controller.event({
    type: "site_message_update",
    data: { assignment_message_id: latest },
  });
  assert.equal(audio.plays, 1, "the off toggle previews once; muted events stay silent");
  assert.equal(controller.toggle(), true);
  await controller.pool();
  assert.equal(audio.plays, 2, "the on toggle previews once without replaying consumed alerts");
  visible = false;
  const before = reads;
  rows.push({ id: 3, status: "PENDING" });
  await controller.pool();
  assert.equal(reads, before);
  latest = 2;
  await controller.event({
    type: "site_message_update",
    data: { assignment_message_id: latest },
  });
  assert.equal(audio.plays, 2);
  visible = true;
  await controller.resume();
  assert.equal(
    audio.plays,
    3,
    "visible pool reread notices the unseen order once",
  );
  await controller.event({
    type: "site_message_update",
    data: { assignment_message_id: latest },
  });
  assert.equal(audio.plays, 3);
  assert.deepEqual(preferences, [false, true]);
});

test("both sound toggles preview exactly once, independent of the resulting mute state", async () => {
  fixture();
  const audio = fakeAudio(), preferences: boolean[] = [];
  audio.running = false;
  const controller = make({ audio, persistEnabled: value => preferences.push(value) });
  assert.equal(controller.toggle(), false);
  await Promise.resolve();
  assert.equal(audio.plays, 1);
  assert.equal(controller.enabled(), false);
  await controller.unlock();
  assert.equal(audio.plays, 1, "ordinary gesture unlock is not an extra preview");
  assert.equal(controller.toggle(), true);
  await Promise.resolve();
  assert.equal(audio.plays, 2);
  assert.deepEqual(preferences, [false, true]);
});

test("an unfinished toggle preview cannot play in a replacement account", async () => {
  fixture();
  const audio = fakeAudio(), gate = deferred<void>();
  audio.running = false;
  audio.unlock = async () => { await gate.promise; audio.running = true; };
  const controller = make({ audio });
  assert.equal(controller.toggle(), false);
  fixture("replacement-provider");
  gate.resolve();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(audio.plays, 0);
  assert.equal(controller.enabled(), false);
});

test("assignment cursor and live event IDs reconcile once and never replay hidden or duplicate notices", async () => {
  fixture();
  const audio = fakeAudio(),
    afters: (number | null)[] = [];
  let latest = 100,
    pending = true,
    visible = true;
  const controller = make({
    audio,
    isVisible: () => visible,
    readAssignments: async (after) => {
      afters.push(after);
      return { latest_id: latest, pending };
    },
  });
  await controller.start();
  assert.equal(audio.plays, 0);
  await controller.event({
    type: "site_message_update",
    data: { assignment_message_id: 101 },
  });
  await controller.event({
    type: "site_message_update",
    data: { assignment_message_id: "101" },
  });
  await controller.event({
    type: "site_message_update",
    data: { assignment_message_id: 99 },
  });
  assert.equal(audio.plays, 1);
  latest = 105;
  await controller.assignments();
  assert.equal(audio.plays, 2);
  await controller.event({
    type: "site_message_update",
    data: { assignment_message_id: 105 },
  });
  assert.equal(audio.plays, 2);
  latest = 106;
  pending = false;
  await controller.assignments();
  assert.equal(audio.plays, 2);
  visible = false;
  latest = 107;
  pending = true;
  await controller.event({
    type: "site_message_update",
    data: { assignment_message_id: 107 },
  });
  visible = true;
  await controller.resume();
  assert.equal(audio.plays, 2);
  assert.deepEqual(afters, [null, 101, 105, 107]);
});

test("same numeric account in a replacement session gets fresh cursors and old asynchronous pool results cannot play", async () => {
  fixture();
  const oldAudio = fakeAudio(),
    newAudio = fakeAudio(),
    late = deferred<ProviderAlertOrder[]>();
  let first = true;
  const old = make({
    audio: oldAudio,
    readPool: async () => (first ? ((first = false), []) : late.promise),
  });
  await old.start();
  await old.event({
    type: "site_message_update",
    data: { assignment_message_id: 900 },
  });
  assert.equal(oldAudio.plays, 1);
  const request = old.pool();
  fixture("same-account-new-session");
  old.dispose();
  const current = make({ audio: newAudio });
  await current.start();
  await current.event({
    type: "site_message_update",
    data: { assignment_message_id: 1 },
  });
  assert.equal(newAudio.plays, 1);
  late.resolve([{ id: 2, status: "PENDING" }]);
  await request;
  await old.event({
    type: "site_message_update",
    data: { assignment_message_id: 901 },
  });
  assert.equal(oldAudio.plays, 1);
  assert.equal(oldAudio.closed, 1);
});

test("same numeric account and message IDs in another store use fresh alert state and reject old-store completions", async () => {
  fixture();
  const aAudio = fakeAudio(),
    bAudio = fakeAudio(),
    late = deferred<{ latest_id: number; pending: boolean }>();
  let first = true;
  const a = make({
    audio: aAudio,
    readAssignments: async () =>
      first
        ? ((first = false), { latest_id: 900, pending: false })
        : late.promise,
  });
  await a.start();
  const request = a.assignments();
  (globalThis.location as any).search = "?store=alerts-b";
  fixture("store-b-same-account");
  a.dispose();
  const b = make({ audio: bAudio });
  await b.start();
  await b.event({
    type: "site_message_update",
    data: { assignment_message_id: 1 },
  });
  late.resolve({ latest_id: 901, pending: true });
  await request;
  assert.equal(aAudio.plays, 0);
  assert.equal(bAudio.plays, 1);
  await a.unlock();
  a.toggle();
  assert.equal(aAudio.unlocks, 0);
});

test("latest pool read wins and disposal prevents pending assignment state or audio from being adopted", async () => {
  fixture();
  const audio = fakeAudio(),
    older = deferred<ProviderAlertOrder[]>(),
    newer = deferred<ProviderAlertOrder[]>();
  let call = 0;
  const controller = make({
    audio,
    readPool: async () =>
      ++call === 1 ? [] : call === 2 ? older.promise : newer.promise,
  });
  await controller.start();
  const first = controller.pool(),
    second = controller.pool();
  newer.resolve([{ id: 1, status: "PENDING" }]);
  await second;
  older.resolve([{ id: 2, status: "PENDING" }]);
  await first;
  assert.equal(audio.plays, 1);
  const pending = deferred<{ latest_id: number; pending: boolean }>();
  const abandonedAudio = fakeAudio();
  const abandoned = make({
    audio: abandonedAudio,
    readAssignments: () => pending.promise,
  });
  const start = abandoned.start();
  abandoned.dispose();
  pending.resolve({ latest_id: 700, pending: true });
  await start;
  await abandoned.event({
    type: "site_message_update",
    data: { assignment_message_id: 701 },
  });
  assert.equal(abandonedAudio.plays, 0);
  assert.equal(abandonedAudio.closed, 1);
});
