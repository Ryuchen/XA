import React, { useEffect, useRef, useState } from 'react';
import { Button, Text, View } from '@tarojs/components';
import Taro, { useDidHide, useDidShow } from '@tarojs/taro';
import PageNav from '@/components/PageNav';
import PlatformLayout from '@/components/PlatformLayout';
import { applicationAction, fetchMyApplication, fetchMyApplications, MyApplicationDetail } from '@/services/order';
import { orderServiceName } from '@/types/order';
import { getSessionRevision, getStoredToken } from '@/utils/auth';
import { clearOrderFocus, queueOrderFocus, queueOrderPool } from '@/utils/orderNavigation';
import { wsService } from '@/services/websocket';
import styles from './index.module.scss';

const reservationDate = (value: string) => {
  const time = new Date(value);
  if (Number.isNaN(time.getTime())) return '—';
  const local = new Date(time.getTime() + 8 * 60 * 60 * 1000);
  const pad = (part: number) => String(part).padStart(2, '0');
  return `${local.getUTCFullYear()}-${pad(local.getUTCMonth() + 1)}-${pad(local.getUTCDate())} ${pad(local.getUTCHours())}:${pad(local.getUTCMinutes())}`;
};

const ApplicationDetailPage: React.FC = () => {
  const [detail, setDetail] = useState<MyApplicationDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState('');
  const [applications, setApplications] = useState<MyApplicationDetail['application'][] | null>(null);
  const sequence = useRef(0);
  const locked = useRef(false);
  const visible = useRef(true);
  const sessionRef = useRef(getSessionRevision());
  const params = Taro.getCurrentInstance().router?.params || {};
  const orderId = Number(params.orderId);
  const [applicationId, setApplicationId] = useState(() => Number(params.applicationId) || 0);
  const valid = Number.isSafeInteger(orderId) && orderId > 0 && Number.isSafeInteger(applicationId) && applicationId >= 0;
  const load = async () => {
    const current = ++sequence.current, session = getSessionRevision();
    if (!valid) { setLoading(false); setError('报名链接无效，请返回消息中心'); return; }
    setLoading(true); setError('');
    try {
      if (!applicationId) {
        const response = await fetchMyApplications(orderId);
        if (current !== sequence.current || session !== getSessionRevision()) return;
        if (response.code !== 0 || !response.data) throw new Error(response.msg || '报名暂时无法查看');
        setApplications(response.data.applications || []); setDetail(null);
        return;
      }
      const response = await fetchMyApplication(orderId, applicationId);
      if (current !== sequence.current || session !== getSessionRevision()) return;
      if (response.code !== 0 || !response.data) throw new Error(response.msg || '报名暂时无法查看');
      setApplications(null); setDetail(response.data);
    } catch (cause) {
      if (current === sequence.current && session === getSessionRevision()) { setDetail(null); setError(cause instanceof Error ? cause.message : '报名暂时无法查看'); }
    } finally { if (current === sequence.current && session === getSessionRevision()) setLoading(false); }
  };
  useEffect(() => {
    const update = (data: unknown) => {
      if (visible.current && data && typeof data === 'object' && Number((data as { id?: unknown }).id) === orderId) void load();
    };
    const offOrder = wsService.on('order_status_update', update);
    // Re-read after reconnect because the socket does not replay missed events.
    const offConnected = wsService.on('connected', () => { if (visible.current) void load(); });
    const onAuthChanged = () => {
      if (sessionRef.current === getSessionRevision()) return;
      sessionRef.current = getSessionRevision();
      ++sequence.current;
      setDetail(null); setApplications(null); setResult(''); setError('');
      if (visible.current && getStoredToken()) void load();
      else { setLoading(false); setError('登录已变化，请重新查看报名'); }
    };
    Taro.eventCenter.on('xa:auth.changed', onAuthChanged);
    void load();
    return () => { sequence.current++; offOrder(); offConnected(); Taro.eventCenter.off('xa:auth.changed', onAuthChanged); };
  }, [orderId, applicationId]);
  useDidHide(() => { visible.current = false; });
  useDidShow(() => { visible.current = true; void load(); });
  const openPool = async () => {
    queueOrderPool();
    try { await Taro.switchTab({ url: '/pages/orders/index' }); }
    catch { clearOrderFocus(); Taro.showToast({ title: '抢单池暂时无法打开', icon: 'none' }); }
  };
  const openOrder = async () => {
    queueOrderFocus(orderId);
    try { await Taro.switchTab({ url: '/pages/orders/index' }); }
    catch { clearOrderFocus(); Taro.showToast({ title: '订单页面暂时无法打开', icon: 'none' }); }
  };
  const act = async (action: 'CONFIRM_TEAM' | 'WITHDRAW') => {
    if (!detail || locked.current) return;
    const declining = action === 'WITHDRAW' && detail.application.is_partner && !detail.application.partner_confirmed;
    const session = getSessionRevision();
    locked.current = true; setBusy(true);
    try {
      if (action === 'WITHDRAW') {
        const answer = await Taro.showModal({ title: declining ? '拒绝邀请' : '撤回报名', content: declining ? '拒绝后，这次组队报名将取消。' : '撤回后，这次组队报名将取消。', confirmText: declining ? '确认拒绝' : '确认撤回' });
        if (!answer.confirm || session !== getSessionRevision()) return;
      }
      const response = await applicationAction(orderId, applicationId, action);
      if (session !== getSessionRevision()) return;
      if (response.code !== 0) {
        Taro.showToast({ title: response.msg || '操作失败，请重试', icon: 'none' });
        await load();
        return;
      }
      if (action === 'WITHDRAW') { setDetail(null); setResult(declining ? '已拒绝邀请' : '报名已撤回'); }
      else { Taro.showToast({ title: '组队已确认', icon: 'success' }); await load(); }
    } catch {
      if (session === getSessionRevision()) { Taro.showToast({ title: action === 'CONFIRM_TEAM' ? '组队结果未确认，请核对报名状态' : declining ? '拒绝结果未确认，请核对报名状态' : '撤回结果未确认，请核对报名状态', icon: 'none' }); await load(); }
    } finally { locked.current = false; setBusy(false); }
  };
  return <PlatformLayout active='orders'><View className={`xa-provider-page ${styles.page}`}><PageNav title='报名详情' fallbackUrl='/pages/orders/index' />
    {loading ? <View className={styles.card}><Text>正在加载报名…</Text></View> : result ? <View className={styles.card}><Text>{result}</Text><Button className={styles.secondary} onClick={openPool}>回到抢单池</Button>{!params.applicationId && <Button className={styles.secondary} onClick={() => { setResult(''); setApplicationId(0); }}>查看其他报名</Button>}</View> : error ? <View className={styles.card}><Text>{error}</Text><Button className={styles.secondary} onClick={load}>重新查看</Button></View> : applications ? <View className={styles.card}>
      <Text className={styles.heading}>当前待处理报名</Text><Text className={styles.muted}>这条旧消息没有指定邀请，请选择当前报名查看。</Text>
      {applications.length ? applications.map(item => <Button className={styles.applicationChoice} key={item.id} onClick={() => setApplicationId(item.id)}>{item.members.map(member => member.nickname).join(' + ')} · {item.ready ? '已确认' : '待确认'}</Button>) : <Text className={styles.reason}>当前没有待处理的报名。</Text>}
    </View> : detail && <View className={styles.card}>
      <Text className={styles.heading}>{orderServiceName(detail.order)}</Text><Text className={styles.muted}>{detail.order.order_no}</Text>
      {detail.order.reservation_origin && <View className={styles.section}><Text className={styles.label}>原预约时段（北京时间）</Text><Text>{reservationDate(detail.order.reservation_origin.start_time)} 至 {reservationDate(detail.order.reservation_origin.end_time)}</Text><Text className={styles.muted}>请与老板确认服务安排。</Text></View>}
      <View className={styles.section}><Text className={styles.label}>组队成员</Text>{detail.application.members.map(member => <Text className={styles.member} key={member.id}>{member.nickname} · {member.level}</Text>)}</View>
      <View className={styles.section}><Text className={styles.label}>报名状态</Text><Text>{detail.closed_reason || (detail.application.ready ? '队伍已确认，等待老板选择' : '等待队友确认')}</Text>{!!detail.confirm_reason && <Text className={styles.reason}>{detail.confirm_reason}</Text>}</View>
      {detail.application.status === 'WITHDRAWN' && <Button className={styles.secondary} onClick={openPool}>回到抢单池</Button>}
      {detail.application.status === 'SELECTED' && <Button className={styles.primary} onClick={openOrder}>查看订单</Button>}
      {!detail.closed_reason && <View className={styles.actions}>{detail.application.is_partner && !detail.application.partner_confirmed && <Button className={styles.primary} disabled={!detail.can_confirm || busy} onClick={() => act('CONFIRM_TEAM')}>确认组队</Button>}<Button className={styles.secondary} disabled={busy} onClick={() => act('WITHDRAW')}>{detail.application.is_partner && !detail.application.partner_confirmed ? '拒绝邀请' : '撤回报名'}</Button></View>}
    </View>}
  </View></PlatformLayout>;
};
export default ApplicationDetailPage;
