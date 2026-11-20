import { ClipboardList, Gamepad2, Globe2, Headphones, MessageCircle, RefreshCw, ShoppingBag, UserCircle } from "lucide-react";
import { useState } from "react";
import { hostStoreSlug, storeBrandLogo } from "./store.ts";
import headsetImage from "../assets/store-closed-headset.webp";
import "./store-closed.css";

type Props = {
  role: "boss" | "provider";
  entryName: string;
  issue: { checkedAt: Date; closed: boolean; message: string } | null;
  checking: boolean;
  onRetry: () => void;
};

const storeNames: Record<string, string> = { shenglang: "声浪电竞", xinan: "兴安电竞" };
const entryContent = {
  boss: {
    services: [
      [Headphones, "挑选陪玩", "按游戏查看本店服务和陪玩资料。"],
      [ShoppingBag, "在线下单", "本店开通自助下单后，可选服务并核对价格。"],
      [ClipboardList, "查看订单", "查看自己的订单进度和售后记录。"],
    ] as const,
    helpTitle: "已有订单或售后问题？",
    help: "请通过下单时使用的客服渠道联系本店。提供订单编号，方便客服核对。",
  },
  provider: {
    services: [
      [UserCircle, "进入工作台", "用本店陪玩账号登录，查看待办。"],
      [Gamepad2, "接单与履约", "按接单资格查看订单、邀请和服务进度。"],
      [ClipboardList, "提交报单", "提交服务记录并查看审核结果。"],
    ] as const,
    helpTitle: "有服务正在进行？",
    help: "需要交接服务或补交报单时，请通过平时使用的渠道联系本店负责人，并提供订单编号和当前进度。",
  },
};

export function StoreEntryStatusPage({ role, entryName, issue, checking, onRetry }: Props) {
  const { services, helpTitle, help } = entryContent[role];
  const hostname = window.location.hostname;
  const brandSlug = hostStoreSlug(hostname);
  const storeName = brandSlug ? storeNames[brandSlug] : undefined;
  const [dark] = useState(() => {
    try {
      const saved = localStorage.getItem(role === "boss" ? "xa:boss:web:theme" : "xa_fronted_provider_theme");
      if (saved === "dark" || saved === "light") return saved === "dark";
    } catch { /* The entrance also works without browser storage. */ }
    return document.documentElement.classList.contains("dark") || window.matchMedia("(prefers-color-scheme: dark)").matches;
  });

  return (
    <div className={`xa-store-closed${dark ? " dark" : ""}`}>
      <div className="xa-store-closed-wrap">
        <header className="xa-store-closed-header">
          <div className="xa-store-closed-identity">
            {storeName && brandSlug
              ? <img src={storeBrandLogo({ slug: brandSlug }, import.meta.env.BASE_URL)} alt="" width="40" height="40" />
              : <Gamepad2 aria-hidden="true" size={28} />}
            <div>
              {storeName && <strong>{storeName}</strong>}
              <span>{entryName}</span>
            </div>
          </div>
          <span className="xa-store-closed-host" dir="ltr"><Globe2 aria-hidden="true" size={15} />{hostname}</span>
        </header>

        <main className="xa-store-closed-main">
          <div className="xa-store-closed-hero">
            <div className="xa-store-closed-copy">
              <h1>{issue?.closed ? "门店暂未开放" : issue ? "暂时无法进入门店" : "正在确认门店…"}</h1>
              {issue && (issue.closed
                ? <p className="xa-store-closed-description">开放时间以门店通知为准。请保留当前网址，稍后再来。</p>
                : <p className="xa-store-closed-description" role="alert">{issue.message}</p>)}
              {issue && (
                <div className="xa-store-closed-actions">
                  <button type="button" aria-disabled={checking} onClick={() => { if (!checking) onRetry(); }}>
                    <RefreshCw size={17} aria-hidden="true" />
                    {checking ? "正在检查…" : issue.closed ? "重新检查状态" : "重试"}
                  </button>
                  <p role="status" aria-atomic="true">{checking ? "正在查询门店状态…" : <>
                    {issue.closed ? "上次检查" : "上次查询失败"} <time dateTime={issue.checkedAt.toISOString()}>{issue.checkedAt.toLocaleTimeString("zh-CN", { hour12: false })}</time>
                    {issue.closed && <span className="sr-only">，门店暂未开放</span>}
                  </>}</p>
                </div>
              )}
            </div>
          </div>
          <div className="xa-store-closed-details">
            <section className="xa-store-closed-help">
              <MessageCircle size={22} aria-hidden="true" />
              <div>
                <h2>{helpTitle}</h2>
                <p>{help}</p>
              </div>
            </section>
            {issue?.closed && <section className="xa-store-closed-services">
              <h2>开放后可使用</h2>
              <ul role="list">
                {services.map(([Icon, label, description]) => <li key={label}>
                  <Icon size={18} aria-hidden="true" />
                  <div><h3>{label}</h3><p>{description}</p></div>
                </li>)}
              </ul>
            </section>}
          </div>
          <div className="xa-store-closed-art">
            <img src={headsetImage} width="900" height="1125" alt="" />
          </div>
        </main>
      </div>
    </div>
  );
}
