import { Gamepad2, Globe2, MessageCircle, RefreshCw } from "lucide-react";
import { useState } from "react";
import { hostStoreSlug, storeBrandLogo } from "./store.ts";
import headsetImage from "../assets/store-closed-headset.webp";
import "./store-closed.css";

type Props = {
  role: "boss" | "provider";
  entryName: string;
  checkedAt: Date;
  checking: boolean;
  onRetry: () => void;
};

export function StoreClosedPage({ role, entryName, checkedAt, checking, onRetry }: Props) {
  const boss = role === "boss";
  const hostname = window.location.hostname;
  const brandSlug = hostStoreSlug(hostname);
  const [dark] = useState(() => {
    try {
      const saved = localStorage.getItem(boss ? "xa:boss:web:theme" : "xa_fronted_provider_theme");
      if (saved === "dark" || saved === "light") return saved === "dark";
    } catch { /* The entrance also works without browser storage. */ }
    return document.documentElement.classList.contains("dark") || window.matchMedia("(prefers-color-scheme: dark)").matches;
  });

  return (
    <div className={`xa-store-closed${dark ? " dark" : ""}`}>
      <div className="xa-store-closed-wrap">
        <header className="xa-store-closed-header">
          <div className="xa-store-closed-identity">
            {brandSlug
              ? <img src={storeBrandLogo({ slug: brandSlug }, import.meta.env.BASE_URL)} alt="" width="32" height="32" />
              : <Gamepad2 aria-hidden="true" size={30} />}
            <span>{entryName}</span>
          </div>
          <span className="xa-store-closed-host" dir="ltr"><Globe2 aria-hidden="true" size={15} />{hostname}</span>
        </header>

        <main className="xa-store-closed-main">
          <div className="xa-store-closed-copy">
            <h1>门店暂未开放</h1>
            <p className="xa-store-closed-description">{boss
              ? "你暂时无法在这里选陪玩或下单。稍后可以回到本页，查看门店是否已开放。"
              : "你暂时无法进入工作台接单或报单。稍后可以回到本页，查看门店是否已开放。"}</p>
            <div className="xa-store-closed-actions">
              <button type="button" aria-disabled={checking} onClick={() => { if (!checking) onRetry(); }}>
                <RefreshCw size={17} aria-hidden="true" />
                {checking ? "正在检查…" : "重新检查状态"}
              </button>
              <p role="status" aria-atomic="true">{checking ? "正在查询门店状态…" : <>
                上次检查 <time dateTime={checkedAt.toISOString()}>{checkedAt.toLocaleTimeString("zh-CN", { hour12: false })}</time>
                <span className="sr-only">，门店暂未开放</span>
              </>}</p>
            </div>
            <aside className="xa-store-closed-help">
              <MessageCircle size={19} aria-hidden="true" />
              <div>
                <h2>{boss ? "已有订单需要处理？" : "有进行中的服务？"}</h2>
                <p>{boss ? "如需查询订单或申请售后，请通过下单时的客服渠道联系门店。" : "如需交接服务或报单，请通过平时使用的渠道联系门店负责人。"}</p>
              </div>
            </aside>
          </div>
          <img className="xa-store-closed-art" src={headsetImage} width="900" height="1125" alt="" />
        </main>
      </div>
    </div>
  );
}
