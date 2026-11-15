import { useEffect, useRef, useState } from "react";
import { formatDateTime } from "./hooks";
import {
  orderLifecycle,
  orderFactEvents,
  type OrderFacts,
  type OrderEvent,
} from "./order-lifecycle";

export function OrderSource({ order }: { order: OrderFacts }) {
  const view = orderLifecycle(order);
  return (
    <span className="xa-order-source">
      {view.source}
      <span aria-hidden="true"> · </span>
      {view.route}
    </span>
  );
}
export function OrderJourney({
  order,
  audience = "staff",
  events,
}: {
  order: OrderFacts;
  audience?: "staff" | "boss" | "provider";
  events?: OrderEvent[];
}) {
  const view = orderLifecycle(order);
  const rows = [
    ["订单", view.status],
    ["支付", view.payment],
    ["派单", view.dispatch],
    ...(audience !== "boss" ? [["结算", view.settlement]] : []),
    ...(order.aftersale_status ? [["售后", view.aftersale]] : []),
  ];
  const history = events || orderFactEvents(order);
  return (
    <section className="xa-order-journey" aria-label="订单流转与状态">
      <div className="xa-order-journey-heading">
        <h2>订单流转</h2>
        <OrderSource order={order} />
      </div>
      <dl className="xa-order-facts">
        {rows.map(([name, value]) => (
          <div key={name}>
            <dt>{name}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <p className="xa-order-next">{view.next}</p>
      <details className="xa-order-history">
        <summary>
          查看状态记录{history.length ? `（${history.length}）` : ""}
        </summary>
        {history.length ? (
          <ol>
            {history.map((event) => (
              <li key={event.id}>
                <strong>{event.title}</strong>
                <time>{formatDateTime(event.at)}</time>
                {event.actor && <span>{event.actor}</span>}
                {event.remark && <p>{event.remark}</p>}
              </li>
            ))}
          </ol>
        ) : (
          <p>暂无可核实的时间记录，请刷新详情或联系客服。</p>
        )}
      </details>
    </section>
  );
}

/** Quiet read fallback for lost sockets. Writes are never replayed. */
export function OrderSyncBar({
  loading,
  error,
  snapshot,
  reload,
}: {
  loading: boolean;
  error?: unknown;
  snapshot: unknown;
  reload: () => unknown;
}) {
  const [online, setOnline] = useState(
    () => typeof navigator === "undefined" || navigator.onLine,
  );
  const [lastRead, setLastRead] = useState<{ snapshot: unknown; at: number } | null>(null);
  const hasSnapshot = snapshot != null;
  const read = useRef(reload);
  const busy = useRef(loading);
  read.current = reload;
  busy.current = loading;
  useEffect(() => {
    if (snapshot == null) setLastRead(null);
    else if (!loading && !error) setLastRead({ snapshot, at: Date.now() });
  }, [snapshot, loading, error]);
  useEffect(() => {
    const refresh = () => {
      if (!document.hidden && navigator.onLine && !busy.current)
        void Promise.resolve()
          .then(() => read.current())
          .catch(() => {});
    };
    const connected = () => {
      setOnline(navigator.onLine);
      if (navigator.onLine) refresh();
    };
    const timer = window.setInterval(refresh, 30000);
    window.addEventListener("online", connected);
    window.addEventListener("offline", connected);
    return () => {
      clearInterval(timer);
      window.removeEventListener("online", connected);
      window.removeEventListener("offline", connected);
    };
  }, []);
  let status: string;
  if (!online) {
    status = hasSnapshot ? "网络已断开，当前保留上次读取的订单状态。" : "网络已断开，订单状态尚未读取。";
  } else if (loading) {
    status = hasSnapshot ? "正在刷新订单状态，当前保留上次读取结果…" : "正在读取订单状态…";
  } else if (error) {
    status = hasSnapshot ? "刷新失败，当前保留上次读取的订单状态。" : "订单状态读取失败，请重试。";
  } else if (!hasSnapshot) {
    status = "订单状态尚未读取。";
  } else if (lastRead && lastRead.snapshot === snapshot) {
    status = `订单状态最近读取于 ${formatDateTime(new Date(lastRead.at).toISOString())}`;
  } else {
    status = "已读取订单状态。";
  }
  return (
    <div className="xa-order-sync" role="status" aria-live="polite">
      <span>{status}</span>
      <button
        type="button"
        disabled={loading || !online}
        onClick={() =>
          void Promise.resolve()
            .then(reload)
            .catch(() => {})
        }
      >
        {error ? "重试读取" : "刷新状态"}
      </button>
    </div>
  );
}
