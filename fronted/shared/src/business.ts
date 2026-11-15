import { coinText, coinUnit } from "./store.ts"
import { createApiClient } from "./client.ts";
import { randomUuid } from "./ids.ts";
import { getSession, sessionChangedError, type Role } from "./session.ts";
import { getStoreSlug } from "./store.ts";
import { formatXaCoin, formatRmb } from "@xa/money";

export type BusinessRecord = Record<string, any>;
export function couponPresentation(coupon: BusinessRecord) {
  return {
    claimable:
      !!coupon.claimable &&
      !coupon.is_claimed &&
      coupon.claim_status !== "USED",
    claimStatus: coupon.claim_status || "",
    claimLabel: coupon.is_claimed
      ? "已领取"
      : coupon.claim_status === "USED"
        ? "已使用"
        : coupon.claimable
          ? "领取优惠券"
          : coupon.unavailable_reason || "暂不可领取",
    thresholdText: coupon.threshold
      ? `会员优惠后满 ${formatXaCoin(Number(coupon.threshold))} ${coinUnit()}`
      : "无门槛",
    benefitText:
      coupon.discount_type === "QUALIFICATION"
        ? "套餐资格券"
        : coupon.discount_type === "RATE"
          ? coupon.discount_rate === 0
            ? "赠送服务券"
            : coupon.discount_rate / 10 + " 折"
          : formatXaCoin(Number(coupon.amount || 0)) + coinText(" 浪花"),
    rateNotice:
      coupon.discount_type === "RATE" && coupon.scope === "ALL"
        ? "与 VIP 折扣取优，不叠加。"
        : "",
  };
}
export function withdrawalPresentation(row: BusinessRecord) {
  return {
    title: `${row.payee_method_display} · ${row.payee_name}`,
    statusLabel:
      row.status === "PENDING"
        ? row.processing
          ? "处理中"
          : "待审核"
        : undefined,
    originalAmount: row.amount,
    actualAmount: row.actual_amount,
    payoutText:
      ["REJECTED", "CANCELLED"].includes(row.status)
        ? "已退回余额"
        : `${["APPROVED", "PAID"].includes(row.status) ? "已登记打款" : "预计打款"} ¥${formatRmb(row.actual_amount)}`,
    auditRemark: row.audit_remark || "",
    payoutReference: row.payout_reference || "",
  };
}
const positiveId = (value: unknown) =>
  /^[1-9]\d*$/.test(String(value ?? "")) ? String(value) : "";
const route = (path: string, entries: Record<string, unknown> = {}) => {
  const query = new URLSearchParams(
    Object.entries(entries)
      .filter(([, value]) => value !== "" && value != null)
      .map(([key, value]) => [key, String(value)]),
  );
  return path + (query.size ? `?${query}` : "");
};

