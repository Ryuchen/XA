import { fetchEscortMe,ProviderQualification } from '@/services/user';
import { getSessionRevision } from '@/utils/auth';
import { networkSnapshot, cooldownRemaining, pendingNetworkReport, acknowledgeNetworkReport } from '@/utils/orderNetwork';
import { request, ApiResponse } from '@/utils/request';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Button, Picker } from '@tarojs/components';
import Taro, { useDidShow, useDidHide, usePullDownRefresh } from '@tarojs/taro';
import {
  fetchOrders,
  grabOrder, fetchTeammates, applicationAction, Teammate,
  startOrder,
  completeOrder,
  rejectOrder,
  fetchProviderStats,
  fetchMyEvaluations,
  replyEvaluation,
} from '@/services/order';
import { Evaluation, ProviderOrder, ProviderStats, statusTextMap, REJECT_REASONS } from '@/types/order';
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

type TabKey = 'pending' | 'grabbed' | 'in_progress' | 'completed';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'pending', label: '抢单池' },
  { key: 'grabbed', label: '已接单' },
  { key: 'in_progress', label: '服务中' },
  { key: 'completed', label: '已完成' },
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
  const [qualifications,setQualifications]=useState<ProviderQualification[]>([]);
  const [scopeIndex,setScopeIndex]=useState(0);
  const scopeRef=useRef<ProviderQualification|null>(null);
  useEffect(()=>{fetchEscortMe().then(r=>{if(r.code===0 && r.data)setQualifications((r.data.qualifications||[]).filter(q=>q.is_active))}).catch(()=>{})},[]);
  const filterOrders=(rows:ProviderOrder[])=>rows.filter(o=>{const q=scopeRef.current,r=o.provider_requirement;return !q || (r && r.game_category_id===q.game_category && r.provider_kind===q.provider_kind && r.platform===q.platform && r.security_level===q.security_level)});
  const [orders, setOrders] = useState<ProviderOrder[]>([]);
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
  const [replying, setReplying] = useState<Evaluation | null>(null);
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
    try {
      const res = await fetchMyEvaluations();
      if (res.code === 0 && res.data) setEvaluations(res.data.list || []);
    } catch { /* 评价失败不阻塞订单操作 */ }
  }, []);

  const loadOrders = useCallback(async (tab: TabKey, quiet = false) => {
    if (tab === 'pending' && cooldownRemaining('pool', getSessionRevision())) return;
    const sequence = ++loadSequence.current;
    if (!quiet) setLoading(true);
    try {
      const res = await fetchOrders({ status: tab, role: 'provider' });
      if (res.traffic_mode) setTrafficMode(res.traffic_mode);
      if (sequence !== loadSequence.current || tab !== activeTabRef.current) return;
      if (res.code === 0 && res.data) {
        setOrders(filterOrders(res.data));
        setLoadError('');
      } else {
        setLoadError(res.msg || '订单暂时无法加载，请重试');
      }
    } catch (error) {
      if (sequence === loadSequence.current) setLoadError(error instanceof Error ? error.message : '订单加载失败');
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
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
    const off = wsService.on('order_status_update', data => {
      if (!visible.current) return;
      if (activeTabRef.current !== 'pending') refresh.request();
      // 公共池变化不改变本人的服务中数量和收入；本人订单完整通知才刷新看板。
      if (isPersonalOrderUpdate(data)) refreshStats.request();
    });
    const offConnected = wsService.on('connected', () => {
      if (!visible.current) return;
      // Pub/sub cannot replay events missed while offline. Re-read every tab.
      if (activeTabRef.current !== 'pending') refresh.request();
      refreshStats.request();
      loadPass();
      loadEvaluations();
    });
    // App-wide monitoring also updates the pool while the workbench is open.
    const offPool = orderAlerts.onPoolUpdate(response => {
      if (!visible.current || activeTabRef.current !== 'pending') return;
      setOrders(filterOrders(response.data || []));
      setLoadError('');
      if (response.traffic_mode) setTrafficMode(response.traffic_mode);
    });
    return () => {
      loadSequence.current++;
      off();
      offConnected();
      refresh.dispose();
      refreshStats.dispose();
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
    loadOrders(activeTabRef.current);
    loadStats();
    loadPass();
    loadEvaluations();
  });

  usePullDownRefresh(async () => {
    await Promise.all([loadOrders(activeTabRef.current), loadStats(), loadPass(), loadEvaluations()]);
    Taro.stopPullDownRefresh();
  });

  const runAction = async (fn: () => Promise<{ code: number; msg?: string }>, successText: string) => {
    if (actionLock.current) return false;
    actionLock.current = true;
    setActing(true);
    Taro.showLoading({ title: '处理中' });
    try {
      const res = await fn();
      if (res.code === 0) {
        Taro.showToast({ title: successText, icon: 'success' });
        await Promise.all([loadOrders(activeTabRef.current), loadStats()]);
        return true;
      } else {
        Taro.showToast({ title: res.msg || '操作失败', icon: 'none' });
        return false;
      }
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : '操作失败', icon: 'none' });
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
    if (!order.requires_boss_selection) return runAction(() => grabOrder(order.id), '接单成功');
    const sequence = ++applicationSequence.current;
    setTeamSize(1); setPartnerIndex(-1); setTeammates([]); setApplying(order); setTeammateLoading(true);
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
    runAction(() => startOrder(order.id, path), '已开始服务');
  };
  const handleComplete = async (order: ProviderOrder) => {
    if (acting) return;
    const path = await chooseImage();
    if (!path) return;
    runAction(() => completeOrder(order.id, path), '订单已完成');
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
          runAction(() => rejectOrder(order.id, reason), '已拒单');
        }
      },
    });
  };

  const handleReply = async (text: string) => {
    if (!replying) return;
    const evaluation = replying;
    setReplying(null);
    await runAction(() => replyEvaluation(evaluation.id, text), '回复已发布');
    await loadEvaluations();
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
        {item.is_partner && !item.partner_confirmed && <Button size='mini' disabled={acting} onClick={() => runAction(() => applicationAction(order.id, item.id, 'CONFIRM_TEAM'), '队伍已确认')}>确认组队</Button>}
        <Button size='mini' disabled={acting} onClick={() => runAction(() => applicationAction(order.id, item.id, 'WITHDRAW'), '已撤回报名')}>撤回报名</Button>
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

  return (
    <PlatformLayout active='orders'><View className={`xa-provider-page ${styles.container}`}>
      <PageHeading title='接单工作台' hint='查看订单，安排今天的服务' action={<Button className={styles.skillButton} onClick={() => Taro.navigateTo({ url: '/pages/skills/index' })}>我的技能 ›</Button>} />
      <View className={styles.dashboard}>
        <View className={styles.dashboardTop}>
          <View><Text className={styles.eyebrow}>今日接单</Text><Text className={styles.todayValue}>{stats?.today_orders || 0}<Text className={styles.todayUnit}> 单</Text></Text></View>
          <View className={styles.liveBadge}><View className={styles.liveDot} /><Text>{stats?.serving || 0} 单服务中</Text></View>
        </View>
        <View className={styles.dashboardBottom}>
          <View><Text className={styles.metricLabel}>完成率</Text><Text className={styles.metricValue}>{stats?.completion_rate || 0}%</Text></View>
          <View><Text className={styles.metricLabel}>累计收益</Text><Text className={styles.metricValue}>{formatXaCoin(stats?.total_income || 0)}币</Text></View>
        </View>
      </View>

      {passData && <View className={`${styles.passCard} ${passData.active_tier ? styles.passActive : ''}`} onClick={handleBuyPass}>
        <View className={styles.passEmblem}>{passData.active_tier ? passData.active_tier_display.slice(0, 1) : '卡'}</View>
        <View className={styles.passInfo}>
          <View className={styles.passTitleRow}><Text className={styles.passName}>{passData.active_tier_display}</Text><Text className={styles.passPill}>{passData.active_tier ? '生效中' : '未开通'}</Text></View>
          <Text className={styles.passMeta}>{passData.active_tier ? `有效至 ${new Date(passData.expires_at || '').toLocaleString()}` : '升级通行证，更早看到优质订单'}</Text>
        </View>
        <View className={styles.passAction}>{passData.active_tier ? '续费' : '采购'} <Text>›</Text></View>
      </View>}

      <View className={styles.tabs}>
        {TABS.map(tab => (
          <View
            key={tab.key}
            className={`${styles.tabItem} ${activeTab === tab.key ? styles.tabItemActive : ''}`}
            onClick={() => setActiveTab(tab.key)}
          >
            <Text className={styles.tabText}>{tab.label}</Text>
          </View>
        ))}
      </View>

      <View className={styles.scopeBar}><Picker range={['全部接单资格',...qualifications.map(q=>q.label)]} value={scopeIndex} onChange={e=>{const n=Number(e.detail.value);setScopeIndex(n);scopeRef.current=n ? qualifications[n-1] : null;void loadOrders(activeTabRef.current)}}><View>接单范围：{scopeIndex ? qualifications[scopeIndex-1]?.label : '全部接单资格'} ▾</View></Picker></View>
      {activeTab === 'pending' && <View className={styles.networkPanel}>
        <Text className={styles.networkTitle}>抢单网络 · {network.stale ? '等待采样' : network.failures || network.jitter_ms > 300 ? '连接波动' : network.server_errors ? '服务异常' : network.grab_wait || network.pool_wait ? '限流等待' : '连接稳定'}{trafficMode ? ` · ${({ peak: '波峰', valley: '波谷', normal: '常规' } as Record<string, string>)[trafficMode] || trafficMode}` : ''}</Text>
        <Text>近一分钟 {network.samples} 次 · 平均 {network.average_ms}ms · P95 {network.p95_ms}ms · 抖动 {network.jitter_ms}ms</Text>
        <Text>网络失败 {network.failure_percent}% · 服务异常 {network.server_errors} 次 · 限流 {network.limited} 次{network.grab_wait ? ` · 抢单等待 ${network.grab_wait}s` : ''}{network.pool_wait ? ` · 刷新等待 ${network.pool_wait}s` : ''}</Text>
        {network.failures > 0 && <Button className={styles.skillButton} onClick={() => setActiveTab('grabbed')}>查看已接单，核对抢单结果</Button>}
        <Text className={styles.networkHint}>数据来自订单请求的往返耗时，包含服务端处理时间。抢单超时后请先查看已接单，确认结果后重试。</Text>
      </View>}
      {!!loadError && <View className={styles.loadError}><Text>{loadError}</Text><Button className={styles.skillButton} onClick={() => loadOrders(activeTab)}>重新加载</Button></View>}
      <View className={styles.listSummary}><Text>{TABS.find(tab => tab.key === activeTab)?.label}</Text><Text>{loading ? '正在加载' : loadError ? '连接异常' : `${orders.length} 笔订单`}</Text></View>
      <View className={styles.list}>
        {loading ? (
          <Skeleton variant="card-list" rows={3} />
        ) : loadError ? null : orders.length === 0 ? (
          <View className={styles.emptyPanel}><Empty icon="🎮" title={activeTab === 'pending' ? '暂时没有可接订单' : '暂无此类订单'} desc={activeTab === 'pending' ? (reachedLimit ? `已达同时进行 ${maxConcurrent} 单上限，完成订单后可继续抢单` : '订单会自动更新。可在「我的技能」中检查服务范围。') : '订单状态更新后，会出现在对应列表中。'}>
            <Button className={styles.skillButton} onClick={() => loadOrders(activeTab)}>刷新订单</Button>
          </Empty></View>
        ) : (
          orders.map(order => {
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
                <View className={styles.serviceBlock}><Text className={styles.serviceName}>{order.service?.name || '陪玩服务'}</Text>{order.provider_requirement?.label && <Text className={styles.orderScope}>{order.provider_requirement.label}</Text>}
                  <Text className={styles.orderNo}>{order.order_no}</Text>{order.fulfillment && <Text className={styles.orderNo}>{order.fulfillment}</Text>}</View>
                <View className={styles.priceBlock}><Text className={styles.pricePrefix}>打手单钱</Text><Text className={styles.amountValue}>{formatXaCoin(order.expected_income)}币</Text></View>
              </View>
              <View className={styles.chips}>
                <Text className={`${styles.statusTag} ${styles[`status_${order.status}`]}`}>{statusTextMap[order.status]}</Text>
                <Text className={styles.chip}>{order.game_rounds} 局</Text>
                {!!order.participants?.length && <Text className={styles.chip}>{order.participants.length === 2 ? '两人服务' : '一人服务'}</Text>}
                {order.provider_gender && order.provider_gender !== 'ANY' && <Text className={styles.chip}>{order.provider_gender === 'MALE' ? '男陪' : '女陪'}</Text>}
                {!!order.game_region && <Text className={styles.chip}>{order.game_region}</Text>}
                {showDiscount && (
                  <Text
                    className={styles.discountChip}
                    style={vipColor ? { color: vipColor, background: hexToRgba(vipColor, 0.12) || undefined } : undefined}
                  >{bossType!.discount_rate}折 VIP价</Text>
                )}
              </View>
              {!!order.participants?.length && <View className={styles.infoRow}><Text className={styles.infoLabel}>本单陪玩</Text><Text className={styles.infoValue}>{order.participants.map(member => member.nickname).join(' + ')}</Text></View>}
              {!!order.game_nickname && (
                <View className={styles.infoRow}>
                  <Text className={styles.infoLabel}>老板游戏昵称</Text>
                  <Text className={styles.infoValue}>{order.game_nickname}</Text>
                </View>
              )}
              <View className={styles.incomePanel}>
                <View><Text className={styles.incomeLabel}>按本单实际收益结算</Text><Text className={styles.incomeHint}>单钱已扣平台服务费，报单审核通过后入账</Text></View>
              </View>
              <View className={styles.requirement}>
                <Text className={styles.requirementLabel}>老板备注要求</Text>
                <Text className={`${styles.requirementText} ${!order.remark ? styles.requirementEmpty : ''}`}>{order.remark || '老板暂无额外要求'}</Text>
              </View>
              {order.status === 'COMPLETED' && (() => {
                const evaluation = evaluations.find(item => item.order === order.id);
                return evaluation ? <View className={styles.evaluation}>
                  <View className={styles.evaluationHead}><Text className={styles.evaluationTitle}>老板评价</Text><Text className={styles.evaluationScore}>{'★'.repeat(Math.round(evaluation.score))} {evaluation.score.toFixed(1)}</Text></View>
                  <Text className={styles.evaluationContent}>{evaluation.content || '老板未填写文字评价'}</Text>
                  {evaluation.reply_content ? <View className={styles.myReply}><Text className={styles.replyLabel}>我的回复</Text><Text className={styles.replyText}>{evaluation.reply_content}</Text></View>
                    : <Button className={styles.replyButton} disabled={acting} onClick={() => setReplying(evaluation)}>回复老板</Button>}
                </View> : <View className={styles.awaitingReview}><Text>等待老板评价</Text><Text>评价后可在此直接回复</Text></View>;
              })()}
              {['GRABBED', 'IN_SERVICE', 'COMPLETED'].includes(order.status) && <Button className={styles.reportButton} size='mini' disabled={acting} onClick={async () => {
                if (acting) return;
                setActing(true);
                try {
                  const res = await createReport(order.id);
                  if (res.code !== 0) throw new Error(res.msg);
                  await Taro.switchTab({ url: '/pages/report/index' });
                } catch (error) { Taro.showToast({ title: (error as Error).message || '报单打开失败', icon: 'none' }); }
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
        if (!applying || (teamSize === 2 && !teammates[partnerIndex])) return;
        const order = applying;
        const success = await runAction(() => grabOrder(order.id, teamSize, teamSize === 2 ? teammates[partnerIndex].id : undefined), '报名已提交');
        if (success) setApplying(null);
      }}>提交报名</Button>}>
        <Text>请选择本次服务人数，老板选定后才正式接单。两人组由队友本人确认，按本单已付总金额均分结算。</Text>
        <View className={styles.actions}><Button disabled={acting} onClick={() => setTeamSize(1)}>{teamSize === 1 ? '✓ ' : ''}我一个人</Button><Button disabled={acting} onClick={() => setTeamSize(2)}>{teamSize === 2 ? '✓ ' : ''}我们两个人</Button></View>
        {teamSize === 2 && <View><Text>{teammateLoading ? '正在加载可组队陪玩…' : teammates.length ? '选择另一名陪玩（等级从高到低）' : '暂无符合本单等级、技能和通行证时间的队友'}</Text><Picker mode='selector' disabled={acting || teammateLoading || !teammates.length} range={teammates.map(item => item.nickname + ' · ' + item.level)} value={Math.max(0, partnerIndex)} onChange={event => setPartnerIndex(Number(event.detail.value))}><Button disabled={acting || teammateLoading || !teammates.length}>{teammates[partnerIndex]?.nickname || '请选择队友'}</Button></Picker></View>}
      </SurfaceDialog>
      <ReplyDialog
        open={!!rejecting}
        title='拒单原因'
        placeholder='请输入拒单原因'
        hint='拒单后订单将重新进入待接单池'
        submitText='确认拒单'
        maxLength={200}
        submitting={acting}
        onClose={() => setRejecting(null)}
        onSubmit={async content => {
          if (!rejecting) return;
          const order = rejecting;
          setRejecting(null);
          await runAction(() => rejectOrder(order.id, content), '已拒单');
        }}
      />
      <ReplyDialog
        open={!!replying}
        title={`回复 ${replying?.customer_name || '老板'} 的评价`}
        initialValue={replying?.reply_content || ''}
        placeholder='感谢老板的认可，也可以说明本次服务情况…'
        submitting={acting}
        onClose={() => setReplying(null)}
        onSubmit={handleReply}
      />
    </View></PlatformLayout>
  );
};

export default OrdersPage;
