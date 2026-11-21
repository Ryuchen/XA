import { CalendarClock, Check, ClipboardList, Copy, Gamepad2, Globe2, Headphones, MessageCircle, RefreshCw, ShoppingBag, UserCircle } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
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
    unavailable: "暂时无法登录、下单或查看订单。",
    services: [
      [Headphones, "挑选陪玩", "按游戏查看本店服务和陪玩资料。"],
      [ShoppingBag, "在线下单", "本店开通自助下单后，可选服务并核对价格。"],
      [ClipboardList, "查看订单", "查看自己的订单进度和售后记录。"],
    ] as const,
    helpTitle: "已有订单或售后问题？",
    help: "请通过原下单渠道联系本店客服。提供订单编号和需要处理的问题，方便客服核对。",
  },
  provider: {
    unavailable: "暂时无法登录、接单或提交报单。",
    services: [
      [UserCircle, "进入工作台", "用本店陪玩账号登录，查看待办。"],
      [Gamepad2, "接单与履约", "按接单资格查看订单、邀请和服务进度。"],
      [ClipboardList, "提交报单", "提交服务记录并查看审核结果。"],
    ] as const,
    helpTitle: "有服务正在进行？",
    help: "如需交接服务或补交报单，请联系本店负责人，提供订单编号、当前进度和待处理事项。",
  },
};

export function StoreEntryStatusPage({ role, entryName, issue, checking, onRetry }: Props) {
  const { services, helpTitle, help, unavailable } = entryContent[role];
  const hostname = window.location.hostname;
  const brandSlug = hostStoreSlug(hostname);
  const storeName = brandSlug ? storeNames[brandSlug] : undefined;
  const returnUrl = brandSlug ? new URL(import.meta.env.BASE_URL, window.location.origin).href : null;
  const showArt = Boolean(brandSlug || issue?.closed);
  const copyStatusId = useId();
  const address = useRef<HTMLInputElement>(null);
  const copyAttempt = useRef(0);
  const [copyResult, setCopyResult] = useState<{ copied: boolean } | null>(null);
  const [dark] = useState(() => {
    try {
      const saved = localStorage.getItem(role === "boss" ? "xa:boss:web:theme" : "xa_fronted_provider_theme");
      if (saved === "dark" || saved === "light") return saved === "dark";
    } catch { /* The entrance also works without browser storage. */ }
    return document.documentElement.classList.contains("dark") || window.matchMedia("(prefers-color-scheme: dark)").matches;
  });

  useEffect(() => {
    if (copyResult && !copyResult.copied) {
      address.current?.focus();
      address.current?.select();
    }
  }, [copyResult]);

  async function copyLink(url: string) {
    const attempt = ++copyAttempt.current;
    let copied = false;
    try {
      await navigator.clipboard.writeText(url);
      copied = true;
    } catch {
      // An unavailable clipboard still allows the visitor to copy the address by hand.
    }
    if (attempt === copyAttempt.current) setCopyResult({ copied });
  }

  function retryStatus() {
    if (checking) return;
    copyAttempt.current += 1;
    setCopyResult(null);
    onRetry();
  }

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
          <div className={`xa-store-closed-hero${showArt ? " xa-store-closed-hero-with-art" : ""}`}>
            <div className="xa-store-closed-copy">
              <h1>{issue?.closed ? "门店暂未开放" : issue ? "暂时无法进入门店" : "正在确认门店…"}</h1>
              {issue && (issue.closed
                ? <>
                  <p className="xa-store-closed-description">{unavailable}</p>
                  <p className="xa-store-closed-schedule"><CalendarClock size={17} aria-hidden="true" />开放时间以门店通知为准</p>
                </>
                : <p className="xa-store-closed-description" role="alert">{issue.message}</p>)}
              {!issue && returnUrl && <p className="xa-store-closed-description">可先保存本店入口，稍后再来查看。</p>}
              <div className="xa-store-closed-actions">
                <button type="button" aria-disabled={checking} onClick={retryStatus}>
                  <RefreshCw size={17} aria-hidden="true" />
                  {checking ? "正在检查…" : issue?.closed ? "重新检查状态" : "重试"}
                </button>
                {returnUrl && <button className="xa-store-closed-copy-link" type="button" onClick={() => copyLink(returnUrl)}>
                  {copyResult?.copied ? <Check size={17} aria-hidden="true" /> : <Copy size={17} aria-hidden="true" />}
                  复制入口链接
                </button>}
              </div>
              <div className="xa-store-closed-feedback">
                <p role="status" aria-atomic="true">
                  {issue && (checking ? "正在查询门店状态…" : <>
                    {issue.closed ? "上次检查" : "上次查询失败"} <time dateTime={issue.checkedAt.toISOString()}>{issue.checkedAt.toLocaleTimeString("zh-CN", { hour12: false })}</time>
                    {issue.closed && <span className="sr-only">，门店暂未开放</span>}
                  </>)}
                </p>
                {returnUrl && <p id={copyStatusId} role="status" aria-atomic="true">
                  {copyResult && (copyResult.copied ? "入口链接已复制" : "入口链接已选中，请手动复制")}
                </p>}
              </div>
              {returnUrl && copyResult && !copyResult.copied && <input
                className="xa-store-closed-address" ref={address} aria-label="门店入口链接"
                aria-describedby={copyStatusId} dir="ltr" type="text" readOnly value={returnUrl}
              />}
            </div>
            {showArt && <div className="xa-store-closed-art">
              <img src={headsetImage} width="900" height="1125" alt="" decoding="async" />
            </div>}
          </div>
          <div className="xa-store-closed-details">
            <section className="xa-store-closed-help">
              <h2><MessageCircle size={20} aria-hidden="true" />{helpTitle}</h2>
              <p>{help}</p>
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
        </main>
      </div>
    </div>
  );
}
