import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { createApiClient, fundRequest } from "../src/client.ts";
import { clearSession, persistSession } from "../src/session.ts";
import { registerStoreCurrency } from "../src/store.ts";
import {
  chatPendingKey,
  readChatPending,
  saveChatPending,
  clearChatPending,
  createRefreshCoordinator,
  bossOrderPresentation,
  bossMessageDestination,
  deliverChatMessage,
  giftBenefitPresentation,
  loadProviderReportPage,
  lookupChatMessage,
  mayMarkMessagesRead,
  mutateAndReload,
  providerMessageDestination,
  providerOrderActions,
  providerOrderPresentation,
  schedulePendingOrderRefresh,
  bossOrderStatusText,
  providerReportDestination,
  providerReportFocus,
  providerReportPresentation,
  reservationRebookTarget,
  uploadProviderReportImages,
  walletTransactionPresentation,
  orderBusinessDetails,
  createViewScopeGuard,
  evaluationFocus,
  loadProviderEvaluations,
  submitProviderEvaluationReply,
  evaluationReplyFailure,
  evaluationLatestReply,
  createPaginationController,
  loadProviderBenefits,
  loadProviderApplications,
  providerApplicationPresentation,
  providerAccountPresentation,
  updateProviderStatus,
} from "../src/business.ts";
import { createPrivateMediaRecovery } from "../src/media.ts";
import {
  couponPresentation,
  chatDraftKey,
  readChatDraft,
  saveChatDraft,
  clearChatDraft,
  removeProviderChatContext,
  withdrawalPresentation,
} from "../src/business.ts";

const values = new Map<string, string>();
// This synthetic business fixture explicitly uses 浪花; unknown stores stay generic.
registerStoreCurrency("business-a", "浪花");
Object.assign(globalThis, {
  localStorage: {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  },
  location: { search: "?store=business-a", hash: "" },
});
const originalFetch = globalThis.fetch;
const reply = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status });
const ok = (data: unknown) => reply({ code: 0, data });
function login() {
  persistSession("provider", {
    token: "provider-token",
    refresh: "refresh",
    user: { id: 1, role: "provider" },
  });
}
afterEach(() => {
  globalThis.fetch = originalFetch;
  clearSession("provider");
  values.clear();
});

test("order details only expose game accounts to the assigned active provider and keep reservation links", () => {
  const order = {
    id: 8,
    status: "PENDING",
    game_region: "一区",
    game_nickname: "本人账号",
    game_uid: "private-uid",
    fulfillment: "两局",
    gameplay_snapshot: { rules: "本单规则快照" },
    reservation_origin: {
      id: 9,
      start_time: "start",
      end_time: "end",
      can_open: false,
    },
  };
  const pending = orderBusinessDetails(order, "provider", 1);
  assert.equal(pending.gameRegion, "一区");
  assert.equal(pending.gameNickname, "");
  assert.equal(pending.gameUid, "");
  assert.equal(pending.reservation?.destination, "");
  assert.equal(pending.fulfillment, "两局");
  assert.equal(pending.gameplayRules, "本单规则快照");
  const active = {
    ...order,
    status: "IN_SERVICE",
    participants: [{ id: 1 }],
    reservation_origin: { ...order.reservation_origin, can_open: true },
  };
  assert.equal(
    orderBusinessDetails(active, "provider", 1).gameUid,
    "private-uid",
  );
  assert.equal(
    orderBusinessDetails(active, "provider", 2).showGameAccount,
    false,
  );
  assert.equal(
    orderBusinessDetails(
      { ...active, my_assignment_released: true },
      "provider",
      1,
    ).gameUid,
    "",
  );
  assert.equal(
    orderBusinessDetails(active, "provider", 1).reservation?.destination,
    "/reservations?id=9",
  );
  assert.equal(
    orderBusinessDetails(order, "boss").reservation?.destination,
    "/reservation-detail?id=9",
  );
  assert.equal(
    orderBusinessDetails(
      { ...order, cancel_reason: "真实原因", payment_status: "UNPAID" },
      "boss",
    ).cancelReason,
    "",
  );
  assert.equal(
    orderBusinessDetails(
      { ...order, cancel_reason: "真实原因", payment_status: "REFUNDED" },
      "boss",
    ).cancelReason,
    "真实原因",
  );
});

test("coupon rendering uses authoritative sold-out state and the existing threshold and rate copy", () => {
  const available = couponPresentation({
    discount_type: "DIRECT",
    amount: 1000,
    threshold: 10000,
    scope: "ALL",
    claimable: true,
    claim_status: "AVAILABLE",
  });
  assert.equal(available.benefitText, "100 浪花");
  assert.equal(available.thresholdText, "会员优惠后满 1,000 浪花");
  assert.equal(available.claimable, true);
  const sold = couponPresentation({
    claimable: false,
    claim_status: "SOLD_OUT",
    unavailable_reason: "已领完",
  });
  assert.equal(sold.claimStatus, "SOLD_OUT");
  assert.equal(sold.claimable, false);
  assert.equal(sold.claimLabel, "已领完");
  const rate = couponPresentation({
    discount_type: "RATE",
    discount_rate: 80,
    scope: "ALL",
    threshold: 0,
    claimable: true,
  });
  assert.equal(rate.benefitText, "8 折");
  assert.equal(rate.thresholdText, "无门槛");
  assert.equal(rate.rateNotice, "与 VIP 折扣取优，不叠加。");
  assert.equal(
    couponPresentation({ is_claimed: true, claimable: true }).claimable,
    false,
  );
});