export function providerMessageDestination(message: BusinessRecord): string {
  const action = String(message.action_url || "");
  const match = /^\/(?:pages\/)?([a-z-]+)(?:\/index)?(?:\?([^#]*))?$/.exec(
    action,
  );
  const query = new URLSearchParams(match?.[2] || "");
  const order = positiveId(
    message.related_order_id || query.get("order_id") || query.get("order"),
  );
  const page = match?.[1] || "";
  if (
    (page === "orders" && query.get("tab") === "pool") ||
    message.title === "队友已撤回组队报名"
  )
    return "/orders";
  if (
    (order &&
      ["收到新的服务评价", "客服更新了评价回复"].includes(message.title)) ||
    (page === "evaluations" && order)
  )
    return `/evaluations?order=${order}`;
  if (["report", "reports"].includes(page)) return route("/reports", { order });
  if (page === "points")
    return route("/points", {
      tab: query.get("tab") === "claims" ? "claims" : "",
    });
  if (page === "reservations")
    return route("/reservations", { id: positiveId(query.get("id")) });
  if (page === "reward-claim")
    return route("/points", { claim_id: positiveId(query.get("id")) });
  if (page === "wallet" && positiveId(query.get("withdraw_id")))
    return route("/wallet", {
      tab: "withdraw",
      withdraw_id: query.get("withdraw_id"),
    });
  if (page === "orders" && order && positiveId(query.get("application_id")))
    return route(`/applications/${order}`, {
      application: query.get("application_id"),
    });
  if (page === "orders" && order && query.get("tab") === "pending")
    return `/applications/${order}`;
  if (order) return `/orders?order=${order}`;
  if (page === "wallet") return "/wallet";
  if (["提现审核通过", "提现审核未通过"].includes(message.title))
    return "/wallet?tab=withdraw";
  return "";
}

export function bossMessageDestination(message: BusinessRecord): string {
  const order = positiveId(message.related_order_id);
  if (order) return `/order-detail?id=${order}`;
  const match = /^\/pages\/([a-zA-Z0-9/-]+)\/index(?:\?([^#]*))?$/.exec(
    String(message.action_url || ""),
  );
  if (!match) return "";
  const query = new URLSearchParams(match[2] || "");
  const id = positiveId(query.get("id") || query.get("orderId"));
  const aliases: Record<string, string> = {
    orderList: id ? "order-detail" : "orders",
    "customer/checkin": "checkin",
    "customer/wallet": "wallet",
    "coupon/mine": "coupons",
    coupon: "coupons",
    serviceCard: "support",
    announcement: "announcements",
    announcementDetail: "announcement-detail",
    productDetail: "service",
    category: "catalog",
    favorite: "favorites",
    messageDetail: "message-detail",
    settings: "profile",
  };
  const page = aliases[match[1]] || match[1];
  if (
    ![
      "home",
      "catalog",
      "orders",
      "mine",
      "messages",
      "wallet",
      "checkin",
      "coupons",
      "support",
      "favorites",
      "announcements",
      "announcement-detail",
      "service",
      "order-detail",
      "reservation-detail",
      "reward-claim",
      "points",
      "chat",
      "companions",
      "profile",
    ].includes(page)
  )
    return "";
  return route(`/${page}`, {
    id,
    tab:
      match[1] === "coupon/mine"
        ? "mine"
        : page === "points" && query.get("tab") === "claims"
          ? "claims"
          : "",
  });
}

export function bossOrderPresentation(order: BusinessRecord) {
  const isUnconfirmed =
    order.payment_display?.kind === "DIRECT_TEST" ||
    order.payment_status === "UNPAID";
  const refundRecords: BusinessRecord[] = (
    Array.isArray(order.refund_summary?.records)
      ? order.refund_summary.records
      : []
  ).map((record: BusinessRecord) => ({
    ...record,
    coupon_results: (Array.isArray(record.coupon_results)
      ? record.coupon_results
      : []
    ).map((coupon: BusinessRecord) => ({
      ...coupon,
      label:
        coupon.outcome === "RETURNED"
          ? "已返还，当前状态请查看我的券"
          : "已核销，不返还",
    })),
  }));
  return {
    isUnconfirmed,
    refundAmount: order.refund_summary?.amount ?? 0,
    refundRemaining: order.refund_summary?.remaining_amount ?? null,
    refundRecords,
    couponOutcomes: refundRecords.flatMap(
      (record: BusinessRecord) => record.coupon_results,
    ),
    zeroPayment: order.amount === 0,
    zeroPaymentCancelled: order.status === "CANCELLED" && order.amount === 0,
    teamProgressNeedsReview:
      !isUnconfirmed &&
      !order.is_gift_snapshot &&
      !order.source_order &&
      !order.service?.is_gift &&
      order.escort_mode === "DOUBLE" &&
      order.requires_boss_selection === false &&
      ["GRABBED", "IN_SERVICE"].includes(order.status) &&
      !order.team_progress,
  };
}

export function bossOrderStatusText(
  order: BusinessRecord,
  fallback = String(order.status || ""),
): string {
  if (bossOrderPresentation(order).isUnconfirmed) return "支付状态待核对";
  if (order.pending_deadline_expired && order.status === "PENDING")
    return "已到接单期限，等待处理";
  if (
    order.dispatch_route === "STAFF" &&
    order.status === "PENDING" &&
    order.payment_status === "PAID"
  )
    return "等待客服安排";
  if (order.status === "PENDING") return "待接单";
  return fallback;
}

/** Schedule a read near the earliest server deadline; never mutate an order. */
export function schedulePendingOrderRefresh(
  role: Role,
  records: BusinessRecord[],
  reload: () => unknown | Promise<unknown>,
  isCurrent: () => boolean = () => true,
): () => void {
  const deadlines = records
    .filter(
      (order) =>
        order.status === "PENDING" &&
        order.payment_status === "PAID" &&
        !order.pending_deadline_expired &&
        order.payment_display?.kind !== "DIRECT_TEST",
    )
    .map((order) =>
      typeof order.auto_cancel_at === "string"
        ? Date.parse(order.auto_cancel_at)
        : NaN,
    )
    .filter(Number.isFinite);
  if (!deadlines.length) return () => {};
  const store = getStoreSlug(),
    revision = getSession(role).revision;
  let disposed = false;
  const current = () =>
    !disposed &&
    store === getStoreSlug() &&
    revision === getSession(role).revision &&
    isCurrent();
  const delay = Math.min(
    60000,
    Math.max(1000, Math.min(...deadlines) - Date.now() + 500),
  );
  const timer = setTimeout(() => {
    if (!current()) return;
    void Promise.resolve()
      .then(() => (current() ? reload() : undefined))
      .catch(() => {
        /* The resource owns its error and retry. */
      });
  }, delay);
  return () => {
    disposed = true;
    clearTimeout(timer);
  };
}

export function providerOrderActions(order: BusinessRecord): string[] {
  const allowed = (action: string, fallback: boolean) =>
    Array.isArray(order.allowed_actions)
      ? order.allowed_actions.includes(action)
      : fallback;
  if (order.my_assignment_released === true) return [];
  if (order.status === "PENDING") return allowed("GRAB", true) ? ["GRAB"] : [];
  if (order.status === "GRABBED" && allowed("START", !!order.can_operate))
    return ["START"];
  if (order.status === "IN_SERVICE" && allowed("START_PARTICIPATION", false))
    return ["START_PARTICIPATION"];
  if (order.status === "IN_SERVICE" && allowed("COMPLETE", !!order.can_operate))
    return ["COMPLETE"];
  return [];
}

export function providerReportDestination(order: BusinessRecord): string {
  return order.report_entry?.available && positiveId(order.id)
    ? `/reports?order=${order.id}`
    : "";
}

export function providerReportFocus(query: URLSearchParams): {
  orderId: string;
  reportId: string;
} {
  return {
    orderId: positiveId(query.get("order") || query.get("order_id")),
    reportId: positiveId(query.get("report") || query.get("report_id")),
  };
}

function providerIncomeDisplay(record: BusinessRecord) {
  const adjusted =
    record.status === "CANCELLED" ||
    record.order_status === "CANCELLED" ||
    ["REFUNDED", "PARTIALLY_REFUNDED"].includes(record.payment_status);
  return {
    adjusted,
    displayIncome: adjusted
      ? (record.refund_summary?.net_income ?? null)
      : (record.expected_income ?? null),
  };
}

export function providerReportPresentation(report: BusinessRecord) {
  const income = providerIncomeDisplay(report);
  const missing = [
    (!report.entry_image_url || report.evidence_upload_block_reasons?.ENTRY) &&
      "入队截图",
    (!report.completion_image_url ||
      report.evidence_upload_block_reasons?.COMPLETION) &&
      "结单截图",
    !(report.result_image_urls || []).length && "战绩截图",
  ].filter(Boolean) as string[];
  return {
    missing,
    incomeLabel: report.negotiated_settlement === true ? "协商保留收益" : income.adjusted ? "服务收益流水净额" : "打手单钱",
    displayIncome: report.negotiated_settlement === true ? (report.expected_income ?? null) : income.displayIncome,
    aftersaleOpen: report.aftersale_status === "OPEN",
    netIncome: report.refund_summary?.net_income ?? null,
    refundedAmount: report.refund_summary?.amount ?? 0,
    remainingFrozen: report.refund_summary?.remaining_frozen_amount ?? null,
    expectedIncome: report.expected_income ?? null,
    canEdit: report.can_edit === true,
    canSubmit:
      report.can_edit === true && report.can_submit === true && !missing.length,
    canWithdraw:
      report.status === "PENDING" && report.can_withdraw === true,
    settlementNotice:
      report.status !== "APPROVED"
        ? ""
        : report.negotiated_settlement === true
          ? report.settlement_status === "SETTLED"
            ? "收益已结算，可用金额以钱包为准。"
            : report.settlement_status === "WAITING"
              ? "凭证已通过，等待本人结算条件满足。"
              : "收益凭证待核对，请联系客服。"
        : income.adjusted
          ? "取消/退款后的实际收益以钱包流水为准。"
          : report.platform_funding_insufficient
            ? "平台补贴资金暂不足，补足后自动结算。"
            : report.settlement_status === "SETTLED"
              ? "收益已结算，冻结及可用金额以钱包为准。"
              : "凭证已通过，等待订单实际结算条件满足。",
  };
}

export function providerOrderPresentation(order: BusinessRecord) {
  const income = providerIncomeDisplay(order);
  return {
    zeroIncomeNotice:
      order.expected_income === 0 ? coinText("本单预计收益为0浪花，请核对后继续。") : "",
    incomeLabel: income.adjusted ? "服务收益流水净额" : "预计",
    displayIncome: income.displayIncome,
    settlementLabel: income.adjusted
      ? "取消/退款后的实际收益以钱包流水为准"
      : order.my_assignment_settled === true
        ? "个人份额已结算，金额以钱包流水为准"
      : order.settlement_status === "SETTLED"
        ? "订单已结算，个人到账请查看钱包流水"
        : order.settlement_status === "WAITING"
          ? "订单待结算"
          : "待核对",
    netIncome: order.refund_summary?.net_income ?? null,
    refundedAmount: order.refund_summary?.amount ?? 0,
    remainingFrozen: order.refund_summary?.remaining_frozen_amount ?? null,
    expectedIncome: order.expected_income ?? null,
    aftersaleOpen: order.aftersale_status === "OPEN",
    reportDestination: providerReportDestination(order),
    actions: providerOrderActions(order),
  };
}

export function orderBusinessDetails(
  order: BusinessRecord,
  role: "boss" | "provider",
  accountId?: number | string,
) {
  const showGameAccount =
    role === "boss" ||
    (["GRABBED", "IN_SERVICE"].includes(order.status) &&
      order.my_assignment_released !== true &&
      (order.my_assignment_released === false ||
        order.can_operate ||
        order.participants?.some(
          (row: BusinessRecord) => String(row.id) === String(accountId),
        )));
  const showGameRegion =
    showGameAccount || (order.status === "PENDING" && !!order.game_region);
  const origin = order.reservation_origin;
  return {
    showGameAccount: !!showGameAccount,
    showGameRegion: !!showGameRegion,
    gameRegion: showGameRegion ? order.game_region || "" : "",
    gameNickname: showGameAccount ? order.game_nickname || "" : "",
    gameUid: showGameAccount ? order.game_uid || "" : "",
    fulfillment: order.fulfillment || "",
    gameplayRules: order.gameplay_snapshot?.rules || "",
    reservation: origin
      ? {
          id: origin.id,
          startTime: origin.start_time,
          endTime: origin.end_time,
          destination:
            role === "boss"
              ? route("/reservation-detail", { id: origin.id })
              : origin.can_open
                ? route("/reservations", { id: origin.id })
                : "",
        }
      : null,
    cancelReason:
      role === "boss" && bossOrderPresentation(order).isUnconfirmed
        ? ""
        : order.cancel_reason || "",
    cancelReasonLabel: role === "boss" ? "取消说明" : "取消/退款原因",
  };
}

/** A mounted route owns its mutations; switching focus never revives an old capture. */
export function createViewScopeGuard(role: Role, initialScope: string) {
  let scope = initialScope,
    active = true,
    generation = 0;
  return {
    setScope(value: string) {
      if (scope !== value) {
        scope = value;
        generation++;
      }
    },
    activate() {
      if (!active) {
        active = true;
        generation++;
      }
    },
    dispose() {
      active = false;
      generation++;
    },
    capture() {
      const version = generation,
        store = getStoreSlug(),
        session = getSession(role);
      return () =>
        active &&
        version === generation &&
        store === getStoreSlug() &&
        session.revision === getSession(role).revision;
    },
  };
}

export function evaluationFocus(params: URLSearchParams): string {
  return positiveId(params.get("order"));
}
export async function loadProviderEvaluations<
  T extends BusinessRecord = BusinessRecord,
>({
  page,
  orderId,
  evaluationId,
}: {
  page: number;
  orderId?: string;
  evaluationId?: number;
}) {
  return createApiClient("provider").get<{
    list: T[];
    total: number;
    pending_total: number;
    filtered_total: number;
  }>("/orders/evaluations/mine/", {
    page,
    page_size: evaluationId ? 1 : 20,
    order_id: orderId || undefined,
    evaluation_id: evaluationId || undefined,
  });
}
export async function submitProviderEvaluationReply(
  row: BusinessRecord,
  draft: string,
) {
  return createApiClient("provider").post(
    `/orders/evaluations/${row.id}/reply/`,
    { reply_content: draft.trim(), reply_version: row.reply_version },
  );
}
export function evaluationReplyFailure(cause: unknown) {
  const error = cause as BusinessRecord;
  const unknown =
    !error.status ||
    error.status >= 500 ||
    (typeof error.code === "number" && error.code >= 500) ||
    error.code === 409 ||
    error.code === 429;
  if (error.errorCode === "EVALUATION_REPLY_CONFLICT" || unknown)
    return {
      blocked: true,
      notice:
        error.errorCode === "EVALUATION_REPLY_CONFLICT"
          ? "回复已在其他位置更新，草稿已保留。请读取最新回复后核对。"
          : "回复结果未确认，请读取最新回复核对，暂勿重新发布。",
    };
  if (error.status === 404 || error.code === 404)
    return { blocked: true, notice: "评价已不存在，关闭前可复制草稿。" };
  return {
    blocked: false,
    notice: cause instanceof Error ? cause.message : "请求失败，请重试",
  };
}
export function evaluationLatestReply(
  row: BusinessRecord | null | undefined,
  draft: string,
) {
  return row
    ? {
        blocked: false,
        notice:
          draft.trim() === String(row.reply_content || "").trim()
            ? "当前回复与草稿一致，无需重复发布。"
            : "已读取最新回复，请核对后决定是否发布草稿。",
      }
    : { blocked: true, notice: "评价已不存在，关闭前可复制草稿。" };
}

/** Cursor lists replace refreshed rows and retain the user's expanded window. */
export function createPaginationController<
  T extends { id: number | string },
>() {
  const channels = new Map<string, T[]>();
  const dedupe = (items: T[]) => [
    ...new Map(items.map((row) => [row.id, row])).values(),
  ];
  return {
    rows(key: string) {
      return channels.get(key) || [];
    },
    merge(key: string, items: T[], append = false) {
      const result = dedupe(
        append ? [...(channels.get(key) || []), ...items] : items,
      );
      channels.set(key, result);
      return result;
    },
    async readExpanded<P extends { items: T[]; next: number | null }>(
      key: string,
      read: (before: number | null) => Promise<P>,
      before: number | null,
    ): Promise<P> {
      const previous = channels.get(key) || [],
        oldest = previous.at(-1)?.id;
      const first = await read(before),
        items = [...first.items];
      let next = first.next;
      while (
        !before &&
        oldest != null &&
        next &&
        !items.some((row) => row.id === oldest)
      ) {
        const page = await read(next);
        items.push(...page.items);
        if (page.next === next) break;
        next = page.next;
      }
      return { ...first, items: dedupe(items), next };
    },
  };
}
export function pageBounds(page: number, total: number, size = 20) {
  const last = Math.max(1, Math.ceil(total / size));
  return {
    last,
    page: Math.min(page, last),
    visible: total > size,
    canPrevious: page > 1,
    canNext: page * size < total,
  };
}
export async function loadProviderApplications<
  T extends BusinessRecord = BusinessRecord,
>(page: number) {
  return createApiClient("provider").get<{
    list: T[];
    total: number;
    page_size: number;
  }>("/orders/applications/mine/", { page });
}
export async function loadProviderBenefits<T = BusinessRecord>(
  tab: string,
  page: number,
): Promise<T> {
  return createApiClient("provider").get<T>("/wallet/provider-benefits/", {
    dispose_page: tab === "dispose" ? page : 1,
    gift_page: tab === "gift" ? page : 1,
    page_size: 20,
  });
}
export function providerApplicationPresentation(detail: BusinessRecord) {
  return {
    notice:
      detail.closed_reason ||
      detail.selection_reason ||
      (detail.application.ready
        ? "队伍已确认，等待老板选择。"
        : detail.confirm_reason),
    canConfirm: !!detail.can_confirm,
    canWithdraw:
      detail.application.status === "PENDING" && !detail.closed_reason,
  };
}
export function providerAccountPresentation(
  profile: BusinessRecord | null | undefined,
  deposit: BusinessRecord | null | undefined,
) {
  return {
    status: profile?.status || "",
    statusOptions: [
      { value: "AVAILABLE", label: "接单中" },
      { value: "BUSY", label: "忙碌" },
      { value: "OFFLINE", label: "离线" },
    ],
    deposit: deposit
      ? {
          modeLabel: deposit.deposit_mode === "EARNINGS" ? "单抵" : "现金",
          required: deposit.deposit_required,
          paid: deposit.deposit_paid,
          remaining: deposit.deposit_remaining,
          balance: deposit.balance,
          canPay: deposit.balance >= deposit.deposit_remaining,
          freezeNotice: deposit.hold_hours
            ? `新订单收益按创建时规则冻结 ${deposit.hold_hours} 小时；`
            : "当前新订单不启用收益冻结；",
          settlementNotice:
            deposit.deposit_mode === "EARNINGS" && deposit.deposit_remaining > 0
              ? `符合条件的净收益按 ${deposit.deposit_percent || 0}% 抵扣押金，实际记录以钱包为准。`
              : "具体到账和可用金额请查看钱包。",
        }
      : null,
  };
}
export async function updateProviderStatus(status: string) {
  return createApiClient("provider").post("/users/escorts/status/", { status });
}

/** There is no report-detail GET API; use the real owned, paginated list. */
export async function loadProviderReportPage<
  T extends BusinessRecord = BusinessRecord,
>(page: number, focus: { orderId: string; reportId: string }) {
  const api = createApiClient("provider");
  const read = (number: number) =>
    api.get<{ list: T[]; total: number }>("/wallet/reports/", {
      page: number,
      page_size: 20,
      order_id: focus.orderId || undefined,
    });
  if (!focus.reportId) return read(page);
  let number = 1;
  while (true) {
    const result = await read(number);
    const report = result.list.find((row) => String(row.id) === focus.reportId);
    if (report) return { list: [report], total: 1 };
    if (!result.list.length || number * 20 >= result.total)
      return { list: [], total: 0 };
    number++;
  }
}

export function walletTransactionPresentation(row: BusinessRecord) {
  let label: string;
  if (row.is_referral_income)
    label = row.amount < 0 ? "推荐收益退回" : "推荐分佣";
  else if (row.tx_type === "PASS_PURCHASE" && row.is_reversal && row.amount > 0)
    label = "通行证退款";
  else if (row.tx_type === "INCOME" && row.amount < 0) label = "收益退回";
  else if (row.tx_type === "GIFT")
    label =
      row.gift_source === "ORDER_GIFT"
        ? "礼物收益"
        : ["RECHARGE_GIFT", "STAFF_RECHARGE"].includes(row.gift_source)
          ? "客服赠送"
          : "赠送";
  else
    label =
      (
        {
          TOPUP: "充值",
          PAY: "订单支付",
          INCOME: "收益",
          WITHDRAW: "提现",
          DEPOSIT: "押金",
          REWARD: "奖励",
          PENALTY: "罚款",
          PASS_PURCHASE: "通行证",
          REFUND: "退款",
          ADJUST: "调账",
          EARNINGS_FREEZE: "收益冻结",
          EARNINGS_RELEASE: "收益解冻",
        } as Record<string, string>
      )[row.tx_type] || row.tx_type;
  if (
    row.is_reversal &&
    !row.is_referral_income &&
    !(row.tx_type === "PASS_PURCHASE" && row.amount > 0) &&
    !(row.tx_type === "INCOME" && row.amount < 0)
  )
    label += "冲回";
  return {
    label,
    amount: row.amount,
    isReferral: row.is_referral_income === true,
    giftSource: row.gift_source || "",
    status: row.status,
  };
}

export function giftBenefitPresentation(row: BusinessRecord) {
  return {
    receivedIncome:
      row.settlement_status === "REFUNDED" ? 0 : row.received_income,
    providerIncome: row.provider_income,
    settlementLabel:
      (
        {
          SETTLED: "已入账",
          FROZEN: "收益冻结中，满足售后及冻结条件后释放",
          REFUNDED: "已退款，收益已冲回",
          PARTIALLY_REFUNDED: "部分退款，保留净实得",
          PARTIALLY_REFUNDED_FROZEN: "部分退款，剩余收益冻结中",
          PENDING: "待入账",
        } as Record<string, string>
      )[row.settlement_status] || "",
    sourceLabel:
      typeof row.source_order_no === "string"
        ? row.source_order_no
          ? `服务订单 ${row.source_order_no}`
          : "直接赠送"
        : "",
  };
}

export function reservationRebookTarget(reservation: BusinessRecord): string {
  return route("/reservation-create", {
    provider_id: positiveId(
      reservation.provider?.account_id || reservation.provider_account_id,
    ),
    service_id: positiveId(reservation.service?.id || reservation.service_id),
  });
}

/** Re-read after every outcome, retaining the original mutation error if unknown. */
export async function mutateAndReload<T>({
  role,
  mutate,
  reload,
  isCurrent = () => true,
}: {
  role: Role;
  mutate: () => Promise<T>;
  reload: () => Promise<unknown>;
  isCurrent?: () => boolean;
}) {
  const store = getStoreSlug(),
    revision = getSession(role).revision;
  const current = () =>
    getStoreSlug() === store &&
    getSession(role).revision === revision &&
    isCurrent();
  let value: T | undefined, error: unknown, reloadError: unknown;
  try {
    value = await mutate();
  } catch (cause) {
    error = cause;
  }
  if (!current())
    return {
      value: undefined,
      error: sessionChangedError(),
      reloadError,
      stale: true,
    };
  try {
    await reload();
  } catch (cause) {
    reloadError = cause;
  }
  return current()
    ? { value, error, reloadError, stale: false }
    : {
        value: undefined,
        error: sessionChangedError(),
        reloadError,
        stale: true,
      };
}

export async function uploadProviderReportImages({
  reportId,
  kind,
  files,
  existingCount = 0,
  reload,
  isCurrent = () => true,
}: {
  reportId: number;
  kind: "ENTRY" | "COMPLETION" | "RESULT";
  files: File[];
  existingCount?: number;
  reload: () => Promise<unknown>;
  isCurrent?: () => boolean;
}) {
  const limit = kind === "RESULT" ? Math.max(0, 9 - existingCount) : 1;
  let uploaded = 0;
  const outcome = await mutateAndReload({
    role: "provider",
    reload,
    isCurrent,
    mutate: async () => {
      for (const file of files.slice(0, limit)) {
        if (!isCurrent()) throw sessionChangedError();
        const form = new FormData();
        form.append("kind", kind);
        form.append("image", file);
        if (kind === "RESULT")
          form.append(
            "client_request_id",
            `report-image-${randomUuid()}`,
          );
        await createApiClient("provider").upload(
          `/wallet/reports/${reportId}/images/`,
          form,
        );
        uploaded++;
      }
    },
  });
  return { ...outcome, uploaded, skipped: Math.max(0, files.length - limit) };
}

export function mayMarkMessagesRead({
  hidden,
  focused = true,
  browsing = false,
}: {
  hidden: boolean;
  focused?: boolean;
  browsing?: boolean;
}): boolean {
  return !hidden && focused && !browsing;
}

export function createRefreshCoordinator({
  reload,
  isVisible,
  types = () => undefined,
  delay = 350,
  role = "provider",
}: {
  reload: () => Promise<unknown>;
  isVisible: () => boolean;
  types?: () => string[] | undefined;
  delay?: number;
  role?: Role;
}) {
  const store = getStoreSlug(),
    revision = getSession(role).revision;
  const sameIdentity = () =>
    getStoreSlug() === store && getSession(role).revision === revision;
  let timer: ReturnType<typeof setTimeout> | undefined,
    disposed = false,
    running = false,
    trailing = false;
  const schedule = (): boolean => {
    if (disposed || !sameIdentity() || !isVisible()) return false;
    if (running) {
      trailing = true;
      return true;
    }
    if (timer) return true;
    timer = setTimeout(async () => {
      timer = undefined;
      if (disposed || !sameIdentity() || !isVisible()) return;
      running = true;
      try {
        await reload();
      } catch {
        /* The resource owns its visible error and retry. */
      } finally {
        running = false;
        if (trailing && !disposed) {
          trailing = false;
          schedule();
        }
      }
    }, delay);
    return true;
  };
  return {
    event(type: string) {
      const filter = types();
      return !filter ||
        filter.includes(type) ||
        ["connected", "reconnected"].includes(type)
        ? schedule()
        : false;
    },
    resume: schedule,
    dispose() {
      disposed = true;
      trailing = false;
      if (timer) clearTimeout(timer);
      timer = undefined;
    },
  };
}

export interface ChatIntent {
  id: string;
  content: string;
  kind: string;
  attempted?: boolean;
  [key: string]: unknown;
}

export function chatPendingKey(role: Role): string {
  const account = getSession(role).user?.id;
  if (!account) throw new Error("请先登录后核对原消息");
  const suffix =
    role === "boss"
      ? `xa:boss:web:chat:pending:${account}`
      : role === "provider"
        ? `xa:provider:chat-pending:${account}`
        : `xa:fronted:admin:chat-pending:${account}`;
  return `xa:store:${getStoreSlug()}:${suffix}`;
}

export function providerChatScope(params: URLSearchParams): string {
  const scope = params.get("order_no") || "";
  return /^ORD[0-9A-F]{20}$/i.test(scope) ? scope.toUpperCase() : "";
}
export function chatDraftKey(role: "boss" | "provider", scope: string): string {
  const account = getSession(role).user?.id;
  if (!account) throw new Error("请先登录后恢复草稿");
  return `xa:store:${getStoreSlug()}:${role === "boss" ? `xa:boss:web:chat:draft:${account}:${scope || "general"}` : `xa:provider:chat-draft:${account}:${scope}`}`;
}
export function readChatDraft(
  role: "boss" | "provider",
  scope: string,
): string {
  try {
    const raw = (role === "boss" ? sessionStorage : localStorage).getItem(
      chatDraftKey(role, scope),
    );
    const value = role === "boss" ? raw : JSON.parse(raw || '""');
    return typeof value === "string" ? value : "";
  } catch {
    return "";
  }
}
export function saveChatDraft(
  role: "boss" | "provider",
  scope: string,
  text: string,
): { saved: boolean; error?: Error } {
  try {
    if (typeof text !== "string") throw new Error("草稿只能保存文字");
    const storage = role === "boss" ? sessionStorage : localStorage,
      key = chatDraftKey(role, scope);
    if (text)
      storage.setItem(key, role === "boss" ? text : JSON.stringify(text));
    else storage.removeItem(key);
    return { saved: true };
  } catch {
    return {
      saved: false,
      error: new Error("设备存储不可用，当前草稿仍保留在页面中。"),
    };
  }
}
export function clearChatDraft(
  role: "boss" | "provider",
  scope: string,
  expectedText?: string,
): { saved: boolean; cleared: boolean; error?: Error } {
  if (
    expectedText !== undefined &&
    readChatDraft(role, scope).trim() !== expectedText
  )
    return { saved: true, cleared: false };
  const result = saveChatDraft(role, scope, "");
  return { ...result, cleared: result.saved };
}
export function removeProviderChatContext(scope: string, input: string) {
  if (!scope) return { conflict: false, saved: true, targetDraft: input };
  const general = readChatDraft("provider", "");
  if (general && general !== input)
    return { conflict: true, saved: true, targetDraft: general };
  const saved = saveChatDraft("provider", "", input);
  if (!saved.saved) return { ...saved, conflict: false, targetDraft: input };
  const removed = clearChatDraft("provider", scope);
  return { ...removed, conflict: false, targetDraft: input };
}

function chatMetadata(intent: ChatIntent): ChatIntent {
  if (
    !intent.id ||
    typeof intent.content !== "string" ||
    !["text", "image"].includes(String(intent.kind).toLowerCase())
  )
    throw new Error("消息凭证无效，请先核对原消息");
  const value: ChatIntent = {
    id: intent.id,
    content: intent.content,
    kind: intent.kind,
  };
  for (const key of ["draft", "scope", "draftKey", "order_ref"] as const)
    if (typeof intent[key] === "string") value[key] = intent[key];
  for (const key of ["attempted", "restored"] as const)
    if (typeof intent[key] === "boolean") value[key] = intent[key];
  return value;
}

export function readChatPending<T extends ChatIntent = ChatIntent>(
  role: Role,
): T | null {
  try {
    const raw = localStorage.getItem(chatPendingKey(role));
    if (!raw) return null;
    return { ...chatMetadata(JSON.parse(raw)), restored: true } as unknown as T;
  } catch {
    return {
      id: "",
      kind: role === "provider" ? "TEXT" : "text",
      content: "原消息凭证无法读取，请先核对聊天记录并联系客服。",
      draft: "",
      scope: "",
      draftKey: "",
      restored: true,
      blocked: true,
    } as unknown as T;
  }
}

export function saveChatPending(
  role: Role,
  intent: ChatIntent,
): { saved: boolean; error?: Error } {
  try {
    localStorage.setItem(
      chatPendingKey(role),
      JSON.stringify(chatMetadata(intent)),
    );
    return { saved: true };
  } catch {
    return {
      saved: false,
      error: new Error("设备存储不可用，请保持当前页面直至核对发送结果。"),
    };
  }
}

export function clearChatPending(
  role: Role,
  expectedId?: string,
): { saved: boolean; cleared?: boolean; error?: Error } {
  try {
    const key = chatPendingKey(role);
    if (
      expectedId !== undefined &&
      JSON.parse(localStorage.getItem(key) || "null")?.id !== expectedId
    )
      return { saved: true, cleared: false };
    localStorage.removeItem(key);
    return { saved: true, cleared: true };
  } catch {
    return {
      saved: false,
      cleared: false,
      error: new Error(
        "设备存储不可用，原消息凭证已保留在当前页面，请继续核对。",
      ),
    };
  }
}
export async function deliverChatMessage<
  T extends BusinessRecord = BusinessRecord,
>(role: Role, intent: ChatIntent, file?: File | null): Promise<T> {
  if (!intent.id) throw new Error("消息凭证不完整，请先核对原消息");
  const api = createApiClient(role);
  const orderRef = typeof intent.order_ref === "string" ? intent.order_ref : "";
  let data: unknown = { content: intent.content, client_message_id: intent.id,
    ...(orderRef ? { order_ref: orderRef } : {}) };
  if (intent.kind.toLowerCase() === "image") {
    if (!file) throw new Error("图片文件已不可读取，请先核对发送结果");
    const form = new FormData();
    form.append("content", intent.content);
    form.append("client_message_id", intent.id);
    if (orderRef) form.append("order_ref", orderRef);
    form.append("image", file);
    data = form;
  }
  const result = await api.post<T>("/chat/send/", data);
  if (result.client_message_id !== intent.id)
    throw new Error("消息结果未确认，请继续核对原消息");
  return result;
}
export async function lookupChatMessage<
  T extends BusinessRecord = BusinessRecord,
>(
  role: Role,
  intent: ChatIntent,
): Promise<{ status: string; message: T | null }> {
  const result = await createApiClient(role).get<{
    status: string;
    message: T | null;
  }>("/chat/message-status/", { client_message_id: intent.id });
  if (
    result.status === "FOUND" &&
    result.message?.client_message_id !== intent.id
  )
    throw new Error("消息结果未确认，请继续核对原消息");
  return result;
}


/** One request has distinct open/resolved history entries, not one React key. */
export function afterSaleHistoryKey(item: BusinessRecord, index: number): string {
  return [item.client_request_id || "aftersale", item.status || "UNKNOWN",
    item.resolved_at || item.created_at || "pending", index].join(":");
}
