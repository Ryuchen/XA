import { getStoreSlug } from "./store.ts";
import { useEffect, useRef } from "react";
import { createApiClient } from "./client.ts";
import { getSession, subscribeSession, type Role } from "./session.ts";

export interface RealtimeMessage {
  type: string;
  data?: any;
}
type Listener = (message: RealtimeMessage) => void;

function createHub(role: Role) {
  const listeners = new Set<Listener>();
  let socket: WebSocket | null = null;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let heartbeat: ReturnType<typeof setInterval> | undefined;
  let deadline: ReturnType<typeof setTimeout> | undefined;
  let unsubscribe: (() => void) | undefined;
  let generation = 0,
    attempts = 0,
    opened = false,
    credential = "";
  let revision = -1;
  const store = getStoreSlug();
  const emit = (message: RealtimeMessage) =>
    listeners.forEach((listener) => listener(message));
  const release = () => {
    generation++;
    clearInterval(heartbeat);
    clearTimeout(deadline);
    const previous = socket;
    socket = null;
    if (previous) {
      previous.onclose = null;
      previous.onerror = null;
      previous.close();
    }
  };
  const reconnect = () => {
    release();
    if (getStoreSlug() !== store || !listeners.size || !getSession(role).token || document.hidden || retry)
      return;
    retry = setTimeout(
      () => {
        retry = undefined;
        void connect(true);
      },
      Math.min(30000, 1000 * 2 ** Math.min(attempts++, 5)) +
        Math.random() * 250,
    );
  };
  const connect = async (recover = false) => {
    release();
    const current = generation;
    const session = getSession(role);
    credential = session.token;
    revision = session.revision;
    if (getStoreSlug() !== store || !listeners.size || !session.token || document.hidden) return;
    try {
      if (recover)
        await createApiClient(role).get(
          role === "admin" ? "/admin/auth/profile" : "/users/me/",
        );
      if (
        current !== generation ||
        !listeners.size ||
        getSession(role).revision !== revision || getStoreSlug() !== store
      )
        return;
      const ws = new WebSocket(
        `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws/orders/?store=${encodeURIComponent(store)}&token=${encodeURIComponent(getSession(role).token)}`,
      );
      socket = ws;
      deadline = setTimeout(reconnect, 10000);
      ws.onopen = () => {
        if (socket !== ws || generation !== current || getStoreSlug() !== store) return;
        clearTimeout(deadline);
        attempts = 0;
        emit({ type: opened ? "reconnected" : "connected" });
        opened = true;
        const ping = () => {
          if (ws.readyState !== WebSocket.OPEN) {
            reconnect();
            return;
          }
          ws.send('{"type":"ping"}');
          deadline = setTimeout(reconnect, 10000);
        };
        ping();
        heartbeat = setInterval(ping, 25000);
      };
      ws.onmessage = (event) => {
        if (socket !== ws || generation !== current || getStoreSlug() !== store) return;
        try {
          const message = JSON.parse(String(event.data)) as RealtimeMessage;
          if (message.type === "pong") clearTimeout(deadline);
          else emit(message);
        } catch {
          /* Malformed events never substitute for an authoritative REST read. */
        }
      };
      ws.onclose = reconnect;
      ws.onerror = reconnect;
    } catch {
      if (current === generation) reconnect();
    }
  };
  const recover = () => {
    clearTimeout(retry);
    retry = undefined;
    void connect(true);
  };
  const visibility = () => {
    clearTimeout(retry);
    retry = undefined;
    if (document.hidden) release();
    else void connect(true);
  };
  const sessionChanged = () => {
    const session = getSession(role);
    if (session.token === credential && session.revision === revision && getStoreSlug() === store) return;
    clearTimeout(retry);
    retry = undefined;
    attempts = 0;
    if (session.revision !== revision) opened = false;
    void connect();
  };
  return {
    subscribe(listener: Listener) {
      listeners.add(listener);
      if (listeners.size === 1) {
        unsubscribe = subscribeSession(role, sessionChanged);
        document.addEventListener("visibilitychange", visibility);
        window.addEventListener("online", recover);
        void connect();
      }
      return () => {
        listeners.delete(listener);
        if (!listeners.size) {
          unsubscribe?.();
          unsubscribe = undefined;
          clearTimeout(retry);
          retry = undefined;
          release();
          opened = false;
          document.removeEventListener("visibilitychange", visibility);
          window.removeEventListener("online", recover);
        }
      };
    },
  };
}
const hubs = new Map<string, ReturnType<typeof createHub>>();
export function subscribeRealtime(role: Role, callback: Listener) {
  const key = `${getStoreSlug()}:${role}`;
  if (!hubs.has(key)) hubs.set(key, createHub(role));
  return hubs.get(key)!.subscribe(callback);
}
export function useRealtime(role: Role, callback: Listener, enabled = true) {
  const callbackRef = useRef(callback);
  callbackRef.current = callback;
  useEffect(() => {
    if (!enabled) return;
    return subscribeRealtime(role, (message) => callbackRef.current(message));
  }, [role, enabled]);
}
