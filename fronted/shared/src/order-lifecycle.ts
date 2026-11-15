/** Presentation only. The server owns transitions, authorization and money. */
export const ORDER_STATES = {
  PENDING: "待接单",
  GRABBED: "已接单",
  IN_SERVICE: "服务中",
  COMPLETED: "已完成",
  CANCELLED: "已取消",
} as const;
export const ORDER_PAYMENTS = {
  UNPAID: "未支付",
  PAID: "已支付",
  PARTIALLY_REFUNDED: "部分退款",
  REFUNDED: "已退款",
} as const;
export const ORDER_SOURCES = {
  STAFF_MANUAL: "客服代下单",
  BOSS_H5: "老板 H5 下单",
  RESERVATION: "预约转单",
  GIFT: "礼物单",
} as const;
export const ORDER_ROUTES = { STAFF: "客服派单", PUBLIC: "公开接单" } as const;
export type OrderFacts = {
  status?: string;
  payment_status?: string;
  payment_display?: { kind?: string };
  dispatch_route?: string;
  order_source?: string;
  requires_boss_selection?: boolean;
  pending_deadline_expired?: boolean;
  aftersale_status?: string;
  settlement_status?: string;
  created_at?: string;
  grabbed_at?: string | null;
  started_at?: string | null;
  in_service_at?: string | null;
  completed_at?: string | null;
  cancelled_at?: string | null;
  updated_at?: string;
};
const label = (dict: Record<string, string>, value: string | undefined) =>
  value ? dict[value] || `待核对（${value}）` : "待核对";
export function orderSourceText(
  order: Pick<OrderFacts, "order_source">,
): string {
  // dispatch_route is routing, never proof of who created the order.
  return order.order_source
    ? label(ORDER_SOURCES, order.order_source)
    : "来源待核对";
}
export function orderLifecycle(order: OrderFacts) {
  const paymentUncertain = order.payment_display?.kind === "DIRECT_TEST";
  const payment = paymentUncertain
    ? "支付状态待核对"
    : label(ORDER_PAYMENTS, order.payment_status);
  const status = label(ORDER_STATES, order.status);
  const pending = order.status === "PENDING";
  const waitingPayment = order.payment_status === "UNPAID" || paymentUncertain;
  const dispatch =
    order.status === "CANCELLED"
      ? "派单已结束"
      : !pending
        ? order.status === "COMPLETED"
          ? "履约已结束"
          : ["GRABBED", "IN_SERVICE"].includes(order.status || "")
            ? "已分配陪玩"
            : "派单状态待核对"
        : waitingPayment
          ? "付款待核对，暂不派单"
          : order.payment_status !== "PAID"
            ? "派单状态待核对"
            : order.pending_deadline_expired
              ? "接单期限已到，等待处理"
              : order.requires_boss_selection
                ? "等待老板选人"
                : order.dispatch_route === "STAFF"
                  ? "等待客服安排"
                  : order.dispatch_route === "PUBLIC"
                    ? "公开接单中"
                    : "派单路径待核对";
  const aftersale = label(
    {
      OPEN: "售后处理中",
      RESOLVED: "售后已结案",
      REFUNDED: "售后已退款",
      NONE: "无售后",
    },
    order.aftersale_status,
  );
  const settlement = label(
    { WAITING: "待整单结算", SETTLED: "已结算", REVIEW: "收益待核对" },
    order.settlement_status,
  );
  const next = paymentUncertain
    ? "请核对付款结果，再继续操作。"
    : order.status === "CANCELLED"
      ? "查看支付与实际退款记录，取消不代表退款已到账。"
      : order.aftersale_status === "OPEN"
        ? "查看售后进展；履约、退款和收益分别以实际记录为准。"
        : pending
          ? dispatch === "等待老板选人"
            ? "老板选择后才算分配成功；报名或组队提交不等于接单。"
            : waitingPayment
              ? "核对支付记录后刷新订单。"
              : "分配结果以订单详情为准，消息发送不代表接单成功。"
          : order.status === "GRABBED"
            ? "陪玩按要求提交入场凭证并开始服务。"
            : order.status === "IN_SERVICE"
              ? "按履约要求完成服务；报单审核与订单完成分别核对。"
              : order.status === "COMPLETED"
                ? "可查看评价、售后与报单；完成不代表收益已可提现。"
                : "刷新详情核对当前状态。";
  return {
    status,
    payment,
    dispatch,
    source: orderSourceText(order),
    route: label(ORDER_ROUTES, order.dispatch_route),
    aftersale,
    settlement,
    next,
    paymentUncertain,
    waitingPayment,
  };
}

export type OrderEvent = {
  id: string;
  at: string;
  title: string;
  actor?: string;
  remark?: string;
  version?: string;
};
/** Only display observed events; never invent timestamps for intermediate steps. */
export function orderFactEvents(order: OrderFacts): OrderEvent[] {
  return (
    [
      ["created", order.created_at, "订单创建"],
      ["assigned", order.grabbed_at, "已接单"],
      ["started", order.in_service_at || order.started_at, "开始服务"],
      ["completed", order.completed_at, "订单完成"],
      ["cancelled", order.cancelled_at, "订单取消"],
    ] as const
  )
    .flatMap(([id, at, title]) =>
      at && Number.isFinite(Date.parse(at)) ? [{ id, at, title }] : [],
    )
    .sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
}
