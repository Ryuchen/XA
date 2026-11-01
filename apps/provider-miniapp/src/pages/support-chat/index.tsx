import React, { useEffect, useRef, useState } from 'react';
import { Button, ScrollView, Text, Textarea, View } from '@tarojs/components';
import Taro, { useDidHide, useDidShow } from '@tarojs/taro';
import PageNav from '@/components/PageNav';
import PlatformLayout from '@/components/PlatformLayout';
import PrivateImage from '@/components/PrivateImage';
import { ChatMessage, checkChatMessage, fetchChat, readChatMessages, sendChatImage, sendChatText } from '@/services/chat';
import { wsService } from '@/services/websocket';
import { getSessionRevision, getStoredUser } from '@/utils/auth';
import { previewPrivateImages } from '@/utils/privateMedia';
import styles from './index.module.scss';

type Pending = { id: string; content: string; kind?: 'text' | 'image' };
const merge = (a: ChatMessage[], b: ChatMessage[]) => [...new Map([...a, ...b].map(row => [row.id, row])).values()]
  .sort((x, y) => x.created_at.localeCompare(y.created_at) || x.id - y.id);
const keyFor = () => `xa:provider:chat-pending:${getStoredUser()?.id || ''}`;
const draftKey = () => `xa:provider:chat-draft:${getStoredUser()?.id || ''}`;
const readDraft = () => { try { const value = Taro.getStorageSync(draftKey()); return typeof value === 'string' ? value : ''; } catch { return ''; } };
const saveDraft = (value: string) => { try { if (value) Taro.setStorageSync(draftKey(), value); else Taro.removeStorageSync(draftKey()); } catch { /* Keep the text on the current page. */ } };
const readPending = (): Pending | null => {
  try { const value = Taro.getStorageSync(keyFor()); return value && typeof value.id === 'string' && typeof value.content === 'string' ? value : null; }
  catch { return null; }
};
const SupportChat: React.FC = () => {
  const [items, setItems] = useState<ChatMessage[]>([]), [input, setInput] = useState(readDraft);
  const [pending, setPending] = useState<Pending | null>(() => readPending());
  const [pendingStored, setPendingStored] = useState(true);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [before, setBefore] = useState<number | null>(null), [browsing, setBrowsing] = useState(false);
  const [scrollTo, setScrollTo] = useState('');
  const [scrollTarget, setScrollTarget] = useState('');
  const rows = useRef<ChatMessage[]>([]), seq = useRef(0), visible = useRef(true), locked = useRef(false), operation = useRef(0);
  const browsingRef = useRef(false), session = useRef(getSessionRevision());
  const imagePath = useRef('');
  const scrollTop = useRef<number | null>(null), programmaticScrollUntil = useRef(0);
  const replace = (next: ChatMessage[]) => { rows.current = next; setItems(next); };
  // Taro's H5 scroll watcher can run before newly inserted message nodes exist.
  useEffect(() => {
    if (!scrollTarget) return;
    const timer = setTimeout(() => setScrollTo(scrollTarget), 30);
    return () => clearTimeout(timer);
  }, [items, scrollTarget]);
  const scrollAfterRender = (id: string) => { programmaticScrollUntil.current = Date.now() + 500; setScrollTo(''); setScrollTarget(id); };
  const load = async (older = false) => {
    if (older && !before) return;
    const current = ++seq.current, revision = getSessionRevision(), cursor = older ? before : null;
    setLoading(true); setError('');
    try {
      const previous = rows.current, recentIds = new Set(previous.map(row => row.id));
      let response = await fetchChat(cursor || undefined);
      if (current !== seq.current || revision !== getSessionRevision()) return;
      if (response.code !== 0 || !response.data) throw new Error(response.msg || '聊天记录加载失败');
      const first = response.data, fetched = [...first.messages];
      while (!older && recentIds.size && !fetched.some(row => recentIds.has(row.id)) && response.data?.has_more && response.data.next_before_id) {
        response = await fetchChat(response.data.next_before_id);
        if (current !== seq.current || revision !== getSessionRevision()) return;
        if (response.code !== 0 || !response.data) throw new Error(response.msg || '聊天记录加载失败');
        fetched.push(...response.data.messages);
      }
      const next = merge(previous, fetched); replace(next);
      if (older || !previous.length) setBefore((older ? first : response.data)?.next_before_id || null);
      if (older) { browsingRef.current = true; setBrowsing(true); scrollAfterRender(previous[0] ? `chat-${previous[0].id}` : ''); }
      else if (!browsingRef.current) {
        scrollAfterRender(next.length ? `chat-${next[next.length - 1].id}` : '');
        const ids = next.filter(row => row.is_from_support && !row.is_read).map(row => row.id);
        for (let offset = 0; offset < ids.length && visible.current && revision === getSessionRevision(); offset += 200) {
          const result = await readChatMessages(ids.slice(offset, offset + 200));
          if (result.code !== 0) throw new Error(result.msg || '消息已读状态更新失败');
        }
        if (ids.length && revision === getSessionRevision()) Taro.eventCenter.trigger('xa:chat.changed');
      }
    } catch (cause) { if (current === seq.current && revision === getSessionRevision()) setError(cause instanceof Error ? cause.message : '聊天记录加载失败'); }
    finally { if (current === seq.current && revision === getSessionRevision()) setLoading(false); }
  };
  const refreshRef = useRef(() => {}); refreshRef.current = () => { if (visible.current) void load(); };
  useEffect(() => {
    const update = () => refreshRef.current();
    const offChat = wsService.on('chat_message', update), offConnect = wsService.on('connected', update);
    const changed = () => {
      if (session.current === getSessionRevision()) return;
      session.current = getSessionRevision(); ++seq.current; ++operation.current; locked.current = false; imagePath.current = ''; setBusy(false); replace([]); setInput(readDraft()); setPending(readPending()); setPendingStored(true);
      setBefore(null); setError(''); browsingRef.current = false; setBrowsing(false); setScrollTo(''); setScrollTarget(''); scrollTop.current = null;
      refreshRef.current();
    };
    Taro.eventCenter.on('xa:auth.changed', changed); void load();
    return () => { visible.current = false; ++seq.current; offChat(); offConnect(); Taro.eventCenter.off('xa:auth.changed', changed); };
  }, []);
  useDidHide(() => { visible.current = false; });
  useDidShow(() => { visible.current = true; refreshRef.current(); });
  const finish = async (message: ChatMessage, item: Pending, revision: number) => {
    if (revision !== getSessionRevision() || message.client_message_id !== item.id) return;
    try { if (readPending()?.id === item.id) Taro.removeStorageSync(keyFor()); } catch { /* Message remains visible in history. */ }
    setPending(null); imagePath.current = '';
    if (item.content) { if (readDraft().trim() === item.content) saveDraft(''); setInput(value => value.trim() === item.content ? '' : value); }
    browsingRef.current = false; setBrowsing(false); await load();
  };
  const submit = async (existing?: Pending, retry = false) => {
    if (locked.current) return;
    const content = input.trim();
    const item = existing || (content ? { id: `chat-${Date.now()}-${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`, content } : null);
    if (!item) return;
    const revision = getSessionRevision(), op = ++operation.current; locked.current = true; setBusy(true); setPending(item);
    try { Taro.setStorageSync(keyFor(), item); setPendingStored(true); } catch { setPendingStored(false); }
    try {
      const result = item.kind === 'image' ? await sendChatImage(imagePath.current, item.id, item.content) : await sendChatText(item.content, item.id);
      if (revision !== getSessionRevision()) return;
      if (!retry && result.code === 400) {
        try { if (readPending()?.id === item.id) Taro.removeStorageSync(keyFor()); } catch { /* Current page can still retain the draft. */ }
        setPending(null); imagePath.current = '';
        Taro.showToast({ title: result.msg || '发送失败，请检查内容', icon: 'none' });
        return;
      }
      if (result.code !== 0 || !result.data) throw new Error(result.msg || '发送结果未确认');
      await finish(result.data, item, revision);
    } catch { if (revision === getSessionRevision()) Taro.showToast({ title: '发送结果未确认，请先核对', icon: 'none' }); }
    finally { if (op === operation.current) { locked.current = false; if (revision === getSessionRevision()) setBusy(false); } }
  };
  const chooseImage = async () => {
    if (locked.current || pending || busy) return;
    const revision = getSessionRevision();
    try {
      const result = await Taro.chooseImage({ count: 1, sizeType: ['compressed'], sourceType: ['album', 'camera'] });
      if (revision !== getSessionRevision() || !result.tempFilePaths[0]) return;
      const file = result.tempFiles?.[0];
      if (file && file.size > 5 * 1024 * 1024) { Taro.showToast({ title: '图片大小不能超过 5MB', icon: 'none' }); return; }
      imagePath.current = result.tempFilePaths[0];
      await submit({ id: `chat-${Date.now()}-${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`, kind: 'image', content: input.trim() });
    } catch { /* User cancelled the picker. */ }
  };
  const check = async () => {
    if (!pending || locked.current) return;
    const revision = getSessionRevision(), item = pending, op = ++operation.current; locked.current = true; setBusy(true);
    try {
      const result = await checkChatMessage(item.id);
      if (revision !== getSessionRevision()) return;
      if (result.code === 0 && result.data?.status === 'FOUND' && result.data.message) await finish(result.data.message, item, revision);
      else Taro.showToast({ title: item.kind === 'image' && !imagePath.current ? '暂未查到，请稍后再核对' : '暂未查到，可重试原消息', icon: 'none' });
    } catch { if (revision === getSessionRevision()) Taro.showToast({ title: '暂时无法核对，请稍后重试', icon: 'none' }); }
    finally { if (op === operation.current) { locked.current = false; if (revision === getSessionRevision()) setBusy(false); } }
  };
  const latest = () => { browsingRef.current = false; setBrowsing(false); void load(); };
  const abandon = async () => {
    if (!pending || busy) return;
    const item = pending, revision = getSessionRevision();
    const answer = await Taro.showModal({ title: '放弃核对？', content: '消息可能已经送达，请先查看聊天记录。', confirmText: '放弃核对' });
    if (!answer.confirm || revision !== getSessionRevision()) return;
    try { if (readPending()?.id === item.id) Taro.removeStorageSync(keyFor()); } catch { /* The current page still keeps the text. */ }
    if (item.kind !== 'image') setInput(value => { const restored = value || item.content; saveDraft(restored); return restored; });
    imagePath.current = ''; setPending(null);
  };
  return <PlatformLayout active='messages'><View className={`xa-provider-page ${styles.page}`}>
    <PageNav title='平台客服' fallbackUrl='/pages/messages/index' />
    <View className={styles.card}><Text className={styles.heading}>站内咨询</Text><Text className={styles.hint}>客服回复会显示在这里</Text>
      <ScrollView className={styles.history} scrollY scrollIntoView={scrollTo} onScroll={event => {
        const top = Number(event.detail.scrollTop);
        if (Number.isFinite(top) && scrollTop.current !== null && top < scrollTop.current - 3 && Date.now() > programmaticScrollUntil.current && !browsingRef.current) {
          browsingRef.current = true; setBrowsing(true); setScrollTo(''); setScrollTarget('');
        }
        if (Number.isFinite(top)) scrollTop.current = top;
      }}>
        {!!before && <Button className={styles.secondary} disabled={loading} onClick={() => load(true)}>加载更早消息</Button>}
        {error && <View className={styles.error}><Text>{error}</Text><Button className={styles.secondary} onClick={() => load()}>重试</Button></View>}
        {loading && !items.length && <Text className={styles.hint}>正在加载…</Text>}
        {!loading && !error && !items.length && <Text className={styles.hint}>写下你想咨询的问题</Text>}
        {items.map(item => <View id={`chat-${item.id}`} key={item.id} className={`${styles.message} ${item.is_from_support ? styles.support : styles.own}`}>
          <Text className={styles.meta}>{item.is_from_support ? '客服' : '我'} · {new Date(item.created_at).toLocaleString()}</Text>
          <View className={styles.bubble}>{item.image_url && <PrivateImage className={styles.image} src={item.image_url} mode='widthFix' onClick={() => previewPrivateImages(item.image_url, [item.image_url])} />}{!!item.content && <Text selectable>{item.content}</Text>}</View>
        </View>)}
      </ScrollView>
      {browsing && <Button className={styles.secondary} onClick={latest}>回到最新消息</Button>}
      {pending && <View className={styles.pending}><Text>上条消息结果未确认，请先核对。</Text>{!pendingStored && <Text>设备未能保存核对编号，请保持此页面。</Text>}{pending.kind === 'image' && !imagePath.current && <Text>原图片已不可用，请先核对发送结果。</Text>}<Text className={styles.pendingContent}>{pending.kind === 'image' ? `图片消息${pending.content ? '：' + pending.content : ''}` : pending.content}</Text><View className={styles.actions}><Button className={styles.secondary} disabled={busy} onClick={check}>核对结果</Button>{(pending.kind !== 'image' || !!imagePath.current) && <Button className={styles.secondary} disabled={busy} onClick={() => submit(pending, true)}>重试原消息</Button>}<Button className={styles.secondary} disabled={busy} onClick={abandon}>放弃核对</Button></View></View>}
      <Textarea className={styles.input} value={input} maxlength={1000} disabled={!!pending || busy} placeholder='输入咨询内容，也可作为图片说明' onInput={event => { setInput(event.detail.value); saveDraft(event.detail.value); }} />
      <View className={styles.actions}><Button className={styles.secondary} disabled={busy || !!pending} onClick={chooseImage}>发送图片</Button><Button className={styles.send} disabled={busy || !!pending || !input.trim()} loading={busy} onClick={() => submit()}>发送</Button></View>
    </View>
  </View></PlatformLayout>;
};
export default SupportChat;
