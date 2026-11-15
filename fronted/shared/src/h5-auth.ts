import { useEffect, useRef, useState } from "react";
import { createApiClient } from "./client.ts";
import { getSession, type Role } from "./session.ts";

export type H5LoginFlow = {
  ticket: string;
  poll_token: string;
  authorization_url: string;
  expires_in: number;
};

export function isWechatBrowser() {
  return typeof navigator !== "undefined" && /MicroMessenger/i.test(navigator.userAgent);
}

export function useH5Auth(role: Role, onSuccess: (result: any) => void) {
  const [flow, setFlow] = useState<H5LoginFlow | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const popup = useRef<Window | null>(null);
  const generation = useRef(0);
  const success = useRef(onSuccess);
  success.current = onSuccess;

  const stop = () => {
    generation.current += 1;
    setFlow(null);
    setBusy(false);
    popup.current?.close();
    popup.current = null;
  };

  useEffect(() => {
    const message = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.data?.type !== "xa-wechat-h5") return;
      if (event.data.status === "ERROR") setError("微信授权未完成，请返回原页面重试");
    };
    window.addEventListener("message", message);
    return () => window.removeEventListener("message", message);
  }, []);

  useEffect(() => {
    if (!flow) return;
    let active = true;
    let polling = false;
    const currentGeneration = generation.current;
    const revision = getSession(role).revision;
    const client = createApiClient(role);
    const poll = async () => {
      if (!active || polling) return;
      polling = true;
      try {
        const result = await client.get<any>(
          role === "admin" ? "/admin/auth/h5-login/status" : "/users/h5-login/status/",
          { ticket: flow.ticket, poll_token: flow.poll_token },
          { public: true },
        );
        if (!active || generation.current !== currentGeneration) return;
        if (getSession(role).revision !== revision) {
          setFlow(null);
          setError("登录状态已变化，请重新发起微信授权");
          return;
        }
        if (result.status === "CONFIRMED") {
          popup.current?.close();
          popup.current = null;
          setFlow(null);
          success.current(result);
        } else if (result.status === "EXPIRED") {
          setFlow(null);
          setError("微信授权已过期，请重新发起");
        } else setError("");
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "微信登录状态暂未更新，请稍后重试");
      } finally {
        polling = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [flow, role]);

  async function start(options: { purpose?: "login" | "bind_wechat" | "sync_avatar"; bindToken?: string } = {}) {
    if (busy) return;
    const currentGeneration = ++generation.current;
    setBusy(true);
    setError("");
    try {
      const purpose = options.purpose || "login";
      const client = createApiClient(role);
      const result = await client.post<H5LoginFlow>(
        role === "admin" ? "/admin/auth/h5-login/start" : "/users/h5-login/start/",
        {
          ...(role === "admin" ? {} : { audience: role }),
          purpose,
          bindToken: options.bindToken,
          return_origin: window.location.origin,
        },
        { public: role === "admin" || purpose === "login" },
      );
      if (generation.current !== currentGeneration || !result.authorization_url) {
        throw new Error("微信授权入口返回不完整，请稍后重试");
      }
      setFlow(result);
      popup.current = window.open(result.authorization_url, "xa-wechat-h5", "width=480,height=720,popup=yes");
      if (!popup.current) setError("浏览器拦截了授权窗口，请点击“继续微信授权”");
    } catch (cause) {
      setFlow(null);
      setError(cause instanceof Error ? cause.message : "微信授权暂时无法发起");
    } finally {
      if (generation.current === currentGeneration) setBusy(false);
    }
  }

  return { flow, busy, error, start, stop, setError };
}
