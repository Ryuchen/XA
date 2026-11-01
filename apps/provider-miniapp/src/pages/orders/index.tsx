import { fetchEscortMe,ProviderQualification } from '@/services/user';
import { getSessionRevision } from '@/utils/auth';
import { takeOrderFocus } from '@/utils/orderNavigation';
import { clearReportOrder, queueReportOrder } from '@/utils/reportNavigation';
import { networkSnapshot, cooldownRemaining, pendingNetworkReport, acknowledgeNetworkReport } from '@/utils/orderNetwork';
import { request, ApiResponse } from '@/utils/request';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Button, Picker, ScrollView } from '@tarojs/components';
import Taro, { useDidShow, useDidHide, usePullDownRefresh } from '@tarojs/taro';
import {
  fetchOrders, fetchOrderDetail,
  grabOrder, fetchTeammates, applicationAction, Teammate,
  startOrder,
  completeOrder,
  rejectOrder,
  fetchProviderStats,
  fetchMyEvaluations,
  replyEvaluation,
} from '@/services/order';
import { Evaluation, ProviderOrder, ProviderStats, statusTextMap, REJECT_REASONS, isGiftOrder, orderServiceName } from '@/types/order';
import { wsService } from '@/services/websocket';
import { orderAlerts } from '@/services/orderAlerts';
import { createCoalescedRefresh, isPersonalOrderUpdate } from '@/utils/coalescedRefresh';
import { formatXaCoin } from '@/utils/format';
import { createReport } from '@/services/report';
import {
  fetchProviderPass, PassProduct, ProviderPassData, purchaseProviderPass,
} from '@/services/user';
import { Empty, Skeleton } from '@/components';
import styles from './index.module.scss';
import PlatformLayout from '@/components/PlatformLayout';
import PageHeading from '@/components/PageHeading';
import ReplyDialog from '@/components/ReplyDialog';
import SurfaceDialog from '@/components/SurfaceDialog';
import StackDisclosure from '@/components/StackDisclosure';

type TabKey = 'pending' | 'grabbed' | 'in_progress' | 'completed' | 'cancelled';

const reservationDate = (value: string) => {
  const time = new Date(value);
  if (Number.isNaN(time.getTime())) return '—';
  const local = new Date(time.getTime() + 8 * 60 * 60 * 1000);
  const pad = (part: number) => String(part).padStart(2, '0');
  return `${local.getUTCFullYear()}-${pad(local.getUTCMonth() + 1)}-${pad(local.getUTCDate())} ${pad(local.getUTCHours())}:${pad(local.getUTCMinutes())}`;
};

const TABS: { key: TabKey; label: string }[] = [
  { key: 'pending', label: '抢单池' },
  { key: 'grabbed', label: '已接单' },
  { key: 'in_progress', label: '服务中' },
  { key: 'completed', label: '已完成' },
  { key: 'cancelled', label: '已取消' },
];

/** 把后端下发的等级 hex 色转为带透明度的 rgba，用于卡片淡色底。非法值返回空串。 */
const hexToRgba = (hex: string, alpha: number): string => {
  const value = (hex || '').replace('#', '');
  if (value.length !== 6) return '';
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  if ([r, g, b].some(Number.isNaN)) return '';
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
};

