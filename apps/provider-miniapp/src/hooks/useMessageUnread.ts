import { createContext, useCallback, useEffect, useRef, useState } from 'react';
import Taro, { useDidHide, useDidShow } from '@tarojs/taro';
import { fetchUnreadCount } from '@/services/message';
import { fetchChatUnread } from '@/services/chat';
import { wsService } from '@/services/websocket';
import { getSessionRevision, getStoredToken } from '@/utils/auth';
import { createCoalescedRefresh } from '@/utils/coalescedRefresh';

// Null means that the server count is not currently known.
export const MessageUnreadContext = createContext<number | null>(null);

export function useMessageUnread() {
  const [unread, setUnread] = useState<number | null>(null);
  const sequence = useRef(0);
  const visible = useRef(true);
  const alive = useRef(false);
  const load = useCallback(async () => {
    const seq = ++sequence.current, revision = getSessionRevision();
    if (!getStoredToken()) { if (alive.current) setUnread(null); return; }
    try {
      const [result, chat] = await Promise.all([fetchUnreadCount(), fetchChatUnread()]);
      if (!alive.current || !visible.current || seq !== sequence.current || revision !== getSessionRevision()) return;
      const count = result.data?.unread;
      const chatCount = chat.data?.unread;
      setUnread(result.code === 0 && typeof count === 'number' && Number.isSafeInteger(count) && count >= 0 && chat.code === 0 && typeof chatCount === 'number' && Number.isSafeInteger(chatCount) && chatCount >= 0 ? count + chatCount : null);
    } catch {
      if (alive.current && visible.current && seq === sequence.current && revision === getSessionRevision()) setUnread(null);
    }
  }, []);
  useEffect(() => {
    alive.current = true;
    const refresh = createCoalescedRefresh(async () => { if (visible.current) await load(); });
    const update = () => { if (visible.current) refresh.request(); };
    const onAuth = () => { ++sequence.current; setUnread(null); if (visible.current) void load(); };
    const offMessage = wsService.on('site_message_update', update);
    const offChat = wsService.on('chat_message', update);
    const offConnected = wsService.on('connected', update);
    Taro.eventCenter.on('xa:auth.changed', onAuth);
    Taro.eventCenter.on('xa:messages.changed', update);
    Taro.eventCenter.on('xa:chat.changed', update);
    void load();
    return () => {
      alive.current = false; ++sequence.current; refresh.dispose(); offMessage(); offChat(); offConnected();
      Taro.eventCenter.off('xa:auth.changed', onAuth);
      Taro.eventCenter.off('xa:messages.changed', update);
      Taro.eventCenter.off('xa:chat.changed', update);
    };
  }, [load]);
  useDidShow(() => { visible.current = true; void load(); });
  useDidHide(() => { visible.current = false; ++sequence.current; });
  return unread;
}
