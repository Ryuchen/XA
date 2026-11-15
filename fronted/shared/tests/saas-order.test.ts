import { test } from "node:test";
import assert from "node:assert/strict";
import { orderLifecycle, orderSourceText, orderFactEvents } from "../src/order-lifecycle.ts";
import { h5BrandingPayload, h5OrderingBlocked, h5Foreground, DEFAULT_H5_BRANDING } from "../src/saas.ts";
import { verifySaasReceipt } from "../../admin/src/lib/saas-intent.ts";

test("订单来源不能从接单路径猜测，客服派单可承接 H5 自主下单", () => {
  const order = { status: "PENDING", payment_status: "PAID", dispatch_route: "STAFF", order_source: "BOSS_H5" };
  assert.equal(orderLifecycle(order).source, "老板 H5 下单");
  assert.equal(orderLifecycle(order).dispatch, "等待客服安排");
  assert.equal(orderSourceText({}), "来源待核对");
});
test("取消、退款、完成与结算保留各自事实", () => {
  const cancelled = orderLifecycle({ status: "CANCELLED", payment_status: "PAID", settlement_status: "WAITING" });
  assert.equal(cancelled.status, "已取消"); assert.equal(cancelled.payment, "已支付");
  assert.match(cancelled.next, /取消不代表退款/);
  const completed = orderLifecycle({ status: "COMPLETED", payment_status: "PARTIALLY_REFUNDED", settlement_status: "WAITING", aftersale_status: "OPEN" });
  assert.equal(completed.status, "已完成"); assert.equal(completed.payment, "部分退款");
  assert.equal(completed.settlement, "待整单结算"); assert.equal(completed.aftersale, "售后处理中");
});
test("付款未核实、选人与过期不会被展示为成功派单", () => {
  const unpaid = { status: "PENDING", payment_status: "UNPAID", dispatch_route: "PUBLIC" };
  assert.match(orderLifecycle(unpaid).dispatch, /暂不派单/);
  assert.equal(orderLifecycle({ ...unpaid, payment_status: "PAID", payment_display: { kind: "DIRECT_TEST" } }).payment, "支付状态待核对");
  assert.equal(orderLifecycle({ ...unpaid, payment_status: "PAID", requires_boss_selection: true }).dispatch, "等待老板选人");
  assert.equal(orderLifecycle({ ...unpaid, payment_status: "PAID", pending_deadline_expired: true }).status, "待接单");
});
test("时间线只使用服务端已提供的有效事实，不补中间事件", () => {
  const facts = Object.freeze({ created_at: "2026-11-11T01:00:00Z", status: "COMPLETED", completed_at: "2026-11-11T03:00:00Z", started_at: "invalid" });
  assert.deepEqual(orderFactEvents(facts).map(event => event.id), ["created", "completed"]);
  assert.equal(orderLifecycle({ status: "FUTURE_STATE" }).status, "待核对（FUTURE_STATE）");
});
test("H5 私人定制仅发出白名单字段，不包含固定模块配置", () => {
  const payload = h5BrandingPayload({ ...DEFAULT_H5_BRANDING, display_name: " 测试俱乐部 ", fixed_modules: { dispatch: "override" }, css: "body{}" } as typeof DEFAULT_H5_BRANDING);
  assert.equal(payload.display_name, "测试俱乐部");
  assert.deepEqual(Object.keys(payload).sort(), ["density", "display_name", "primary_color", "support_label", "welcome"]);
  assert.throws(() => h5BrandingPayload({ ...payload, primary_color: "url(javascript:test)" }));
  assert.equal(h5Foreground("#FFFFFF"), "#020617"); assert.equal(h5Foreground("#020617"), "#FFFFFF");
});
test("模块入口以服务端下单能力为准，未对接不改变既有真实接口", () => {
  assert.equal(h5OrderingBlocked(undefined), false);
  assert.equal(h5OrderingBlocked({ status: "EXPIRED", can_create_order: false, can_customize: false }), true);
  assert.equal(h5OrderingBlocked({ status: "ACTIVE", can_create_order: false, can_customize: true }), true);
});
test("结果未知的操作仅凭同店同请求的终态回执解除，不把未找到当失败", () => {
  const receipt = { store_slug: "shenglang", client_request_id: "original", status: "NOT_FOUND" as const };
  assert.equal(verifySaasReceipt(receipt, "shenglang", "original"), false);
  assert.equal(verifySaasReceipt({ ...receipt, status: "PROCESSING" }, "shenglang", "original"), false);
  assert.equal(verifySaasReceipt({ ...receipt, status: "APPLIED" }, "shenglang", "original"), true);
  assert.throws(() => verifySaasReceipt({ ...receipt, status: "APPLIED" }, "xinan", "original"));
  assert.throws(() => verifySaasReceipt({ ...receipt, status: "APPLIED" }, "shenglang", "new-request"));
});