const OrdersPage: React.FC = () => {
  const [network, setNetwork] = useState(() => networkSnapshot(getSessionRevision()));
  const [trafficMode, setTrafficMode] = useState('');
  const visible = useRef(true);
  const [activeTab, setActiveTab] = useState<TabKey>('pending');
  const [focusedId, setFocusedId] = useState<number | null>(null);
  const focusedIdRef = useRef<number | null>(null);
  const focusedSession = useRef<number | null>(null);
  const [qualifications,setQualifications]=useState<ProviderQualification[]>([]);
  const [scopeIndex,setScopeIndex]=useState(0);
  const scopeRef=useRef<ProviderQualification|null>(null);
  useEffect(()=>{fetchEscortMe().then(r=>{if(r.code===0 && r.data)setQualifications((r.data.qualifications||[]).filter(q=>q.is_active))}).catch(()=>{})},[]);
  const filterOrders=(rows:ProviderOrder[],tab:TabKey)=>tab !== 'pending' ? rows : rows.filter(o=>{const q=scopeRef.current,r=o.provider_requirement;return !q || (r && r.game_category_id===q.game_category && r.provider_kind===q.provider_kind && r.platform===q.platform && r.security_level===q.security_level)});
  const [orders, setOrders] = useState<ProviderOrder[]>([]);
  const pendingOrders = useRef<ProviderOrder[]>([]);
  const [stats, setStats] = useState<ProviderStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [acting, setActing] = useState(false);
  const [applying, setApplying] = useState<ProviderOrder | null>(null);
  const [teamSize, setTeamSize] = useState(1);
  const [teammates, setTeammates] = useState<Teammate[]>([]);
  const [partnerIndex, setPartnerIndex] = useState(-1);
  const [teammateLoading, setTeammateLoading] = useState(false);
  const applicationSequence = useRef(0);
  const actionLock = useRef(false);
  const loadSequence = useRef(0);
  const [passData, setPassData] = useState<ProviderPassData | null>(null);
  const buyingPass = useRef(false);
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [evaluationsLoading, setEvaluationsLoading] = useState(true);
  const [evaluationsError, setEvaluationsError] = useState('');
  const evaluationsSequence = useRef(0);
  const [replying, setReplying] = useState<Evaluation | null>(null);
  const [replyConflict, setReplyConflict] = useState('');
  const [rejecting, setRejecting] = useState<ProviderOrder | null>(null);
  const activeTabRef = useRef<TabKey>(activeTab);
  activeTabRef.current = activeTab;

  const loadStats = useCallback(async () => {
    try {
      const res = await fetchProviderStats();
      if (res.code === 0 && res.data) setStats(res.data);
    } catch {
      /* 看板失败不阻塞列表 */
    }
  }, []);

  const loadPass = useCallback(async () => {
    try {
      const res = await fetchProviderPass();
      if (res.code === 0 && res.data) setPassData(res.data);
    } catch { /* 通行证加载失败不阻塞接单 */ }
  }, []);

  const loadEvaluations = useCallback(async () => {
    const sequence = ++evaluationsSequence.current;
    const session = getSessionRevision();
    setEvaluationsLoading(true);
    setEvaluationsError('');
    try {
      const res = await fetchMyEvaluations();
      if (sequence !== evaluationsSequence.current || session !== getSessionRevision()) return;
      if (res.code !== 0 || !res.data) throw new Error(res.msg || '评价暂时无法加载');
      setEvaluations(res.data.list || []);
    } catch {
      if (sequence === evaluationsSequence.current && session === getSessionRevision()) setEvaluationsError('评价暂时无法加载，请重试');
    } finally {
      if (sequence === evaluationsSequence.current && session === getSessionRevision()) setEvaluationsLoading(false);
    }
  }, []);

  const loadOrders = useCallback(async (tab: TabKey, quiet = false) => {
    const target = focusedIdRef.current;
    const session = getSessionRevision();
    const wait = !target && tab === 'pending' ? cooldownRemaining('pool', getSessionRevision()) : 0;
    if (wait) {
      ++loadSequence.current;
      setOrders([]);
      setLoading(false);
      setLoadError(`抢单池请求受限，请在 ${wait} 秒后刷新`);
      return;
    }
    const sequence = ++loadSequence.current;
    if (!quiet) setLoading(true);
    try {
      if (target) {
        const res = await fetchOrderDetail(target);
        if (sequence !== loadSequence.current || focusedIdRef.current !== target || session !== getSessionRevision()) return;
        if (res.code === 0 && res.data) {
          const tabs: Record<ProviderOrder['status'], TabKey> = { PENDING: 'pending', GRABBED: 'grabbed', IN_SERVICE: 'in_progress', COMPLETED: 'completed', CANCELLED: 'cancelled' };
          setActiveTab(tabs[res.data.status]);
          setOrders([res.data]); setLoadError('');
        } else { setOrders([]); setLoadError(res.msg || '订单暂时无法查看'); }
        return;
      }
      const res = await fetchOrders({ status: tab, role: 'provider' });
      if (res.traffic_mode) setTrafficMode(res.traffic_mode);
      if (sequence !== loadSequence.current || tab !== activeTabRef.current || session !== getSessionRevision()) return;
      if (res.code === 0 && res.data) {
        if (tab === 'pending') pendingOrders.current = res.data;
        setOrders(filterOrders(res.data, tab));
        setLoadError('');
      } else {
        setLoadError(res.msg || '订单暂时无法加载，请重试');
      }
    } catch (error) {
      if (sequence === loadSequence.current && session === getSessionRevision()) setLoadError(error instanceof Error ? error.message : '订单加载失败');
    } finally {
      if (sequence === loadSequence.current && session === getSessionRevision()) setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadOrders(activeTab);
    loadStats();
    loadPass();
    loadEvaluations();
    // 合并密集推送；请求期间收到新变化时，完成后再读取一次最新状态。
    const refresh = createCoalescedRefresh(() => loadOrders(activeTabRef.current, true));
    const refreshStats = createCoalescedRefresh(loadStats);
    const refreshEvaluations = createCoalescedRefresh(loadEvaluations);
    const off = wsService.on('order_status_update', data => {
      if (!visible.current) return;
      if (activeTabRef.current !== 'pending' || focusedIdRef.current) refresh.request();
      // 公共池变化不改变本人的服务中数量和收入；本人订单完整通知才刷新看板。
      if (isPersonalOrderUpdate(data)) {
        refreshStats.request();
        if ((data as Partial<ProviderOrder>).status === 'COMPLETED') refreshEvaluations.request();
      }
    });
    const offConnected = wsService.on('connected', () => {
      if (!visible.current) return;
      // Pub/sub cannot replay events missed while offline. Re-read every tab.
      if (activeTabRef.current !== 'pending' || focusedIdRef.current) refresh.request();
      refreshStats.request();
      loadPass();
      loadEvaluations();
    });
    // App-wide monitoring also updates the pool while the workbench is open.
    const offPool = orderAlerts.onPoolUpdate(response => {
      if (!visible.current || activeTabRef.current !== 'pending' || focusedIdRef.current) return;
      pendingOrders.current = response.data || [];
      setOrders(filterOrders(pendingOrders.current, 'pending'));
      setLoadError('');
      if (response.traffic_mode) setTrafficMode(response.traffic_mode);
    });
    return () => {
      loadSequence.current++;
      evaluationsSequence.current++;
      off();
      offConnected();
      refresh.dispose();
      refreshStats.dispose();
      refreshEvaluations.dispose();
      offPool();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadOrders(activeTab);
  }, [activeTab, loadOrders]);

  useEffect(() => {
    request<ApiResponse<{ mode: string }>>('/orders/traffic-status/').then(res => { if (res.data) setTrafficMode(res.data.mode); }).catch(() => {});
    let reporting = false;
    const viewTimer = setInterval(() => { if (visible.current) setNetwork(networkSnapshot(getSessionRevision())); }, 1000);
    const reportTimer = setInterval(async () => {
      if (!visible.current || reporting) return;
      const revision = getSessionRevision(), report = pendingNetworkReport(revision);
      if (!report.samples) return;
      reporting = true;
      try {
        const res = await request<ApiResponse>('/orders/network-report/', 'POST', report);
        if (res.code === 0) acknowledgeNetworkReport(report.cursor, revision);
      } catch { /* 监控上报失败不干扰订单操作 */ }
      finally { reporting = false; }
    }, 60000);
    return () => { clearInterval(viewTimer); clearInterval(reportTimer); };
  }, []);
  useDidHide(() => { visible.current = false; });
  useDidShow(() => {
    visible.current = true;
    const target = takeOrderFocus();
    if (target === 0) {
      focusedIdRef.current = null; focusedSession.current = null; setFocusedId(null);
      activeTabRef.current = 'pending'; setActiveTab('pending');
      loadOrders('pending');
    } else {
      if (target) { focusedIdRef.current = target; focusedSession.current = getSessionRevision(); setFocusedId(target); }
      else if (focusedIdRef.current && focusedSession.current !== getSessionRevision()) { focusedIdRef.current = null; focusedSession.current = null; setFocusedId(null); setOrders([]); }
      loadOrders(activeTabRef.current);
    }
    loadStats();
    loadPass();
    loadEvaluations();
  });

  const selectTab = (tab: TabKey) => {
    const wasFocused = !!focusedIdRef.current;
    focusedIdRef.current = null; focusedSession.current = null; setFocusedId(null);
    if (tab === activeTabRef.current && wasFocused) loadOrders(tab);
    else setActiveTab(tab);
  };

  usePullDownRefresh(async () => {
    await Promise.all([loadOrders(activeTabRef.current), loadStats(), loadPass(), loadEvaluations()]);
    Taro.stopPullDownRefresh();
  });

  const runAction = async <T extends { code: number; msg?: string },>(fn: () => Promise<T>, successText: string | ((response: T) => string), uncertainText?: string, onUncertain?: () => void) => {
    if (actionLock.current) return false;
    actionLock.current = true;
    setActing(true);
    Taro.showLoading({ title: '处理中' });
    try {
      const res = await fn();
      if (res.code === 0) {
        Taro.showToast({ title: typeof successText === 'function' ? successText(res) : successText, icon: 'success' });
        await Promise.all([loadOrders(activeTabRef.current), loadStats()]);
        return true;
      } else {
        Taro.showToast({ title: res.msg || '操作失败', icon: 'none' });
        // The server may have rejected an action because another participant
        // already changed the order. Refresh even if the socket event was missed.
        await Promise.all([loadOrders(activeTabRef.current, true), loadStats()]);
        return false;
      }
    } catch (error) {
      Taro.showToast({ title: uncertainText || (error instanceof Error ? error.message : '操作失败'), icon: 'none' });
      onUncertain?.();
      if (uncertainText) await Promise.all([loadOrders(activeTabRef.current, true), loadStats()]);
      return false;
    } finally {
      Taro.hideLoading();
      actionLock.current = false;
      setActing(false);
    }
  };

  const handleGrab = async (order: ProviderOrder) => {
    const wait = cooldownRemaining('grab', getSessionRevision());
    if (wait) { Taro.showToast({ title: `请在 ${wait} 秒后重试`, icon: 'none' }); return; }
    const size = order.required_team_size === 2 ? 2 : 1;
    if (!order.requires_boss_selection) {
      if (size === 2) { Taro.showToast({ title: '本单需要组队，请刷新订单或联系客服', icon: 'none' }); return; }
      return runAction(() => grabOrder(order.id), '接单成功', '接单结果未确认，请查看已接单');
    }
    if (size === 2 && order.can_team_apply !== true) {
      Taro.showToast({ title: '当前订单暂不可组队，请刷新后重试', icon: 'none' });
      return;
    }
    const sequence = ++applicationSequence.current;
    setTeamSize(size); setPartnerIndex(-1); setTeammates([]); setApplying(order); setTeammateLoading(size === 2);
    if (size === 1) return;
    try {
      const res = await fetchTeammates(order.id);
      if (sequence !== applicationSequence.current) return;
      if (res.code === 0) setTeammates(res.data || []);
      else Taro.showToast({ title: res.msg || '队友加载失败', icon: 'none' });
    } catch (error) { Taro.showToast({ title: (error as Error).message || '队友加载失败', icon: 'none' }); }
    finally { if (sequence === applicationSequence.current) setTeammateLoading(false); }
  };

  // 开始服务/完成订单必须上传对应截图：先选图，用户取消则中止，不推进状态。
  const chooseImage = async (): Promise<string | null> => {
    const res = await Taro.chooseImage({ count: 1, sizeType: ['compressed'] }).catch(() => null);
    return res?.tempFilePaths?.[0] || null;
  };
  const handleStart = async (order: ProviderOrder) => {
    if (acting) return;
    const path = await chooseImage();
    if (!path) return;
    runAction(() => startOrder(order.id, path), res => res.data?.status === 'IN_SERVICE' ? '已开始服务' : res.msg || '截图已提交，请刷新订单核对状态', '开始结果未确认，请核对订单状态');
  };
  const handleComplete = async (order: ProviderOrder) => {
    if (acting) return;
    const path = await chooseImage();
    if (!path) return;
    runAction(() => completeOrder(order.id, path), res => res.data?.status === 'COMPLETED' ? '订单已完成' : res.msg || '截图已提交，请刷新订单核对状态', '完成结果未确认，请核对订单状态');
  };

  // 同时进行中订单（已接单 + 服务中）是否已达上限
  const maxConcurrent = stats?.max_concurrent_orders ?? 0;
  const reachedLimit = !!stats && maxConcurrent > 0 && (stats.active_orders ?? 0) >= maxConcurrent;

  const handleReject = (order: ProviderOrder) => {
    Taro.showActionSheet({
      itemList: [...REJECT_REASONS],
      success: ({ tapIndex }) => {
        const reason = REJECT_REASONS[tapIndex];
        if (reason === '其他') {
          setRejecting(order);
        } else {
          runAction(() => rejectOrder(order.id, reason), res => res.msg || '已拒单', '拒单结果未确认，请刷新已接单核对');
        }
      },
    });
  };

  const handleReply = async (text: string) => {
    if (!replying || actionLock.current || replyConflict) return;
    const evaluation = replying;
    const session = getSessionRevision();
    actionLock.current = true;
    setActing(true);
    try {
      const res = await replyEvaluation(evaluation.id, text, evaluation.reply_version);
      if (session !== getSessionRevision()) return;
      if (!res || typeof res.code !== 'number' || res.code >= 500) { setReplyConflict('回复结果未确认，草稿仍在此处。关闭后将刷新评价，请核对是否发布。'); return; }
      if (res.errorCode === 'EVALUATION_REPLY_CONFLICT') { setReplyConflict('回复已变化，草稿仍在此处。关闭后将刷新评价，请核对最新回复。'); return; }
      if (res.code !== 0) { Taro.showToast({ title: res.msg || '回复失败', icon: 'none' }); return; }
      setReplying(null);
      Taro.showToast({ title: '回复已发布', icon: 'success' });
      await loadEvaluations();
    } catch (error) {
      if (session === getSessionRevision()) setReplyConflict('回复结果未确认，草稿仍在此处。关闭后将刷新评价，请核对是否发布。');
    } finally { actionLock.current = false; setActing(false); }
  };

  const handleBuyPass = async () => {
    if (!passData || buyingPass.current) return;
    buyingPass.current = true;
    try {
    const products = passData.products;
    const { tapIndex } = await Taro.showActionSheet({
      itemList: products.map(item => `${item.name} · ${formatXaCoin(item.daily_price)}兴安币/天`),
    }).catch(() => ({ tapIndex: -1 }));
    if (tapIndex < 0) return;
    const product: PassProduct = products[tapIndex];
    const confirm = await Taro.showModal({
      title: `采购${product.name}`,
      content: `有效期1天，从钱包扣除 ${formatXaCoin(product.daily_price)} 兴安币。${product.delay_seconds ? `订单发布${product.delay_seconds}秒后可见。` : '订单发布后立即可见。'}`,
      confirmText: '确认采购',
    });
    if (!confirm.confirm) return;
    Taro.showLoading({ title: '采购中' });
    try {
      const res = await purchaseProviderPass(product.tier, product.daily_price);
      if (res.code === 0 && res.data) {
        setPassData(res.data);
        await loadOrders(activeTabRef.current);
        Taro.showToast({ title: '采购成功', icon: 'success' });
      } else Taro.showToast({ title: res.msg || '采购失败', icon: 'none' });
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : '采购失败', icon: 'none' });
    } finally { Taro.hideLoading(); await loadPass(); }
    } finally { buyingPass.current = false; }
  };

  const renderActions = (order: ProviderOrder) => {
    const can = (action: 'GRAB' | 'START' | 'COMPLETE' | 'REJECT', fallback: boolean) =>
      Array.isArray(order.allowed_actions) ? order.allowed_actions.includes(action) : fallback;
    if (order.status === 'PENDING') {
      const mine = (order.my_applications || []).filter(item => item.status === 'PENDING');
      if (mine.length) return <View className={styles.actions}>{mine.map(item => <View key={item.id}>
        <Text>{item.members.map(member => member.nickname + ' · ' + member.level).join(' + ')} · {item.team_size}人 · {item.ready ? '等待老板选择' : '等待队友确认'}</Text>
        {item.is_partner && !item.partner_confirmed && <Button size='mini' disabled={acting} onClick={() => runAction(() => applicationAction(order.id, item.id, 'CONFIRM_TEAM'), '队伍已确认', '组队结果未确认，请核对报名状态')}>确认组队</Button>}
        <Button size='mini' disabled={acting} onClick={() => runAction(() => applicationAction(order.id, item.id, 'WITHDRAW'), '已撤回报名', '撤回结果未确认，请核对报名状态')}>撤回报名</Button>
      </View>)}</View>;
      if (!can('GRAB', true)) return null;
      return (
        <View className={styles.actions}>
          <Button
            className={`xa-button-primary ${styles.actionBtn} ${styles.primaryBtn}`}
            disabled={acting || reachedLimit}
            onClick={() => reachedLimit
              ? Taro.showToast({ title: `最多同时进行 ${maxConcurrent} 单`, icon: 'none' })
              : handleGrab(order)}
          >
            {reachedLimit ? '已达接单上限' : order.requires_boss_selection ? '报名抢单' : '抢单'}
          </Button>
        </View>
      );
    }
    if (order.status === 'GRABBED') {
      if (!can('START', Boolean(order.can_operate))) return null;
      return (
        <View className={styles.actions}>
          {can('REJECT', Boolean(order.can_reject)) && <Button className={`${styles.actionBtn} ${styles.rejectBtn}`} disabled={acting} onClick={() => handleReject(order)}>拒单</Button>}
          <Button className={`xa-button-primary ${styles.actionBtn} ${styles.primaryBtn}`} disabled={acting} onClick={() => handleStart(order)}>
            开始服务
          </Button>
        </View>
      );
    }
    if (order.status === 'IN_SERVICE') {
      if (!can('COMPLETE', Boolean(order.can_operate))) return null;
      return (
        <View className={styles.actions}>
          <Button className={`xa-button-primary ${styles.actionBtn} ${styles.primaryBtn}`} disabled={acting} onClick={() => handleComplete(order)}>
            完成订单
          </Button>
        </View>
      );
    }
    return null;
  };

  const requestHealth = network.stale ? '等待采样' : network.failures || network.jitter_ms > 300 ? '请求波动' : network.server_errors ? '服务异常' : network.grab_wait || network.pool_wait ? '限流等待' : '未见网络异常';

  return (
    <PlatformLayout active='orders'><View className={`xa-provider-page ${styles.container}`}>
      <PageHeading title='接单工作台' action={<View><Button className={styles.skillButton} onClick={() => Taro.navigateTo({ url: '/pages/reservations/index' })}>我的预约 ›</Button><Button className={styles.skillButton} onClick={() => Taro.navigateTo({ url: '/pages/skills/index' })}>我的技能 ›</Button></View>} />
      <View className={styles.dashboard}>
        <View className={styles.dashboardTop}>
          <View><Text className={styles.eyebrow}>今日接单</Text><Text className={styles.todayValue}>{stats?.today_orders || 0}<Text className={styles.todayUnit}> 单</Text></Text></View>
          <View className={styles.liveBadge}><View className={styles.liveDot} /><Text>{stats?.serving || 0} 单服务中</Text></View>
        </View>
        <View className={styles.dashboardBottom}>
          <View><Text className={styles.metricLabel}>完成率</Text><Text className={styles.metricValue}>{stats?.completion_rate || 0}%</Text></View>
          <View><Text className={styles.metricLabel}>服务及推荐净收益</Text><Text className={styles.metricValue}>{formatXaCoin(stats?.total_income || 0)}币</Text></View>
        </View>
      </View>

      {passData && <Button className={`${styles.passCard} ${passData.active_tier ? styles.passActive : ''}`} onClick={handleBuyPass}>
        <View className={styles.passEmblem}>{passData.active_tier ? passData.active_tier_display.slice(0, 1) : '卡'}</View>
        <View className={styles.passInfo}>
          <View className={styles.passTitleRow}><Text className={styles.passName}>{passData.active_tier_display}</Text><Text className={styles.passPill}>{passData.active_tier ? '生效中' : '未开通'}</Text></View>
          <Text className={styles.passMeta}>{passData.active_tier ? `有效至 ${new Date(passData.expires_at || '').toLocaleString()}` : '升级通行证，更早看到优质订单'}</Text>
        </View>
        <View className={styles.passAction}>{passData.active_tier ? '续费' : '采购'} <Text>›</Text></View>
      </Button>}

      <View className={styles.tabs}>
        {TABS.map(tab => (
          <Button
            key={tab.key}
            className={`${styles.tabItem} ${activeTab === tab.key ? styles.tabItemActive : ''}`}
            aria-pressed={activeTab === tab.key}
            onClick={() => selectTab(tab.key)}
          >
            <Text className={styles.tabText}>{tab.label}</Text>
          </Button>
        ))}
      </View>

      {!!focusedId && <View className={styles.focusNotice}><Text>正在查看关联订单</Text><Button className={styles.skillButton} onClick={() => selectTab(activeTab)}>查看全部订单</Button></View>}

      {activeTab === 'pending' && !focusedId && <View className={styles.scopeBar}>
        <Text className={styles.scopeTitle}>抢单范围</Text>
        <ScrollView scrollX className={styles.scopeScroller}>
          <View className={styles.scopeOptions}>
            {['全部接单资格', ...qualifications.map(q => q.label)].map((label, index) => <Button
              key={index} className={`${styles.scopeChoice} ${scopeIndex === index ? styles.scopeChoiceActive : ''}`}
              aria-pressed={scopeIndex === index}
              onClick={() => { setScopeIndex(index); scopeRef.current = index ? qualifications[index - 1] : null; setOrders(filterOrders(pendingOrders.current, 'pending')); }}
            >{label}</Button>)}
          </View>
        </ScrollView>
      </View>}
      {activeTab === 'pending' && !focusedId && <StackDisclosure title={`抢单请求 · ${requestHealth}`}><View className={styles.networkPanel}>
        <Text className={styles.networkTitle}>抢单请求 · {requestHealth}{trafficMode ? ` · ${({ peak: '波峰', valley: '波谷', normal: '常规' } as Record<string, string>)[trafficMode] || trafficMode}` : ''}</Text>
        <Text>近一分钟 {network.samples} 次 · 平均 {network.average_ms}ms · P95 {network.p95_ms}ms · 抖动 {network.jitter_ms}ms</Text>
        <Text>网络失败 {network.failure_percent}% · 服务异常 {network.server_errors} 次 · 限流 {network.limited} 次{network.grab_wait ? ` · 抢单等待 ${network.grab_wait}s` : ''}{network.pool_wait ? ` · 刷新等待 ${network.pool_wait}s` : ''}</Text>
        {network.failures > 0 && <Button className={styles.skillButton} onClick={() => setActiveTab('grabbed')}>查看已接单，核对抢单结果</Button>}
        <Text className={styles.networkHint}>仅统计抢单池和抢单请求，不代表推送连接状态。前台约每10秒尝试刷新抢单池，网络或限流可能延迟。抢单超时后请先查看已接单，确认结果后重试。</Text>
      </View></StackDisclosure>}
      {!!loadError && <View className={styles.loadError}><Text>{loadError}</Text><Button className={styles.skillButton} onClick={() => loadOrders(activeTab)}>重新加载</Button></View>}
      <View className={styles.listSummary}><Text>{TABS.find(tab => tab.key === activeTab)?.label}</Text><Text>{loading ? '正在加载' : loadError ? '连接异常' : `${orders.length} 笔订单`}</Text></View>
      <View className={styles.list}>
        {loading ? (
          <Skeleton variant="card-list" rows={3} />
        ) : loadError ? null : orders.length === 0 ? (
          <View className={styles.emptyPanel}><Empty icon="🎮" title={activeTab === 'pending' ? '暂时没有可接订单' : activeTab === 'cancelled' ? '暂无已取消订单' : '暂无此类订单'} desc={activeTab === 'pending' ? (reachedLimit ? `已达同时进行 ${maxConcurrent} 单上限，完成订单后可继续抢单` : scopeIndex ? '当前抢单范围暂无订单，可以切换到全部接单资格查看。' : '订单会自动更新。可在「我的技能」中检查服务范围。') : activeTab === 'cancelled' ? '取消或退款后的订单会显示在这里。' : '订单状态更新后，会出现在对应列表中。'}>
            {activeTab === 'pending' && scopeIndex > 0 && <Button className={styles.skillButton} onClick={() => { setScopeIndex(0); scopeRef.current = null; setOrders(filterOrders(pendingOrders.current, 'pending')); }}>查看全部接单资格</Button>}
            <Button className={styles.skillButton} onClick={() => loadOrders(activeTab)}>刷新订单</Button>
          </Empty></View>
        ) : (
          orders.map(order => {
            const partialRefund = order.payment_status === 'PARTIALLY_REFUNDED';
            const refund = order.refund_summary;
            const bossType = order.customer?.boss_type;
            const vipColor = bossType?.color || '';
            const cardStyle = vipColor
              ? { borderLeft: `3px solid ${vipColor}` }
              : undefined;
            const showDiscount = !!bossType && bossType.discount_rate < 100;
            return (
            <View key={order.id} className={styles.orderCard} style={cardStyle}>
              {!!bossType && (
                <View className={styles.vipRow}>
                  <Text
                    className={styles.vipBadge}
                    style={vipColor ? { background: vipColor } : undefined}
                  >{bossType.name}</Text>
                  <Text className={styles.bossName}>{order.customer?.nickname || '老板'}</Text>
                </View>
              )}
              <View className={styles.cardHeader}>
                <View className={styles.serviceBlock}><Text className={styles.serviceName}>{orderServiceName(order)}</Text>{order.provider_requirement?.label && <Text className={styles.orderScope}>{order.provider_requirement.label}</Text>}
                  <Text className={styles.orderNo}>{order.order_no}</Text>{order.fulfillment && <Text className={styles.orderNo}>{order.fulfillment}</Text>}</View>
                <View className={styles.priceBlock}><Text className={styles.pricePrefix}>{partialRefund ? order.settled_at ? '原核定单钱' : '原预计单钱' : order.status === 'CANCELLED' ? '原预计单钱' : '打手单钱'}</Text><Text className={styles.amountValue}>{order.expected_income == null ? '待核对' : `${formatXaCoin(order.expected_income)}币`}</Text></View>
              </View>
              {order.reservation_origin && <View className={styles.orderScope}><Text>原预约时段（北京时间）：{reservationDate(order.reservation_origin.start_time)} 至 {reservationDate(order.reservation_origin.end_time)}{order.status === 'PENDING' ? '。请与老板确认服务安排。' : ''}</Text>{order.reservation_origin.can_open && <Button className={styles.skillButton} size='mini' onClick={() => Taro.navigateTo({ url: `/pages/reservations/index?id=${order.reservation_origin!.id}` })}>查看预约</Button>}</View>}
              <View className={styles.chips}>
                <Text className={`${styles.statusTag} ${styles[`status_${order.status}`]}`}>{partialRefund ? '已完成 · 部分退款' : order.status === 'CANCELLED' && order.payment_status === 'REFUNDED' ? '已退款' : statusTextMap[order.status]}</Text>
                {order.my_assignment_released && <Text className={styles.chip}>本人已转出</Text>}
                <Text className={styles.chip}>{order.game_rounds} {order.quantity_unit || '份'}</Text>
                {!!order.participants?.length && <Text className={styles.chip}>{order.participants.length === 2 ? '两人服务' : '一人服务'}</Text>}
                {order.provider_gender && order.provider_gender !== 'ANY' && <Text className={styles.chip}>{order.provider_gender === 'MALE' ? '男陪' : '女陪'}</Text>}
                {!!order.game_region && <Text className={styles.chip}>{order.game_region}</Text>}
                {showDiscount && (
                  <Text
                    className={styles.discountChip}
                    style={vipColor ? { color: vipColor, background: hexToRgba(vipColor, 0.12) || undefined } : undefined}
                  >{bossType!.discount_rate / 10}折 VIP价</Text>
                )}
              </View>
              {order.aftersale_status === 'OPEN' && <View className={styles.orderScope}>{order.settled_at ? '售后处理中，请关注处理结果；已结算收益以钱包流水为准。' : '售后处理中，本单暂不结算；处理后仍需满足结算条件。'}</View>}
              {!!order.participants?.length && <View className={styles.infoRow}><Text className={styles.infoLabel}>本单陪玩</Text><Text className={styles.infoValue}>{order.participants.map(member => member.nickname).join(' + ')}</Text></View>}
              {order.team_progress && <View className={styles.teamProgress}>
                <Text className={styles.teamProgressTitle}>{order.team_progress.stage === 'ENTRY' ? '入队截图' : '结单截图'} · {order.team_progress.submitted_count}/{order.team_progress.total_count} 已提交</Text>
                {order.team_progress.members.map(member => <Text className={styles.teamProgressMember} key={member.id}>{member.nickname} · {member.submitted ? '已提交' : '等待提交'}</Text>)}
              </View>}
              {order.status === 'CANCELLED' && !!order.cancel_reason && <View className={styles.infoRow}><Text className={styles.infoLabel}>{order.payment_status === 'REFUNDED' ? '退款原因' : '取消原因'}</Text><Text className={styles.infoValue}>{order.cancel_reason}</Text></View>}
              {!!order.game_nickname && (
                <View className={styles.infoRow}>
                  <Text className={styles.infoLabel}>老板游戏昵称</Text>
                  <Text className={styles.infoValue}>{order.game_nickname}</Text>
                </View>
              )}
              <StackDisclosure title='收益与结算'><View className={styles.incomePanel}>
                <View><Text className={styles.incomeLabel}>{partialRefund ? '部分退款，保留剩余净收益' : order.status === 'CANCELLED' ? '原预计金额仅供核对' : '按本单分账规则核算收益'}</Text><Text className={styles.incomeHint}>{partialRefund ? '实际净收益及剩余冻结以钱包流水为准' : order.status === 'CANCELLED' ? '订单已取消/退款，实际净收益以钱包流水为准' : isGiftOrder(order) ? '礼物结算进度请在钱包查看' : order.expected_income == null ? '收益待核对，请联系客服核对' : order.settlement_status === 'SETTLED' ? order.expected_income === 0 ? '已结算，本单收益为 0' : '收益已记入内部钱包，冻结及可用金额以钱包为准' : order.settlement_status === 'WAITING' ? '报单全部通过、售后窗口结束且无未结客诉后结算' : '收益待核对，请联系客服核对'}</Text>
                  {refund && <><Text className={styles.incomeHint}>累计已退 {formatXaCoin(refund.amount)}币{refund.remaining_amount != null ? ` · 订单剩余 ${formatXaCoin(refund.remaining_amount)}币` : ''}</Text>
                    {refund.recovered_amount != null && <Text className={styles.incomeHint}>服务收益累计追回 {formatXaCoin(refund.recovered_amount)}币</Text>}
                    {refund.net_income != null && <Text className={styles.incomeHint}>服务净收益 {formatXaCoin(refund.net_income)}币</Text>}
                    {!!refund.referral_recovered_amount && <Text className={styles.incomeHint}>推荐分佣追回 {formatXaCoin(refund.referral_recovered_amount)}币</Text>}
                    {!!refund.referral_net_income && <Text className={styles.incomeHint}>推荐分佣净额 {formatXaCoin(refund.referral_net_income)}币</Text>}
                    {refund.income_breakdown_unavailable && <Text className={styles.incomeHint}>收益明细待核对，请查看钱包流水或联系客服</Text>}
                    {refund.remaining_frozen_amount != null && <Text className={styles.incomeHint}>本单剩余冻结 {formatXaCoin(refund.remaining_frozen_amount)}币</Text>}
                    {refund.records?.map(record => <Text className={styles.incomeHint} key={record.id}>退款 #{record.id} · {formatXaCoin(record.amount)}币{record.recovered_amount != null ? ` · 服务收益追回 ${formatXaCoin(record.recovered_amount)}币` : ''}{record.referral_recovered_amount ? ` · 推荐分佣追回 ${formatXaCoin(record.referral_recovered_amount)}币` : ''} · {new Date(record.created_at).toLocaleString()}</Text>)}
                  </>}
                </View>
                {isGiftOrder(order) && <Button className={styles.reportButton} size='mini' onClick={() => Taro.switchTab({ url: '/pages/wallet/index' })}>前往钱包</Button>}
              </View>
              </StackDisclosure><View className={styles.requirement}>
                <Text className={styles.requirementLabel}>老板备注要求</Text>
                <Text className={`${styles.requirementText} ${!order.remark ? styles.requirementEmpty : ''}`}>{order.remark || '老板暂无额外要求'}</Text>
              </View>
              {order.status === 'COMPLETED' && !order.my_assignment_released && !isGiftOrder(order) && (() => {
                const evaluation = evaluations.find(item => item.order === order.id);
                return evaluationsLoading ? <View className={styles.awaitingReview}><Text>正在加载评价…</Text></View>
                  : evaluationsError ? <View className={styles.awaitingReview}><Text>{evaluationsError}</Text><Button className={styles.replyButton} onClick={loadEvaluations}>重新加载评价</Button></View>
                  : evaluation ? <View className={styles.evaluation}>
                  <View className={styles.evaluationHead}><Text className={styles.evaluationTitle}>老板评价</Text><Text className={styles.evaluationScore}>{'★'.repeat(Math.round(evaluation.score))} {evaluation.score.toFixed(1)}</Text></View>
                  <Text className={styles.evaluationContent}>{evaluation.content || '老板未填写文字评价'}</Text>
                  {evaluation.reply_content ? <View className={styles.myReply}><Text className={styles.replyLabel}>我的回复</Text><Text className={styles.replyText}>{evaluation.reply_content}</Text></View>
                    : <Button className={styles.replyButton} disabled={acting} onClick={() => { setReplyConflict(''); setReplying(evaluation); }}>回复老板</Button>}
                </View> : <View className={styles.awaitingReview}><Text>等待老板评价</Text><Text>评价后可在此直接回复</Text></View>;
              })()}
              {['GRABBED', 'IN_SERVICE', 'COMPLETED'].includes(order.status) && !isGiftOrder(order) && order.report_entry?.available === false && order.report_entry.reason && <Text className={styles.incomeHint}>{order.report_entry.reason}</Text>}
              {['GRABBED', 'IN_SERVICE', 'COMPLETED'].includes(order.status) && !isGiftOrder(order) && order.report_entry?.available !== false && <Button className={styles.reportButton} size='mini' disabled={acting} onClick={async () => {
                if (acting) return;
                const revision = getSessionRevision();
                let queued = false;
                setActing(true);
                try {
                  let res: Awaited<ReturnType<typeof createReport>> | undefined;
                  try { res = await createReport(order.id); } catch { /* Read report state on the destination page. */ }
                  if (revision !== getSessionRevision()) return;
                  const uncertain = !res || typeof res.code !== 'number' || res.code >= 500;
                  if (!uncertain && res?.code !== 0) { Taro.showToast({ title: res?.msg || '报单打开失败', icon: 'none' }); return; }
                  queueReportOrder(order.id);
                  queued = true;
                  await Taro.switchTab({ url: '/pages/report/index' });
                  if (uncertain && revision === getSessionRevision()) Taro.showToast({ title: '报单结果未确认，请核对报单', icon: 'none' });
                } catch { if (revision === getSessionRevision()) { if (queued) clearReportOrder(); Taro.showToast({ title: '报单页面未打开，请手动查看', icon: 'none' }); } }
                finally { setActing(false); }
              }}>查看 / 补充报单</Button>}
              <View className={styles.cardFooter}>
                <Text className={styles.time}>{new Date(order.created_at).toLocaleString()}</Text>
                {renderActions(order)}
              </View>
            </View>
            );
          })
        )}
      </View>
      <SurfaceDialog open={!!applying} title='报名抢单' busy={acting} onClose={() => { applicationSequence.current++; setApplying(null); }} footer={<Button className={'xa-button-primary ' + styles.actionBtn + ' ' + styles.primaryBtn} disabled={acting || (teamSize === 2 && partnerIndex < 0)} onClick={async () => {
        if (!applying) return;
        const requiredSize = applying.required_team_size === 2 ? 2 : 1;
        if (teamSize !== requiredSize || (requiredSize === 2 && (applying.can_team_apply !== true || !teammates[partnerIndex]))) return;
        const order = applying;
        const success = await runAction(() => grabOrder(order.id, teamSize, teamSize === 2 ? teammates[partnerIndex].id : undefined), '报名已提交', '报名结果未确认，请核对抢单池', () => setApplying(null));
        if (success) setApplying(null);
      }}>提交报名</Button>}>
        <Text>{applying?.required_team_size === 2 ? '本单为双陪套餐，需要两名陪玩组队报名。队友本人确认、老板选定后正式接单；双方份额按服务成交金额及本单冻结分账规则核算，手续费不参与分成。' : '本单为单陪套餐，仅支持本人报名。老板选定后正式接单。'}</Text>
        <View className={styles.attendance}><Text>本单报名人数</Text><Text className={styles.statusTag}>{applying?.required_team_size === 2 ? '双人组队' : '单人报名'}</Text></View>
        {teamSize === 2 && <View><Text>{teammateLoading ? '正在加载可组队陪玩…' : teammates.length ? '选择另一名陪玩（等级从高到低）' : '暂无符合本单等级、技能和通行证时间的队友'}</Text><Picker mode='selector' disabled={acting || teammateLoading || !teammates.length} range={teammates.map(item => item.nickname + ' · ' + item.level)} value={Math.max(0, partnerIndex)} onChange={event => setPartnerIndex(Number(event.detail.value))}><Button disabled={acting || teammateLoading || !teammates.length}>{teammates[partnerIndex]?.nickname || '请选择队友'}</Button></Picker></View>}
      </SurfaceDialog>
      <ReplyDialog
        open={!!rejecting}
        title='拒单原因'
        placeholder='请输入拒单原因'
        hint={rejecting?.dispatch_route === 'STAFF' ? '拒单后订单将退回客服重新安排，不进入公开接单池' : '拒单后订单将重新进入待接单池'}
        submitText='确认拒单'
        maxLength={200}
        submitting={acting}
        onClose={() => setRejecting(null)}
        onSubmit={async content => {
          if (!rejecting) return;
          const order = rejecting;
          setRejecting(null);
          await runAction(() => rejectOrder(order.id, content), res => res.msg || '已拒单', '拒单结果未确认，请刷新已接单核对');
        }}
      />
      <ReplyDialog
        open={!!replying}
        title={`回复 ${replying?.customer_name || '老板'} 的评价`}
        initialValue={replying?.reply_content || ''}
        placeholder='感谢老板的认可，也可以说明本次服务情况…'
        submitting={acting}
        blocked={!!replyConflict}
        hint={replyConflict || undefined}
        onClose={() => { setReplying(null); if (replyConflict) void loadEvaluations(); setReplyConflict(''); }}
        onSubmit={handleReply}
      />
    </View></PlatformLayout>
  );
};

export default OrdersPage;