test("withdrawal receipts distinguish registered, estimated and returned funds without changing raw amounts", () => {
  const row = {
    status: "APPROVED",
    amount: 2000,
    actual_amount: 1960,
    payee_method_display: "微信",
    payee_name: "验收",
    payout_reference: "ISOLATED-RECEIPT",
  };
  const approved = withdrawalPresentation(row);
  assert.equal(approved.payoutText, "已登记打款 ¥19.60");
  assert.equal(approved.payoutReference, "ISOLATED-RECEIPT");
  assert.equal(approved.originalAmount, 2000);
  assert.equal(approved.actualAmount, 1960);
  assert.equal(
    withdrawalPresentation({ ...row, status: "PENDING", processing: true })
      .statusLabel,
    "处理中",
  );
  assert.equal(
    withdrawalPresentation({ ...row, status: "PENDING" }).payoutText,
    "预计打款 ¥19.60",
  );
  const rejected = withdrawalPresentation({
    ...row,
    status: "REJECTED",
    payout_reference: "",
    audit_remark: "验收驳回，资金已释放",
  });
  assert.equal(rejected.payoutText, "已退回余额");
  assert.equal(rejected.auditRemark, "验收驳回，资金已释放");
  assert.equal(rejected.payoutReference, "");
});

test("removing chat order context preserves conflicting drafts and isolates store and account scopes", () => {
  login();
  const scope = "ORD1234567890ABCDEF1234";
  saveChatDraft("provider", scope, "本订单草稿");
  saveChatDraft("provider", "", "普通会话草稿");
  assert.equal(removeProviderChatContext(scope, "本订单草稿").conflict, true);
  assert.equal(readChatDraft("provider", scope), "本订单草稿");
  assert.equal(readChatDraft("provider", ""), "普通会话草稿");
  clearChatDraft("provider", "");
  const moved = removeProviderChatContext(scope, "本订单草稿");
  assert.equal(moved.saved, true);
  assert.equal(moved.conflict, false);
  assert.equal(readChatDraft("provider", scope), "");
  assert.equal(readChatDraft("provider", ""), "本订单草稿");
  const originalKey = chatDraftKey("provider", "");
  persistSession("provider", {
    token: "second",
    refresh: "second",
    user: { id: 2, role: "provider" },
  });
  assert.equal(readChatDraft("provider", ""), "");
  login();
  const previousStore = location.search;
  try {
    (globalThis.location as any).search = "?store=other-draft-store";
    assert.equal(readChatDraft("provider", ""), "");
  } finally {
    (globalThis.location as any).search = previousStore;
  }
  assert.equal(chatDraftKey("provider", ""), originalKey);
  assert.equal(readChatDraft("provider", ""), "本订单草稿");
});

test("a failed draft move reports unsaved and retains the original order draft", () => {
  login();
  const scope = "ORD1234567890ABCDEF1234";
  saveChatDraft("provider", scope, "不能丢失的正文");
  const write = localStorage.setItem;
  localStorage.setItem = () => {
    throw new Error("quota");
  };
  try {
    const moved = removeProviderChatContext(scope, "不能丢失的正文");
    assert.equal(moved.saved, false);
    assert.ok(moved.error instanceof Error);
    assert.equal(readChatDraft("provider", scope), "不能丢失的正文");
    assert.equal(readChatDraft("provider", ""), "");
  } finally {
    localStorage.setItem = write;
  }
  saveChatDraft("provider", scope, "更新的草稿");
  assert.equal(clearChatDraft("provider", scope, "旧消息正文").cleared, false);
  assert.equal(readChatDraft("provider", scope), "更新的草稿");
});

