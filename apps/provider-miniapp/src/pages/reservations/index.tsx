import React, { useEffect, useRef, useState } from 'react';
import { Button, Text, Textarea, View } from '@tarojs/components';
import Taro, { useDidHide, useDidShow, usePullDownRefresh } from '@tarojs/taro';
import PageNav from '@/components/PageNav';
import PlatformLayout from '@/components/PlatformLayout';
import SurfaceDialog from '@/components/SurfaceDialog';
import { fetchReservation, fetchReservations, Reservation, ReservationCursor, reservationAction } from '@/services/reservation';
import { getSessionRevision } from '@/utils/auth';
import { clearOrderFocus, queueOrderFocus } from '@/utils/orderNavigation';
import { wsService } from '@/services/websocket';
import styles from './index.module.scss';

const statuses: Record<string, string> = { PENDING: '待确认', CONFIRMED: '已确认', REJECTED: '已拒绝', CANCELLED: '已取消', CONVERTED: '已转订单', EXPIRED: '已过期' };
// The boss chooses a Beijing-time slot; keep the same clock on every device.
const date = (value: string) => {
  const instant = value ? new Date(value) : null;
  if (!instant || Number.isNaN(instant.getTime())) return '—';
  const local = new Date(instant.getTime() + 8 * 60 * 60 * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${local.getUTCFullYear()}-${pad(local.getUTCMonth() + 1)}-${pad(local.getUTCDate())} ${pad(local.getUTCHours())}:${pad(local.getUTCMinutes())}`;
};
const ReservationPage: React.FC = () => {
  const params = Taro.getCurrentInstance().router?.params || {};
  const initial = Number(params.id) || 0;
  const [selected, setSelected] = useState(initial);
  const [items, setItems] = useState<Reservation[]>([]);
  const [detail, setDetail] = useState<Reservation | null>(null);
  const [nextCursor, setNextCursor] = useState<ReservationCursor | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [dialog, setDialog] = useState<'reject' | 'cancel' | null>(null);
  const [reason, setReason] = useState('');
  const seq = useRef(0), lock = useRef(false);
  const itemsRef = useRef<Reservation[]>([]);
  const replaceItems = (rows: Reservation[]) => { itemsRef.current = rows; setItems(rows); };
  const visible = useRef(true), pendingRefresh = useRef(false);
  const dialogRef = useRef(dialog); dialogRef.current = dialog;
  const refreshRef = useRef<() => void>(() => {});
  const load = async (cursor: ReservationCursor | null = null, append = false) => {
    const current = ++seq.current, session = getSessionRevision();
    setLoading(true); setError('');
    try {
      if (selected) {
        const response = await fetchReservation(selected);
        if (current !== seq.current || session !== getSessionRevision()) return;
        if (response.code !== 0 || !response.data) throw new Error(response.msg || '预约暂时无法查看');
        setDetail(response.data);
        // Another participant may change the reservation while an action
        // dialog is open. Keep typed input on transient failures, but dismiss
        // an action the refreshed server state no longer permits.
        if (dialogRef.current && !response.data.allowed_actions.includes(dialogRef.current)) {
          dialogRef.current = null; setDialog(null); setReason('');
        }
      } else {
        const lastVisible = !append ? itemsRef.current[itemsRef.current.length - 1]?.id : undefined;
        const rows = append ? [...itemsRef.current] : [];
        const seen = new Set(rows.map(row => row.id));
        let next = cursor;
        do {
          const response = await fetchReservations(next);
          if (current !== seq.current || session !== getSessionRevision()) return;
          if (response.code !== 0 || !response.data) throw new Error(response.msg || '预约暂时无法加载');
          for (const row of response.data.items) if (!seen.has(row.id)) { seen.add(row.id); rows.push(row); }
          next = response.data.next_cursor;
        } while (!append && lastVisible && !seen.has(lastVisible) && next);
        replaceItems(rows); setNextCursor(next);
      }
    } catch (cause) {
      if (current === seq.current && session === getSessionRevision()) {
        if (selected) setDetail(null);
        const message = cause instanceof Error ? cause.message : '';
        setError(/^(failed to fetch|networkerror|load failed|request:fail)/i.test(message)
          ? '网络连接失败，请检查网络后重试' : message || '预约暂时无法加载');
      }
    } finally { if (current === seq.current && session === getSessionRevision()) setLoading(false); }
  };
  refreshRef.current = () => {
    if (!visible.current) return;
    if (lock.current || dialogRef.current) { pendingRefresh.current = true; return; }
    pendingRefresh.current = false; void load();
  };
  useEffect(() => { setDetail(null); load(); return () => { seq.current++; }; }, [selected]);
  useDidHide(() => { visible.current = false; });
  useDidShow(() => { visible.current = true; refreshRef.current(); });
  usePullDownRefresh(() => { refreshRef.current(); Taro.stopPullDownRefresh(); });
  useEffect(() => {
    const update = () => refreshRef.current();
    const offReservation = wsService.on('reservation_status_update', update);
    const offConnected = wsService.on('connected', update);
    return () => { visible.current = false; offReservation(); offConnected(); };
  }, []);
  useEffect(() => { if (!busy && !dialog && pendingRefresh.current) refreshRef.current(); }, [busy, dialog]);
  useEffect(() => {
    let revision = getSessionRevision();
    const changed = () => { if (revision === getSessionRevision()) return; revision = getSessionRevision(); seq.current++; pendingRefresh.current = false; replaceItems([]); setNextCursor(null); setDetail(null); setDialog(null); setError('登录状态已变化，请重新查看'); };
    Taro.eventCenter.on('xa:auth.changed', changed);
    return () => { Taro.eventCenter.off('xa:auth.changed', changed); };
  }, []);
  const act = async (action: 'confirm' | 'reject' | 'cancel') => {
    if (!detail || lock.current || !detail.allowed_actions.includes(action)) return;
    const session = getSessionRevision();
    lock.current = true; seq.current++; setLoading(false); setBusy(true);
    try {
      if (action === 'confirm') {
        const result = await Taro.showModal({ title:'确认预约', content:'确认后请预留这段档期，转为订单后开始服务。', confirmText:'确认预约' });
        if (!result.confirm || session !== getSessionRevision()) return;
      }
      const response = await reservationAction(detail.id, action, action === 'confirm' ? '' : reason.trim());
      if (session !== getSessionRevision()) return;
      if (response.code !== 0 || !response.data) throw new Error(response.msg || '操作失败，请刷新后重试');
      // A page-show refresh started before this action must not restore the
      // earlier status after the server has confirmed the transition.
      seq.current++; setLoading(false); setDetail(response.data); setDialog(null); setReason('');
      Taro.showToast({ title: response.msg || '预约已更新', icon:'none' });
    } catch (cause) {
      if (session === getSessionRevision()) {
        const message = cause instanceof Error ? cause.message : '';
        Taro.showToast({ title: /^request:fail/i.test(message) ? '预约结果未确认，请核对状态' : message || '操作失败，请重试', icon:'none' });
        await load();
      }
    } finally { lock.current = false; setBusy(false); }
  };
  const openOrder = async () => {
    if (!detail?.order) return;
    queueOrderFocus(detail.order);
    try { await Taro.switchTab({ url:'/pages/orders/index' }); }
    catch { clearOrderFocus(); Taro.showToast({ title:'订单暂时无法打开', icon:'none' }); }
  };
  return <PlatformLayout active='orders'><View className={`xa-provider-page ${styles.page}`}><PageNav title={selected ? '预约详情' : '我的预约'} fallbackUrl='/pages/orders/index' />
    <View className={styles.body}>
      {!!selected && <Button className={styles.secondary} disabled={busy} onClick={() => { setSelected(0); setDetail(null); }}>查看全部预约</Button>}
      {!selected && <View className={styles.intro}><Text className={styles.title}>我的预约</Text><Text className={styles.muted}>确认档期后，转为订单才开始服务。</Text></View>}
      {!!error && <View className={styles.card}><Text className={styles.reason}>{error}</Text><Button className={styles.secondary} onClick={() => load()}>重新加载</Button></View>}
      {loading && <View className={styles.card}><Text>正在加载预约…</Text></View>}
      {!loading && !error && !selected && !items.length && <View className={styles.card}><Text>暂无预约</Text></View>}
      {!selected && items.map(item => <View className={styles.card} key={item.id}><View className={styles.top}><Text className={styles.title}>{item.service?.name || '陪玩服务'}</Text><Text className={styles.status}>{statuses[item.status] || item.status}</Text></View><Text className={styles.muted}>{date(item.start_time)} 至 {date(item.end_time)}（北京时间）</Text><Text className={styles.muted}>老板 {item.customer?.name || '—'}</Text><Button className={styles.secondary} onClick={() => setSelected(item.id)}>查看预约</Button></View>)}
      {!selected && !loading && !!nextCursor && <Button className={styles.secondary} onClick={() => load(nextCursor, true)}>加载更多</Button>}
      {!!selected && detail && <View className={styles.card}><View className={styles.top}><Text className={styles.title}>{detail.service?.name || '陪玩服务'}</Text><Text className={styles.status}>{statuses[detail.status] || detail.status}</Text></View><Text className={styles.muted}>{detail.reservation_no}</Text>
        <View className={styles.section}><Text className={styles.label}>预约时间（北京时间）</Text><Text className={styles.value}>{date(detail.start_time)} 至 {date(detail.end_time)}</Text><Text className={styles.muted}>共 {detail.duration_minutes} 分钟</Text></View>
        <View className={styles.section}><Text className={styles.label}>老板与游戏</Text><Text className={styles.value}>{detail.customer?.name || '—'} · 数量 {detail.game_rounds}</Text>{!!detail.game_region && <Text className={styles.value}>大区：{detail.game_region}</Text>}{!!detail.game_nickname && <Text className={styles.value}>游戏昵称：{detail.game_nickname}</Text>}{!!detail.game_uid && <Text className={styles.value}>游戏 UID：{detail.game_uid}</Text>}{!!detail.remark && <Text className={styles.value}>备注：{detail.remark}</Text>}</View>
        {!!(detail.reject_reason || detail.cancel_reason) && <View className={styles.section}><Text className={styles.label}>处理说明</Text><Text className={styles.reason}>{detail.reject_reason || detail.cancel_reason}</Text></View>}
        {detail.status === 'CONFIRMED' && <Text className={styles.muted}>档期已确认，等待转为订单。</Text>}
        {detail.status === 'EXPIRED' && <Text className={styles.muted}>预约时段已结束，请与老板重新预约。</Text>}
        {detail.status === 'CONVERTED' && <View className={styles.section}><Text className={styles.value}>订单 {detail.order_no || '已创建'}</Text>{!!detail.order && <Button className={styles.secondary} onClick={openOrder}>查看订单</Button>}</View>}
        <View className={styles.actions}>{detail.allowed_actions.includes('confirm') && <Button className={styles.primary} disabled={busy} onClick={() => act('confirm')}>确认预约</Button>}{detail.allowed_actions.includes('reject') && <Button className={styles.secondary} disabled={busy} onClick={() => { seq.current++; setLoading(false); setReason(''); dialogRef.current = 'reject'; setDialog('reject'); }}>拒绝预约</Button>}{detail.allowed_actions.includes('cancel') && <Button className={styles.secondary} disabled={busy} onClick={() => { seq.current++; setLoading(false); setReason(''); dialogRef.current = 'cancel'; setDialog('cancel'); }}>取消预约</Button>}</View>
      </View>}
    </View>
    <SurfaceDialog open={!!dialog} title={dialog === 'reject' ? '拒绝预约' : '取消预约'} busy={busy} onClose={() => setDialog(null)} footer={<Button className={styles.primary} disabled={busy} onClick={() => dialog && act(dialog)}>{busy ? '处理中…' : '确认'}</Button>}><Text className={styles.muted}>可填写原因，方便老板了解。</Text><Textarea className={styles.input} value={reason} maxlength={255} placeholder='原因（选填）' onInput={event => setReason(event.detail.value)} /></SurfaceDialog>
  </View></PlatformLayout>;
};
export default ReservationPage;
