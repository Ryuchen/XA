import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Text, View } from '@tarojs/components';
import Taro, { useDidHide, useDidShow, usePullDownRefresh } from '@tarojs/taro';
import { fetchMessages, markAllMessagesRead, MessageType, SiteMessage } from '@/services/message';
import PlatformLayout from '@/components/PlatformLayout';
import PageHeading from '@/components/PageHeading';
import styles from './index.module.scss';
import { Empty } from '@/components';
import { getSessionRevision, getStoredToken } from '@/utils/auth';
import { createCoalescedRefresh } from '@/utils/coalescedRefresh';
import { wsService } from '@/services/websocket';

const TYPE_META: Record<MessageType, { label: string }> = {
  SYSTEM: { label: '系统通知' },
  ORDER: { label: '订单消息' },
  SUPPORT: { label: '客服消息' },
  PROMOTION: { label: '活动通知' },
};
const timeText = (value: string) => {
  const date = new Date(value); const diff = Date.now() - date.getTime();
  if (diff < 60000) return '刚刚';
  if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
  return `${date.getMonth() + 1}/${date.getDate()}`;
};

const MessagesPage: React.FC = () => {
  const [items, setItems] = useState<SiteMessage[]>([]);
  const [unread, setUnread] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const [moreError, setMoreError] = useState('');
  const [loadingMore, setLoadingMore] = useState(false);
  const [nextBefore, setNextBefore] = useState<number | null>(null);
  const sequence = useRef(0);
  const moreLock = useRef(false);
  const refreshing = useRef(false);
  const visible = useRef(true);
  const sessionRef = useRef(getSessionRevision());
  const itemsRef = useRef<SiteMessage[]>([]);
  const replaceItems = (rows: SiteMessage[]) => { itemsRef.current = rows; setItems(rows); };
  const load = useCallback(async () => {
    const current = ++sequence.current, session = getSessionRevision();
    if (sessionRef.current !== session) {
      sessionRef.current = session; replaceItems([]); setUnread(0); setNextBefore(null); setLoaded(false);
    }
    const oldestVisible = itemsRef.current.length ? itemsRef.current[itemsRef.current.length - 1].id : null;
    moreLock.current = false;
    refreshing.current = true;
    setLoadingMore(false); setMoreError('');
    try {
      const rows: SiteMessage[] = [];
      let before: number | undefined;
      let next: number | null = null;
      let unreadCount = 0;
      do {
        const res = await fetchMessages(1, 50, before);
        if (current !== sequence.current || session !== getSessionRevision()) return;
        if (res.code !== 0 || !res.data) throw new Error(res.msg || '消息加载失败');
        rows.push(...(res.data.messages || []));
        unreadCount = res.data.unread || 0;
        next = res.data.next_before || null;
        before = next || undefined;
      } while (oldestVisible && !rows.some(item => item.id === oldestVisible) && next);
      replaceItems(rows); setUnread(unreadCount); setNextBefore(next); setError('');
    } catch (cause) {
      if (current === sequence.current && session === getSessionRevision()) setError(cause instanceof Error ? cause.message : '消息加载失败');
    } finally { if (current === sequence.current && session === getSessionRevision()) { refreshing.current = false; setLoaded(true); } }
  }, []);
  useEffect(() => {
    visible.current = true;
    const refresh = createCoalescedRefresh(async () => { if (visible.current && getStoredToken()) await load(); });
    const update = () => { if (visible.current && getStoredToken()) refresh.request(); };
    const offMessage = wsService.on('site_message_update', update);
    // The socket does not replay changes missed while offline.
    const offConnected = wsService.on('connected', update);
    const onAuthChanged = () => {
      if (sessionRef.current === getSessionRevision()) return;
      sessionRef.current = getSessionRevision();
      ++sequence.current;
      replaceItems([]); setUnread(0); setNextBefore(null); setLoaded(false);
      setError(''); setMoreError(''); setLoadingMore(false);
      moreLock.current = false; refreshing.current = false;
      if (visible.current && getStoredToken()) void load();
    };
    Taro.eventCenter.on('xa:auth.changed', onAuthChanged);
    void load();
    return () => { sequence.current += 1; visible.current = false; offMessage(); offConnected(); refresh.dispose(); Taro.eventCenter.off('xa:auth.changed', onAuthChanged); };
  }, [load]);
  useDidHide(() => { visible.current = false; });
  useDidShow(() => { visible.current = true; void load(); });
  usePullDownRefresh(async () => { await load(); Taro.stopPullDownRefresh(); });
  const loadMore = async () => {
    if (!nextBefore || moreLock.current || refreshing.current) return;
    const current = sequence.current, session = getSessionRevision();
    moreLock.current = true; setLoadingMore(true); setMoreError('');
    try {
      const res = await fetchMessages(1, 50, nextBefore);
      if (current !== sequence.current || session !== getSessionRevision()) return;
      if (res.code !== 0 || !res.data) throw new Error(res.msg || '加载更早消息失败');
      const incoming = res.data.messages || [];
      const ids = new Set(itemsRef.current.map(item => item.id));
      replaceItems(itemsRef.current.concat(incoming.filter(item => !ids.has(item.id))));
      setUnread(res.data.unread || 0); setNextBefore(res.data.next_before || null);
    } catch (cause) {
      if (current === sequence.current && session === getSessionRevision()) setMoreError(cause instanceof Error ? cause.message : '加载更早消息失败');
    } finally {
      if (current === sequence.current && session === getSessionRevision()) { moreLock.current = false; setLoadingMore(false); }
    }
  };
  const readAll = async () => {
    const session = getSessionRevision();
    try {
      const res = await markAllMessagesRead();
      if (session !== getSessionRevision()) return;
      if (res.code !== 0) throw new Error(res.msg || '标记失败，请重试');
      Taro.eventCenter.trigger('xa:messages.changed');
      await load();
      if (session === getSessionRevision()) Taro.showToast({ title: '已全部标记为已读', icon: 'success' });
    } catch (cause) { if (session === getSessionRevision()) Taro.showToast({ title: cause instanceof Error ? cause.message : '标记失败，请重试', icon: 'none' }); }
  };
  return (
    <PlatformLayout active='messages'><View className={`xa-provider-page ${styles.page}`}>
      <PageHeading title='消息中心' hint={!loaded ? '正在读取消息' : error ? '消息暂时无法更新' : unread ? `${unread} 条消息等待查看` : '所有消息均已阅读'} action={unread > 0 && <Button className={styles.readAll} onClick={readAll}>全部已读</Button>} />
      {!!error && !!items.length && <View className={styles.emptyCard}><Empty title='消息暂未更新' desc={error}><Button onClick={load}>重新加载</Button></Empty></View>}
      <View className={styles.messageList}>
      {!loaded ? <Text className={styles.empty}>加载中…</Text> : error && !items.length ? <View className={styles.emptyCard}><Empty title='消息暂未加载' desc={error}><Button onClick={load}>重新加载</Button></Empty></View> : items.length === 0 ? <View className={styles.emptyCard}><Empty title='暂无消息' desc='平台通知和订单动态会出现在这里' /></View> : items.map(item => {
        const meta = TYPE_META[item.type] || TYPE_META.SYSTEM;
        return <View className={styles.item} key={item.id} onClick={() => Taro.navigateTo({ url: `/pages/message-detail/index?id=${item.id}` })}>
          <View className={styles.icon}><Text>{meta.label.slice(0, 2)}</Text></View>
          <View className={styles.content}><View className={styles.itemHead}><Text className={styles.itemTitle}>{item.title}</Text><Text className={styles.time}>{timeText(item.created_at)}</Text></View><Text className={styles.type}>{meta.label}</Text><Text className={styles.preview}>{item.preview || item.detail || '点击查看详情'}</Text></View>
          {!item.is_read && <View className={styles.dot} />}
        </View>;
      })}
      </View>
      {moreError && <Text className={styles.moreError}>{moreError}</Text>}
      {loaded && !error && !!nextBefore && <Button className={styles.loadMore} disabled={loadingMore} onClick={loadMore}>{loadingMore ? '加载中…' : moreError ? '重试加载' : '加载更早消息'}</Button>}
    </View></PlatformLayout>
  );
};
export default MessagesPage;