test("evaluation focus, CAS reply and authoritative reread retain the draft after an uncertain receipt", async () => {
  login();
  const draft = "  保留的回复草稿  ";
  const requests: { url: URL; body: any }[] = [];
  globalThis.fetch = (async (input, init) => {
    const url = new URL(String(input), "http://local");
    requests.push({
      url,
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    if (init?.method === "POST") throw new TypeError("committed response lost");
    return ok({
      list: [{ id: 7, reply_content: draft.trim(), reply_version: 5 }],
      total: 1,
    });
  }) as typeof fetch;
  assert.equal(evaluationFocus(new URLSearchParams("order=17")), "17");
  assert.equal(evaluationFocus(new URLSearchParams("order=bad")), "");
  await loadProviderEvaluations({ page: 3, orderId: "17" });
  assert.equal(requests[0].url.searchParams.get("page"), "3");
  assert.equal(requests[0].url.searchParams.get("order_id"), "17");
  await assert.rejects(
    submitProviderEvaluationReply({ id: 7, reply_version: 4 }, draft),
  );
  assert.deepEqual(requests[1].body, {
    reply_content: draft.trim(),
    reply_version: 4,
  });
  assert.equal(evaluationReplyFailure(new TypeError("lost")).blocked, true);
  assert.equal(
    evaluationReplyFailure({
      status: 409,
      errorCode: "EVALUATION_REPLY_CONFLICT",
    }).blocked,
    true,
  );
  assert.equal(
    evaluationReplyFailure({ status: 200, code: 404 }).blocked,
    true,
  );
  const latest = await loadProviderEvaluations({ page: 1, evaluationId: 7 });
  assert.equal(requests[2].url.searchParams.get("evaluation_id"), "7");
  assert.equal(evaluationLatestReply(latest.list[0], draft).blocked, false);
  assert.match(
    evaluationLatestReply(latest.list[0], draft).notice,
    /无需重复发布/,
  );
  assert.equal(evaluationLatestReply(null, draft).blocked, true);
  assert.equal(draft, "  保留的回复草稿  ");
  assert.equal(requests.filter((row) => row.body).length, 1);
});

test("a committed report mutation cannot reload or navigate after leaving and returning to the old focus", async () => {
  login();
  const guard = createViewScopeGuard("provider", "report:order=1");
  const current = guard.capture();
  let release!: () => void;
  globalThis.fetch = (async () => {
    await new Promise<void>((resolve) => {
      release = resolve;
    });
    return ok({ id: 11 });
  }) as typeof fetch;
  let reloads = 0;
  const pending = mutateAndReload({
    role: "provider",
    mutate: () =>
      createApiClient("provider").post("/wallet/reports/", { order_id: 1 }),
    reload: async () => {
      reloads++;
    },
    isCurrent: current,
  });
  guard.setScope("report:order=2");
  guard.setScope("report:order=1");
  release();
  const result = await pending;
  assert.equal(result.stale, true);
  assert.equal(reloads, 0);
  assert.equal(current(), false);
  const next = guard.capture();
  assert.equal(next(), true);
  guard.dispose();
  guard.activate();
  assert.equal(next(), false);
  const oldAccount = guard.capture();
  persistSession("provider", {
    token: "replacement",
    refresh: "new",
    user: { id: 2, role: "provider" },
  });
  assert.equal(oldAccount(), false);
});

test("cursor refresh retains expanded claims, updates duplicate rows and isolates list channels", async () => {
  const pages = createPaginationController<{ id: number; value?: string }>();
  pages.merge("claims", [{ id: 6 }, { id: 5 }, { id: 4 }]);
  pages.merge("ledger", [{ id: 1 }]);
  const reads: (number | null)[] = [];
  const refreshed = await pages.readExpanded(
    "claims",
    async (before) => {
      reads.push(before);
      if (!before)
        return {
          items: [{ id: 8 }, { id: 7 }, { id: 6, value: "old" }],
          next: 6,
        };
      if (before === 6)
        return { items: [{ id: 6, value: "fresh" }, { id: 5 }], next: 4 };
      return { items: [{ id: 4 }, { id: 3 }], next: 2 };
    },
    null,
  );
  assert.deepEqual(reads, [null, 6, 4]);
  assert.deepEqual(
    pages.rows("claims").map((row) => row.id),
    [6, 5, 4],
  );
  assert.deepEqual(
    pages.merge("claims", refreshed.items).map((row) => row.id),
    [8, 7, 6, 5, 4, 3],
  );
  assert.equal(
    pages.rows("claims").find((row) => row.id === 6)?.value,
    "fresh",
  );
  assert.deepEqual(pages.rows("ledger"), [{ id: 1 }]);
  pages.merge("claims", [{ id: 3 }, { id: 2 }], true);
  assert.equal(pages.rows("claims").filter((row) => row.id === 3).length, 1);
});

test("private image recovery coalesces errors, allows explicit retry and drops a disposed image response", async () => {
  login();
  const source = "/api/media/private/signed-ticket/?store=business-a";
  let calls = 0;
  globalThis.fetch = (async (_input, init) => {
    calls++;
    assert.deepEqual(JSON.parse(String(init?.body)), {
      ticket: "signed-ticket",
    });
    return calls === 1
      ? reply({ code: 503, msg: "unavailable" }, 503)
      : ok({ url: "/renewed-private-image" });
  }) as typeof fetch;
  const recovery = createPrivateMediaRecovery("provider", source);
  const [first, duplicate] = await Promise.all([
    recovery.recover(),
    recovery.recover(),
  ]);
  assert.equal(first.failed, true);
  assert.equal(duplicate.failed, true);
  assert.equal(calls, 1);
  recovery.retry();
  assert.equal((await recovery.recover()).url, "/renewed-private-image");
  assert.equal((await recovery.recover()).failed, true);
  assert.equal(calls, 2);
  let finish!: () => void;
  globalThis.fetch = (async () => {
    await new Promise<void>((resolve) => {
      finish = resolve;
    });
    return ok({ url: "/old-account-image" });
  }) as typeof fetch;
  const abandoned = createPrivateMediaRecovery("provider", source);
  const request = abandoned.recover();
  abandoned.dispose();
  finish();
  assert.equal((await request).stale, true);
});

test("provider numbered benefits and application pages issue independent real request parameters", async () => {
  login();
  const urls: URL[] = [];
  globalThis.fetch = (async (input) => {
    urls.push(new URL(String(input), "http://local"));
    return ok({ list: [], total: 0 });
  }) as typeof fetch;
  await loadProviderBenefits("gift", 3);
  await loadProviderBenefits("dispose", 2);
  await loadProviderApplications(4);
  assert.equal(urls[0].searchParams.get("gift_page"), "3");
  assert.equal(urls[0].searchParams.get("dispose_page"), "1");
  assert.equal(urls[1].searchParams.get("gift_page"), "1");
  assert.equal(urls[1].searchParams.get("dispose_page"), "2");
  assert.equal(urls[2].searchParams.get("page"), "4");
  const closed = providerApplicationPresentation({
    application: { status: "PENDING", ready: true },
    can_confirm: false,
    closed_reason: "报名已结束",
  });
  assert.equal(closed.canWithdraw, false);
  assert.equal(closed.canConfirm, false);
  assert.equal(closed.notice, "报名已结束");
});

test("provider status writes and deposit cards preserve raw coin amounts and cash versus earnings rules", async () => {
  login();
  globalThis.fetch = (async (_url, init) => {
    assert.deepEqual(JSON.parse(String(init?.body)), { status: "OFFLINE" });
    return ok({ status: "OFFLINE" });
  }) as typeof fetch;
  await updateProviderStatus("OFFLINE");
  const result = providerAccountPresentation(
    { status: "OFFLINE" },
    {
      deposit_mode: "EARNINGS",
      deposit_required: 1000,
      deposit_paid: 700,
      deposit_remaining: 300,
      balance: 299,
      hold_hours: 72,
      deposit_percent: 20,
    },
  );
  assert.equal(result.status, "OFFLINE");
  assert.equal(result.deposit?.modeLabel, "单抵");
  assert.equal(result.deposit?.required, 1000);
  assert.equal(result.deposit?.paid, 700);
  assert.equal(result.deposit?.balance, 299);
  assert.equal(result.deposit?.canPay, false);
  assert.match(result.deposit!.settlementNotice, /20%/);
  assert.equal(providerAccountPresentation(null, null).deposit, null);
});

test("cancelled and refunded order/report headers show current ledger net income instead of the historical estimate", () => {
  const fullRefund = {
    id: 8,
    status: "CANCELLED",
    payment_status: "REFUNDED",
    expected_income: 960,
    provider_income: 960,
    settlement_status: "UNSETTLED",
    refund_summary: { amount: 1200, net_income: 0, remaining_frozen_amount: 0 },
  };
  const order = providerOrderPresentation(fullRefund);
  assert.equal(order.expectedIncome, 960);
  assert.equal(order.displayIncome, 0);
  assert.equal(order.zeroIncomeNotice, "");
  assert.equal(
    providerOrderPresentation({ expected_income: 0 }).zeroIncomeNotice,
    "本单预计收益为0浪花，请核对后继续。",
  );
  assert.equal(
    providerOrderPresentation({ expected_income: null }).zeroIncomeNotice,
    "",
  );
  assert.equal(order.incomeLabel, "服务收益流水净额");
  assert.match(order.settlementLabel, /实际收益以钱包流水为准/);
  const report = providerReportPresentation({
    ...fullRefund,
    status: "APPROVED",
    order_status: "CANCELLED",
  });
  assert.equal(report.expectedIncome, 960);
  assert.equal(report.displayIncome, 0);
  assert.equal(report.incomeLabel, order.incomeLabel);
  assert.doesNotMatch(report.settlementNotice, /等待.*结算/);
  assert.equal(fullRefund.provider_income, 960);
  const partial = {
    ...fullRefund,
    status: "COMPLETED",
    payment_status: "PARTIALLY_REFUNDED",
    refund_summary: { net_income: 640, remaining_frozen_amount: 640 },
  };
  assert.equal(providerOrderPresentation(partial).displayIncome, 640);
  assert.equal(
    providerReportPresentation({ ...partial, order_status: "COMPLETED" })
      .displayIncome,
    640,
  );
  assert.equal(
    providerOrderPresentation({
      ...partial,
      refund_summary: { net_income: null },
    }).displayIncome,
    null,
  );
  assert.equal(
    providerOrderPresentation({
      status: "IN_SERVICE",
      payment_status: "PAID",
      expected_income: 960,
    }).displayIncome,
    960,
  );
});

test("pending refresh uses the earliest valid server deadline and respects the server expired flag", () => {
  login();
  const now = Date.now,
    timer = globalThis.setTimeout,
    clear = globalThis.clearTimeout;
  const delays: number[] = [];
  const disposers: (() => void)[] = [];
  const fixed = Date.parse("2030-01-01T00:00:00Z");
  const paid = {
    status: "PENDING",
    payment_status: "PAID",
    pending_deadline_expired: false,
  };
  Date.now = () => fixed;
  globalThis.setTimeout = ((_callback: unknown, delay: number) => {
    delays.push(delay);
    return 1 as unknown as ReturnType<typeof setTimeout>;
  }) as typeof setTimeout;
  globalThis.clearTimeout = (() => {}) as typeof clearTimeout;
  try {
    const ignored = [
      { ...paid, auto_cancel_at: "bad" },
      { ...paid, auto_cancel_at: null },
      {
        ...paid,
        auto_cancel_at: "2030-01-01T00:00:02Z",
        pending_deadline_expired: true,
      },
      {
        ...paid,
        auto_cancel_at: "2030-01-01T00:00:01Z",
        payment_display: { kind: "DIRECT_TEST" },
      },
      {
        ...paid,
        auto_cancel_at: "2030-01-01T00:00:01Z",
        payment_status: "UNPAID",
      },
      { ...paid, status: "COMPLETED", auto_cancel_at: "2030-01-01T00:00:01Z" },
    ];
    disposers.push(
      schedulePendingOrderRefresh("provider", ignored, () =>
        assert.fail("ineligible order read"),
      ),
    );
    assert.deepEqual(delays, []);
    disposers.push(
      schedulePendingOrderRefresh(
        "provider",
        [
          ...ignored,
          { ...paid, auto_cancel_at: "2030-01-01T00:00:30Z" },
          { ...paid, auto_cancel_at: "2030-01-01T08:00:05+08:00" },
        ],
        () => {},
      ),
    );
    disposers.push(
      schedulePendingOrderRefresh(
        "provider",
        [{ ...paid, auto_cancel_at: "2029-12-31T23:59:59Z" }],
        () => {},
      ),
    );
    disposers.push(
      schedulePendingOrderRefresh(
        "provider",
        [{ ...paid, auto_cancel_at: "2030-01-01T01:00:00Z" }],
        () => {},
      ),
    );
    assert.deepEqual(delays, [5500, 1000, 60000]);
    assert.equal(
      bossOrderStatusText({ ...paid, auto_cancel_at: "2029-12-31T23:59:59Z" }),
      "待接单",
    );
    assert.equal(
      bossOrderStatusText({ ...paid, pending_deadline_expired: true }),
      "已到接单期限，等待处理",
    );
    assert.equal(
      bossOrderStatusText({ ...paid, dispatch_route: "STAFF" }),
      "等待客服安排",
    );
  } finally {
    disposers.forEach((dispose) => dispose());
    Date.now = now;
    globalThis.setTimeout = timer;
    globalThis.clearTimeout = clear;
  }
});

test("pending deadline callback only invokes the supplied GET reload and never cancels or refunds", async () => {
  login();
  const timer = globalThis.setTimeout,
    clear = globalThis.clearTimeout;
  let fire!: () => void;
  const methods: string[] = [];
  globalThis.setTimeout = ((callback: () => void) => {
    fire = callback;
    return 1 as unknown as ReturnType<typeof setTimeout>;
  }) as typeof setTimeout;
  globalThis.clearTimeout = (() => {}) as typeof clearTimeout;
  globalThis.fetch = (async (_input, init) => {
    methods.push(init?.method || "GET");
    return ok({
      status: "PENDING",
      payment_status: "PAID",
      pending_deadline_expired: true,
    });
  }) as typeof fetch;
  const dispose = schedulePendingOrderRefresh(
    "provider",
    [
      {
        status: "PENDING",
        payment_status: "PAID",
        auto_cancel_at: new Date(Date.now() - 1000).toISOString(),
      },
    ],
    () => createApiClient("provider").get("/orders/orders/8/"),
  );
  try {
    fire();
    await Promise.resolve();
    await Promise.resolve();
    assert.deepEqual(methods, ["GET"]);
  } finally {
    dispose();
    globalThis.setTimeout = timer;
    globalThis.clearTimeout = clear;
  }
});

test("pending deadline callbacks are invalid after disposal, route change, store change or account replacement", async () => {
  login();
  const timer = globalThis.setTimeout,
    clear = globalThis.clearTimeout,
    previousStore = location.search;
  const callbacks: (() => void)[] = [];
  let reloads = 0,
    routeCurrent = true;
  const row = [
    {
      status: "PENDING",
      payment_status: "PAID",
      auto_cancel_at: new Date(Date.now() + 5000).toISOString(),
    },
  ];
  globalThis.setTimeout = ((callback: () => void) => {
    callbacks.push(callback);
    return 1 as unknown as ReturnType<typeof setTimeout>;
  }) as typeof setTimeout;
  globalThis.clearTimeout = (() => {}) as typeof clearTimeout;
  const disposers: (() => void)[] = [];
  try {
    const disposed = schedulePendingOrderRefresh("provider", row, () => {
      reloads++;
    });
    disposers.push(disposed);
    disposed();
    callbacks[0]();
    disposers.push(
      schedulePendingOrderRefresh(
        "provider",
        row,
        () => {
          reloads++;
        },
        () => routeCurrent,
      ),
    );
    routeCurrent = false;
    callbacks[1]();
    disposers.push(
      schedulePendingOrderRefresh("provider", row, () => {
        reloads++;
      }),
    );
    (globalThis.location as any).search = "?store=deadline-other";
    callbacks[2]();
    (globalThis.location as any).search = previousStore;
    disposers.push(
      schedulePendingOrderRefresh("provider", row, () => {
        reloads++;
      }),
    );
    persistSession("provider", {
      token: "replacement",
      user: { id: 2, role: "provider" },
    });
    callbacks[3]();
    // Disposing after a timer fires still prevents its queued microtask GET.
    const queued = schedulePendingOrderRefresh("provider", row, () => {
      reloads++;
    });
    disposers.push(queued);
    callbacks[4]();
    queued();
    await Promise.resolve();
    assert.equal(reloads, 0);
  } finally {
    disposers.forEach((dispose) => dispose());
    (globalThis.location as any).search = previousStore;
    globalThis.setTimeout = timer;
    globalThis.clearTimeout = clear;
  }
});

test("unknown fund writes retain their ID across a later 400 or 409, then recover a verified receipt", async () => {
  login();
  const ids: string[] = [];
  let attempt = 0;
  globalThis.fetch = (async (_url, options) => {
    ids.push(JSON.parse(String(options!.body)).client_request_id);
    attempt++;
    if (attempt === 1) throw new TypeError("response lost after commit");
    if (attempt === 2) return reply({ code: 400, msg: "rules changed" }, 400);
    if (attempt === 3)
      return reply({ code: 409, errorCode: "FUND_RESULT_UNAVAILABLE" }, 409);
    return reply(
      {
        code: 409,
        errorCode: "FUND_ALREADY_PROCESSED",
        fundResult: { code: 0, data: { id: 91 } },
      },
      409,
    );
  }) as typeof fetch;
  for (let index = 0; index < 3; index++)
    await assert.rejects(
      fundRequest("provider", "/wallet/withdraw/", { amount: 80 }),
    );
  assert.equal(
    (await fundRequest<any>("provider", "/wallet/withdraw/", { amount: 80 }))
      .id,
    91,
  );
  assert.equal(new Set(ids).size, 1);
  assert.equal(
    [...values.keys()].some((key) => key.includes(":fund:")),
    false,
  );
});

test("an initial definite fund rejection may release its unused ID", async () => {
  login();
  const ids: string[] = [];
  globalThis.fetch = (async (_url, options) => {
    ids.push(JSON.parse(String(options!.body)).client_request_id);
    return reply({ code: 400, msg: "invalid amount" }, 400);
  }) as typeof fetch;
  await assert.rejects(
    fundRequest("provider", "/wallet/withdraw/", { amount: 80 }),
  );
  await assert.rejects(
    fundRequest("provider", "/wallet/withdraw/", { amount: 80 }),
  );
  assert.notEqual(ids[0], ids[1]);
});

test("an unsuccessful fund receipt cannot be mistaken for a recovered write", async () => {
  login();
  globalThis.fetch = (async () =>
    reply(
      {
        code: 409,
        errorCode: "FUND_ALREADY_PROCESSED",
        fundResult: { code: 400, data: { id: 99 } },
      },
      409,
    )) as typeof fetch;
  await assert.rejects(
    fundRequest("provider", "/wallet/withdraw/", { amount: 80 }),
  );
  assert.equal(
    [...values.keys()].some((key) => key.includes(":fund:")),
    true,
  );
});

test("provider notice routes retain the real order, application, report and refund destinations", () => {
  assert.equal(
    providerMessageDestination({
      related_order_id: 17,
      action_url: "/pages/orders/index?application_id=9",
    }),
    "/applications/17?application=9",
  );
  assert.equal(
    providerMessageDestination({
      related_order_id: 17,
      action_url: "/pages/orders/index?tab=pool",
    }),
    "/orders",
  );
  assert.equal(
    providerMessageDestination({
      related_order_id: 17,
      action_url: "/pages/report/index",
    }),
    "/reports?order=17",
  );
  assert.equal(
    providerMessageDestination({
      related_order_id: 17,
      title: "收到新的服务评价",
    }),
    "/evaluations?order=17",
  );
  assert.equal(
    providerMessageDestination({
      action_url: "/pages/wallet/index?withdraw_id=6",
    }),
    "/wallet?tab=withdraw&withdraw_id=6",
  );
  assert.equal(
    bossMessageDestination({
      action_url: "/pages/reservation-detail/index?id=3",
    }),
    "/reservation-detail?id=3",
  );
  assert.equal(
    bossMessageDestination({ action_url: "https://untrusted.invalid/" }),
    "",
  );
});

test("released team members cannot complete the whole order but retain an explicitly allowed report", () => {
  const row = {
    id: 7,
    status: "IN_SERVICE",
    my_assignment_released: true,
    allowed_actions: ["COMPLETE"],
    report_entry: { available: true },
  };
  assert.deepEqual(providerOrderActions(row), []);
  assert.equal(providerReportDestination(row), "/reports?order=7");
  assert.deepEqual(
    providerOrderActions({ status: "PENDING", allowed_actions: [] }),
    [],
  );
  assert.deepEqual(
    providerOrderActions({
      status: "GRABBED",
      allowed_actions: ["START", "REJECT"],
    }),
    ["START"],
  );
});

test("transferred-in provider sees entry before completing the service", () => {
  assert.deepEqual(providerOrderActions({status:"IN_SERVICE", can_operate:true,
    allowed_actions:["START_PARTICIPATION"]}), ["START_PARTICIPATION"]);
  assert.deepEqual(providerOrderActions({status:"IN_SERVICE", can_operate:true,
    allowed_actions:["COMPLETE"]}), ["COMPLETE"]);
  assert.deepEqual(providerOrderActions({status:"IN_SERVICE", my_assignment_released:true,
    allowed_actions:["START_PARTICIPATION"]}), []);
});

test("report presentation keeps resolved aftersales, actual net earnings and settlement distinct", () => {
  const result = providerReportPresentation({
    status: "APPROVED",
    aftersale_status: "RESOLVED",
    settlement_status: "WAITING",
    expected_income: 960,
    refund_summary: {
      amount: 300,
      net_income: 720,
      remaining_frozen_amount: 720,
    },
    can_edit: false,
    can_submit: false,
    entry_image_url: "entry",
    completion_image_url: "end",
    result_image_urls: ["result"],
  });
  assert.equal(result.aftersaleOpen, false);
  assert.equal(result.netIncome, 720);
  assert.equal(result.expectedIncome, 960);
  assert.equal(result.remainingFrozen, 720);
  assert.equal(result.canSubmit, false);
  assert.equal(result.canWithdraw, false);
  assert.equal(
    providerReportPresentation({
      status: "PENDING",
      can_withdraw: true,
      entry_image_url: "entry",
      completion_image_url: "end",
      result_image_urls: ["result"],
    }).canWithdraw,
    true,
  );
  assert.match(result.settlementNotice, /等待/);
});

test("negotiated report displays retained income and the participant's own settlement", () => {
  const report = { status: "APPROVED", payment_status: "PARTIALLY_REFUNDED",
    negotiated_settlement: true, expected_income: 3000,
    refund_summary: { net_income: 0 }, settlement_status: "SETTLED" };
  const result = providerReportPresentation(report);
  assert.equal(result.incomeLabel, "协商保留收益");
  assert.equal(result.displayIncome, 3000);
  assert.match(result.settlementNotice, /已结算/);
  assert.match(providerReportPresentation({ ...report, settlement_status: "WAITING" }).settlementNotice, /等待/);
  assert.match(providerReportPresentation({ ...report, settlement_status: "REVIEW" }).settlementNotice, /待核对/);
});

test("wallet gift and referral sources and refunded net earnings remain distinguishable", () => {
  assert.equal(
    walletTransactionPresentation({ tx_type: "PASS_PURCHASE", is_reversal: true, amount: 200 }).label,
    "通行证退款",
  );
  assert.equal(
    walletTransactionPresentation({
      tx_type: "GIFT",
      gift_source: "ORDER_GIFT",
      amount: 100,
    }).label,
    "礼物收益",
  );
  assert.equal(
    walletTransactionPresentation({
      tx_type: "GIFT",
      gift_source: "STAFF_RECHARGE",
      amount: 100,
    }).label,
    "客服赠送",
  );
  assert.equal(
    walletTransactionPresentation({
      tx_type: "INCOME",
      is_referral_income: true,
      amount: -10,
    }).label,
    "推荐收益退回",
  );
  assert.equal(
    giftBenefitPresentation({
      settlement_status: "REFUNDED",
      received_income: 50,
      provider_income: 50,
    }).receivedIncome,
    0,
  );
  assert.equal(
    giftBenefitPresentation({
      settlement_status: "PARTIALLY_REFUNDED",
      received_income: 20,
      provider_income: 50,
    }).receivedIncome,
    20,
  );
});

test("mutation response loss still re-reads authoritative state without claiming success", async () => {
  login();
  let state = "PENDING",
    observed = "";
  const api = createApiClient("provider");
  globalThis.fetch = (async (_url, options) => {
    if (options!.method === "POST") {
      state = "CONFIRMED";
      throw new TypeError("response lost");
    }
    return ok({ status: state });
  }) as typeof fetch;
  const outcome = await mutateAndReload({
    role: "provider",
    mutate: () => api.post("/orders/reservations/4/confirm/"),
    reload: async () => {
      observed = (await api.get<any>("/orders/reservations/4/")).status;
    },
  });
  assert.ok(outcome.error);
  assert.equal(outcome.stale, false);
  assert.equal(observed, "CONFIRMED");
  assert.equal(outcome.value, undefined);
});

test("stale mutation completion skips re-reading and cannot return old-account data", async () => {
  login();
  let reloads = 0;
  const outcome = await mutateAndReload({
    role: "provider",
    mutate: async () => {
      clearSession("provider");
      return { id: 5 };
    },
    reload: async () => {
      reloads++;
    },
  });
  assert.equal(outcome.stale, true);
  assert.equal(outcome.value, undefined);
  assert.equal(reloads, 0);
});

test("report-id focus searches the owned paginated API instead of treating it as an order ID", async () => {
  login();
  const pages: string[] = [];
  globalThis.fetch = (async (url) => {
    pages.push(String(url));
    return ok(
      pages.length === 1
        ? { list: [{ id: 1 }], total: 21 }
        : { list: [{ id: 81, order_id: 51 }], total: 21 },
    );
  }) as typeof fetch;
  const focus = providerReportFocus(new URLSearchParams("report_id=81"));
  assert.deepEqual(focus, { reportId: "81", orderId: "" });
  const result = await loadProviderReportPage(1, focus);
  assert.equal(result.list[0].order_id, 51);
  assert.equal(pages.length, 2);
  assert.match(pages[1], /page=2/);
});

test("a failed batch image stops later uploads and still reloads the actual report", async () => {
  login();
  let writes = 0,
    reloads = 0;
  globalThis.fetch = (async () => {
    writes++;
    if (writes === 2) throw new TypeError("upload result lost");
    return ok({ id: writes });
  }) as typeof fetch;
  const outcome = await uploadProviderReportImages({
    reportId: 4,
    kind: "RESULT",
    files: [
      new File(["a"], "a.png"),
      new File(["b"], "b.png"),
      new File(["c"], "c.png"),
    ],
    reload: async () => {
      reloads++;
    },
  });
  assert.equal(outcome.uploaded, 1);
  assert.equal(writes, 2);
  assert.equal(reloads, 1);
  assert.ok(outcome.error);
});

test("chat retries preserve original IDs and a lookup must match that exact intent", async () => {
  login();
  const intent = { id: "message-one", content: "original draft", kind: "TEXT" };
  const sent: string[] = [];
  globalThis.fetch = (async (url, options) => {
    if (String(url).includes("message-status"))
      return ok({
        status: "FOUND",
        message: { id: 1, client_message_id: "message-one" },
      });
    sent.push(JSON.parse(String(options!.body)).client_message_id);
    if (sent.length === 1) throw new TypeError("response lost");
    return ok({ id: 1, client_message_id: "message-one" });
  }) as typeof fetch;
  await assert.rejects(deliverChatMessage("provider", intent));
  assert.equal(
    (await lookupChatMessage("provider", intent)).message?.client_message_id,
    intent.id,
  );
  assert.equal((await deliverChatMessage("provider", intent)).id, 1);
  assert.deepEqual(sent, [intent.id, intent.id]);
});

test("hidden, unfocused and history-reading views cannot mark new messages read", () => {
  assert.equal(mayMarkMessagesRead({ hidden: true, focused: true }), false);
  assert.equal(mayMarkMessagesRead({ hidden: false, focused: false }), false);
  assert.equal(
    mayMarkMessagesRead({ hidden: false, focused: true, browsing: true }),
    false,
  );
  assert.equal(mayMarkMessagesRead({ hidden: false, focused: true }), true);
  assert.equal(
    reservationRebookTarget({
      provider: { account_id: 12 },
      service: { id: 7 },
    }),
    "/reservation-create?provider_id=12&service_id=7",
  );
});

test("boss refunds, zero-payment cancellation and legacy-team progress use real snapshots", () => {
  const refund = bossOrderPresentation({
    status: "CANCELLED",
    amount: 0,
    refund_summary: {
      amount: 150,
      remaining_amount: 0,
      records: [
        {
          id: 1,
          amount: 150,
          coupon_results: [{ id: 8, outcome: "RETURNED" }],
        },
      ],
    },
  });
  assert.equal(refund.refundAmount, 150);
  assert.equal(refund.refundRemaining, 0);
  assert.equal(refund.refundRecords.length, 1);
  assert.equal(refund.zeroPaymentCancelled, true);
  assert.match(refund.couponOutcomes[0].label, /已返还/);
  const legacy = {
    escort_mode: "DOUBLE",
    requires_boss_selection: false,
    status: "IN_SERVICE",
    payment_status: "PAID",
  };
  assert.equal(bossOrderPresentation(legacy).teamProgressNeedsReview, true);
  assert.equal(
    bossOrderPresentation({
      ...legacy,
      team_progress: { submitted_count: 1, required_count: 2 },
    }).teamProgressNeedsReview,
    false,
  );
  assert.equal(
    bossOrderPresentation({ ...legacy, payment_status: "UNPAID" })
      .teamProgressNeedsReview,
    false,
  );
});

async function until(condition: () => boolean) {
  for (let i = 0; i < 100; i++) {
    if (condition()) return;
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
  throw new Error("refresh did not run");
}
test("visible live events, resume and reconnect execute refresh and preserve a trailing event", async () => {
  login();
  let visible = false,
    calls = 0;
  let release!: () => void;
  const blocked = new Promise<void>((resolve) => {
    release = resolve;
  });
  const coordinator = createRefreshCoordinator({
    reload: async () => {
      calls++;
      if (calls === 1) await blocked;
    },
    isVisible: () => visible,
    types: () => ["site_message_update"],
    delay: 1,
  });
  try {
    assert.equal(coordinator.event("site_message_update"), false);
    visible = true;
    assert.equal(coordinator.event("unrelated"), false);
    coordinator.event("site_message_update");
    coordinator.event("site_message_update");
    await until(() => calls === 1);
    coordinator.event("site_message_update");
    release();
    await until(() => calls === 2);
    coordinator.resume();
    await until(() => calls === 3);
    coordinator.event("reconnected");
    await until(() => calls === 4);
  } finally {
    release();
    coordinator.dispose();
  }
});

test("queued refreshes cannot execute after another account replaces their session", async () => {
  login();
  let calls = 0;
  const coordinator = createRefreshCoordinator({
    reload: async () => {
      calls++;
    },
    isVisible: () => true,
    delay: 1,
  });
  coordinator.event("connected");
  clearSession("provider");
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(calls, 0);
  assert.equal(coordinator.resume(), false);
  coordinator.dispose();
});

test("chat pending metadata restores the same account and store without persisting image objects", () => {
  login();
  const pending = {
    id: "pending-one",
    content: "original text",
    kind: "IMAGE",
    draft: "draft",
    scope: "order-one",
    image: new File(["image"], "image.png"),
    imageData: "data:image/png;base64,private-image",
  };
  assert.equal(saveChatPending("provider", pending).saved, true);
  const stored = values.get(chatPendingKey("provider"))!;
  assert.equal(stored.includes("base64"), false);
  assert.equal(stored.includes("image.png"), false);
  assert.equal(readChatPending("provider")?.content, "original text");
  const original = location.search;
  try {
    Object.assign(location, { search: "?store=business-other" });
    persistSession("provider", {
      token: "other-store",
      user: { id: 1, role: "provider" },
    });
    assert.equal(readChatPending("provider"), null);
    Object.assign(location, { search: original });
    persistSession("provider", {
      token: "other-account",
      user: { id: 2, role: "provider" },
    });
    assert.equal(readChatPending("provider"), null);
    login();
    assert.equal(readChatPending("provider")?.scope, "order-one");
    assert.equal(readChatPending("provider")?.id, "pending-one");
  } finally {
    Object.assign(location, { search: original });
  }
});

test("chat storage failure reports unsaved and cannot destroy a newer pending intent", () => {
  login();
  saveChatPending("provider", {
    id: "new-intent",
    content: "new text",
    kind: "TEXT",
  });
  const previous = localStorage.setItem;
  try {
    localStorage.setItem = () => {
      throw new Error("quota");
    };
    const result = saveChatPending("provider", {
      id: "another",
      content: "not saved",
      kind: "TEXT",
    });
    assert.equal(result.saved, false);
    assert.ok(result.error instanceof Error);
  } finally {
    localStorage.setItem = previous;
  }
  assert.equal(readChatPending("provider")?.id, "new-intent");
  assert.deepEqual(clearChatPending("provider", "old-intent"), {
    saved: true,
    cleared: false,
  });
  assert.equal(readChatPending("provider")?.id, "new-intent");
});
