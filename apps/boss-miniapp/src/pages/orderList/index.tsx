import React, { useMemo, useState, useEffect, useCallback, useRef } from 'react';
import { Button, View, Text, Image, ScrollView, Textarea } from '@tarojs/components';
import Taro, { useDidHide, useDidShow, usePullDownRefresh, useRouter } from '@tarojs/taro';
import {
  fetchOrders,
  evaluateOrder,
  cancelOrder,
  tipOrder,
  fetchAfterSale, submitAfterSale, newAfterSaleRequestId, AfterSaleRecord,
} from '@/services/order';
import { wsService } from '@/services/websocket';
import { useCatalogStore, useOrderStore, useServices, useWalletStore } from '@/store';
import { formatXaCoin } from '@xa/money';
import {
  EscortOrderStatus,
  EscortOrder,
  ServiceInfo,
  statusTextMap,
  normalizeStatus,
  CANCEL_REASONS,
} from '@/types/order';
import { Skeleton } from '@/components';
import Icon, { IconName } from '@/components/Icon';
import { resolveImageUrl } from '@/utils/media';
import RouletteWheel from '@/components/RouletteWheel';
import styles from './index.module.scss';
import { useLoginGuard } from '@/hooks/useLoginGuard';
import { getSessionRevision, getStoredToken } from '@/utils/auth';
import { createCoalescedRefresh } from '@/utils/coalescedRefresh';
import { openWecomCustomerService } from '@/services/support';

const statusTabs: Array<{ label: string; value: 'all' | EscortOrderStatus }> = [
  { label: '全部', value: 'all' },
  { label: '待接单', value: 'pending' },
  { label: '已接单', value: 'grabbed' },
  { label: '服务中', value: 'in_progress' },
  { label: '已完成', value: 'completed' },
  { label: '已取消', value: 'cancelled' },
];

const statusHintMap: Partial<Record<EscortOrderStatus, string>> = {
  pending: '订单已提交，等待大神接单中。如长时间未接单可取消并退款。',
  grabbed: '大神已接单，请留意约定时间并保持联系。',
  in_progress: '服务进行中，如有问题可联系平台客服。',
  completed: '服务已完成，欢迎评价本次体验。',
  cancelled: '订单已取消，款项已原路退回钱包。',
};

const statusIconMap: Record<EscortOrderStatus, IconName> = {
  pending: 'timer',
  grabbed: 'check-circle',
  in_progress: 'gamepad-2',
  completed: 'badge-check',
  cancelled: 'info',
};

const statusColorMap: Record<EscortOrderStatus, string> = {
  pending: '#FF9500',
  grabbed: '#002fa7',
  in_progress: '#002fa7',
  completed: '#34C759',
  cancelled: '#AEAEB2',
};

const OrderListPage: React.FC = () => {
  const router = useRouter();
  const focusedOrderId = Number(router.params.orderId || 0) || null;
  const initialStatus = statusTabs.some(tab => tab.value === router.params.status)
    ? router.params.status as 'all' | EscortOrderStatus
    : 'all';
  const [activeStatus, setActiveStatus] = useState<'all' | EscortOrderStatus>(initialStatus);
  const [orders, setOrders] = useState<EscortOrder[]>([]);
  const [loadingOrderId, setLoadingOrderId] = useState<number | null>(null);
  const [evaluatingOrder, setEvaluatingOrder] = useState<EscortOrder | null>(null);
  const [evalScore, setEvalScore] = useState(5);
  const [evalContent, setEvalContent] = useState('');
  const [loading, setLoading] = useState(true);
  const [expandedOrderId, setExpandedOrderId] = useState<number | null>(focusedOrderId);
  const [wheelOrderId, setWheelOrderId] = useState<number | null>(null);
  const [afterSale, setAfterSale] = useState<AfterSaleRecord | null>(null);
  const [afterSaleError, setAfterSaleError] = useState('');
  const [afterSaleLoading, setAfterSaleLoading] = useState(false);
  const [afterSaleOrder, setAfterSaleOrder] = useState<EscortOrder | null>(null);
  const [afterSaleReason, setAfterSaleReason] = useState('');
  const alive = useRef(true), visible = useRef(true), requestSequence = useRef(0), detailSequence = useRef(0);
  const expandedRef = useRef(expandedOrderId), ordersRef = useRef<EscortOrder[]>([]);
  const session = useRef(getSessionRevision()), afterSaleBusy = useRef(false);
  const afterSaleRequestId = useRef('');
  expandedRef.current = expandedOrderId;
  const loadAfterSale = useCallback(async (orderId: number) => {
    const sequence = ++detailSequence.current, revision = getSessionRevision();
    const current = () => alive.current && sequence === detailSequence.current && revision === getSessionRevision() && expandedRef.current === orderId;
    setAfterSaleLoading(true); setAfterSaleError('');
    try {
      const response = await fetchAfterSale(orderId);
      if (!current()) return;
      if (response.code !== 0) throw Error(response.msg || '售后详情加载失败');
      setAfterSale(response.data || null);
    } catch (error) { if (current()) { setAfterSale(null); setAfterSaleError(error instanceof Error ? error.message : '售后详情加载失败'); } }
    finally { if (current()) setAfterSaleLoading(false); }
  }, []);

  useEffect(() => {
    ++detailSequence.current; setAfterSale(null); setAfterSaleError(''); setAfterSaleLoading(false);
    if (expandedOrderId && ordersRef.current.find(order => order.id === expandedOrderId)?.aftersale_status) void loadAfterSale(expandedOrderId);
  }, [expandedOrderId, loadAfterSale]);
  // 打赏礼物列表来自全局目录缓存，与「陪玩」页共用
  const giftOptions = useServices('GIFT');
  const { ensureLogin, loginSheet } = useLoginGuard();

  // 用于检测订单状态变化，触发评价引导
  const previousStatusesRef = useRef<Map<number, EscortOrderStatus>>(new Map());

  const loadOrders = useCallback(async () => {
    if (!alive.current || !getStoredToken()) return;
    const sequence = ++requestSequence.current, revision = getSessionRevision();
    const current = () => alive.current && sequence === requestSequence.current && revision === getSessionRevision();
    setLoading(true);
    try {
      const res = await fetchOrders({ role: 'customer' });
      if (!current()) return;
      if (res.code !== 0) throw Error(res.msg || '订单加载失败');
      const normalized = (res.data || []).map(order => ({
        ...order,
        status: normalizeStatus(order.status),
      })) as EscortOrder[];

      // 检测状态变化：in_progress → completed，自动唤起评价
      const newlyCompleted = normalized.find(order => {
        const prev = previousStatusesRef.current.get(order.id);
        return prev && prev !== 'completed' && order.status === 'completed';
      });
      if (newlyCompleted) {
        setTimeout(() => { if (current() && visible.current) startEvaluate(newlyCompleted); }, 600);
      }

      previousStatusesRef.current = new Map(normalized.map(o => [o.id, o.status]));
      setOrders(normalized);
      ordersRef.current = normalized;
      const expanded = normalized.find(order => order.id === expandedRef.current);
      if (expanded?.aftersale_status) void loadAfterSale(expanded.id);
      else { ++detailSequence.current; setAfterSale(null); setAfterSaleError(''); setAfterSaleLoading(false); }
    } catch {
      // silently fail
    } finally {
      if (current()) setLoading(false);
    }
  }, [loadAfterSale]);

  // 打赏礼物目录只需挂载时取一次（命中缓存则不发请求）
  useEffect(() => {
    if (getStoredToken()) void useCatalogStore.getState().loadServices('GIFT');
  }, []);

  // 订单列表统一由 useDidShow 驱动，首次进入也会触发，避免首屏双份请求
  useDidShow(() => {
    visible.current = true;
    if (getStoredToken()) void loadOrders();
    else setLoading(false);
  });
  useDidHide(() => { visible.current = false; });

  usePullDownRefresh(async () => {
    if (getStoredToken()) await loadOrders();
    Taro.stopPullDownRefresh();
  });

  useEffect(() => {
    alive.current = true;
    const refresh = createCoalescedRefresh(async () => { if (visible.current) await loadOrders(); });
    const unsubscribe = wsService.on('order_status_update', (payload: { status: string; order_no?: string }) => {
      if (!visible.current || !getStoredToken()) return;
      const status = normalizeStatus(payload?.status || '');
      const tipMap: Partial<Record<EscortOrderStatus, string>> = {
        grabbed: '大神已接单',
        in_progress: '大神已开始服务',
        completed: '服务已完成，请评价',
        cancelled: '订单已取消',
      };
      const tip = tipMap[status];
      if (tip) {
        Taro.showToast({ title: tip, icon: 'none', duration: 1500 });
      }
      // 订单状态变了，「我的」页的统计与余额缓存同步失效
      useOrderStore.getState().invalidate();
      useWalletStore.getState().invalidate();
      ++requestSequence.current; setLoading(true); refresh.request();
    });
    const offConnected = wsService.on('connected', () => { if (visible.current && getStoredToken()) refresh.request(); });
    const onSession = () => {
      if (session.current === getSessionRevision()) return;
      session.current = getSessionRevision(); ++requestSequence.current; ++detailSequence.current;
      ordersRef.current = []; previousStatusesRef.current.clear();
      setOrders([]); setAfterSale(null); setAfterSaleError(''); setAfterSaleLoading(false); setExpandedOrderId(null); expandedRef.current = null;
      setEvaluatingOrder(null); setLoadingOrderId(null); afterSaleBusy.current = false;
      setAfterSaleOrder(null); setAfterSaleReason('');
      afterSaleRequestId.current = '';
      if (visible.current && getStoredToken()) void loadOrders(); else setLoading(false);
    };
    Taro.eventCenter.on('xa:auth.changed', onSession);
    return () => { alive.current = false; ++requestSequence.current; ++detailSequence.current; unsubscribe(); offConnected(); refresh.dispose(); Taro.eventCenter.off('xa:auth.changed', onSession); };
  }, [loadOrders]);

  const visibleOrders = useMemo(() => {
    if (activeStatus === 'all') return orders;
    return orders.filter(order => order.status === activeStatus);
  }, [activeStatus, orders]);
  const afterSaleHistory = (Array.isArray(afterSale?.history) ? afterSale.history : []).filter(item => item.status !== 'OPEN' && (afterSale?.client_request_id ? item.client_request_id !== afterSale.client_request_id : !afterSale?.created_at || Date.parse(item.created_at) !== Date.parse(afterSale.created_at)));

  const statusCounts = useMemo(() => orders.reduce<Record<string, number>>((acc, order) => {
    acc[order.status] = (acc[order.status] || 0) + 1;
    return acc;
  }, {}), [orders]);

  const activeCount = (statusCounts.pending || 0) + (statusCounts.grabbed || 0) + (statusCounts.in_progress || 0);
  const totalAmount = orders
    .filter(order => order.status !== 'cancelled')
    .reduce((sum, order) => sum + Number(order.refund_summary?.remaining_amount ?? order.amount ?? 0), 0);

  const formatDateTime = (value: string) => {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
  };

  const handleCancelOrder = async (order: EscortOrder) => {
    const revision = getSessionRevision();
    // 1. 选取消原因（ActionSheet）
    let chosenReason = '';
    try {
      const sheet = await Taro.showActionSheet({
        itemList: [...CANCEL_REASONS],
      });
      chosenReason = CANCEL_REASONS[sheet.tapIndex] || '';
    } catch {
      return; // 用户取消
    }

    // 2. "其他"则展开自由输入
    if (chosenReason === '其他') {
      const modal = await Taro.showModal({
        title: '请输入取消原因',
        editable: true,
        placeholderText: '简要描述取消原因（可选）',
      } as Taro.showModal.Option);
      if (!modal.confirm) return;
      chosenReason = (modal as { content?: string }).content || '其他';
    }

    // 3. 二次确认 + 退款金额提示
    const confirm = await Taro.showModal({
      title: '确认取消订单？',
      content: `本次取消将退回 ${formatXaCoin(order.amount)} 兴安币到您的钱包。`,
      confirmText: '确认取消',
      confirmColor: '#FF4D4F',
      cancelText: '再想想',
    });
    if (!confirm.confirm || !alive.current || !visible.current || revision !== getSessionRevision()) return;

    // 4. 调用 API
    try {
      setLoadingOrderId(order.id);
      const res = (await cancelOrder(order.id, chosenReason)) as { code?: number; msg?: string };
      if (!alive.current || revision !== getSessionRevision()) return;
      if (res?.code === 0) {
        Taro.showToast({ title: '已取消，款项原路退回', icon: 'success' });
        useOrderStore.getState().invalidate();
        useWalletStore.getState().invalidate();
        void loadOrders();
      } else {
        Taro.showToast({ title: res?.msg || '取消失败', icon: 'none' });
      }
    } catch {
      Taro.showToast({ title: '取消失败', icon: 'none' });
    } finally {
      if (alive.current && revision === getSessionRevision()) setLoadingOrderId(null);
    }
  };

  const canAfterSale = (order: EscortOrder) => order.can_aftersale === true && order.status === 'completed'
    && ['PAID', 'PARTIALLY_REFUNDED'].includes(order.payment_status) && order.aftersale_status !== 'REFUNDED';
  const handleAfterSale = (order: EscortOrder) => {
    if (afterSaleBusy.current || loading || !canAfterSale(order)) return;
    afterSaleRequestId.current = newAfterSaleRequestId();
    setAfterSaleOrder(order); setAfterSaleReason('');
  };
  const handleSubmitAfterSale = async () => {
    const order = afterSaleOrder;
    if (!order || afterSaleBusy.current || loading || !alive.current || !visible.current) return;
    const reason = afterSaleReason.trim();
    if (reason.length < 2 || reason.length > 1000) { Taro.showToast({ title: '请填写2至1000字问题说明', icon: 'none' }); return; }
    if (!ordersRef.current.some(row => row.id === order.id && canAfterSale(row))) { setAfterSaleOrder(null); return; }
    const revision = getSessionRevision(); afterSaleBusy.current = true;
    try {
      setLoadingOrderId(order.id);
      const response = await submitAfterSale(order.id, reason, afterSaleRequestId.current);
      if (!alive.current || revision !== getSessionRevision()) return;
      if (response.code !== 0) throw Error(response.msg || '售后提交失败');
      setAfterSaleOrder(null); setAfterSaleReason('');
      afterSaleRequestId.current = '';
      Taro.showToast({ title: '售后已提交', icon: 'success' }); await loadOrders();
    } catch (error) { if (alive.current && revision === getSessionRevision()) Taro.showToast({ title: error instanceof Error ? error.message : '售后提交失败', icon: 'none' }); }
    finally { if (alive.current && revision === getSessionRevision()) { afterSaleBusy.current = false; setLoadingOrderId(null); } }
  };

  const handleContactSupport = async (order?: EscortOrder) => {
    Taro.showLoading({ title: '正在连接客服' });
    const result = await openWecomCustomerService({
      orderId: order?.id,
      title: order ? `订单 ${order.order_no} 咨询` : '订单售后咨询',
      path: order
        ? `/pages/orderList/index?orderId=${order.id}`
        : '/pages/orderList/index',
    });
    Taro.hideLoading();
    if (!result.opened) Taro.navigateTo({ url: `/pages/serviceCard/index${order ? `?orderId=${order.id}` : ''}` });
  };

  const startEvaluate = (order: EscortOrder) => {
    setEvaluatingOrder(order);
    setEvalScore(5);
    setEvalContent('');
  };

  const handleSubmitEval = async () => {
    if (!evaluatingOrder) return;
    try {
      const res = (await evaluateOrder({
        order_id: evaluatingOrder.id,
        score: evalScore,
        content: evalContent || undefined,
      })) as { code?: number; msg?: string };
      if (res?.code === 0) {
        Taro.showToast({ title: '评价成功', icon: 'success' });
        setEvaluatingOrder(null);
        void loadOrders();
      } else {
        Taro.showToast({ title: res?.msg || '评价失败', icon: 'none' });
      }
    } catch {
      Taro.showToast({ title: '评价失败', icon: 'none' });
    }
  };

  const handleTip = async (order: EscortOrder) => {
    if (loadingOrderId != null) return;
    if (giftOptions.length === 0) {
      Taro.showToast({ title: '暂无可用礼物，请联系平台配置', icon: 'none' });
      return;
    }
    let gift: ServiceInfo | undefined;
    try {
      const result = await Taro.showActionSheet({
        itemList: giftOptions.map(item => `${item.name} · ${formatXaCoin(item.price)}兴安币`),
      });
      gift = giftOptions[result.tapIndex];
    } catch {
      return;
    }
    if (!gift) return;
    const confirmation = await Taro.showModal({
      title: `送给${order.provider?.nickname || '陪玩'}`,
      content: `确认赠送「${gift.name}」？将从钱包扣除 ${formatXaCoin(gift.price)} 兴安币，礼物会即时到账。`,
      confirmText: '确认赠送',
      confirmColor: '#FF2D55',
    });
    if (!confirmation.confirm) return;
    try {
      setLoadingOrderId(order.id);
      const response = await tipOrder(order.id, gift.id);
      if (response.code === 0) {
        Taro.showToast({ title: '礼物已送达', icon: 'success' });
        useWalletStore.getState().invalidate();
        await loadOrders();
      } else {
        Taro.showToast({ title: response.msg || '赠送失败', icon: 'none' });
      }
    } catch {
      Taro.showToast({ title: '赠送失败，请稍后重试', icon: 'none' });
    } finally {
      setLoadingOrderId(null);
    }
  };

  const renderActions = (order: EscortOrder) => {
    const can = (action: 'CANCEL' | 'EVALUATE' | 'TIP', fallback: boolean) =>
      Array.isArray(order.allowed_actions) ? order.allowed_actions.includes(action) : fallback;
    if (order.status === 'pending') {
      return (
        <View className={styles.actionGroup}>
          <View className={styles.secondaryBtn} onClick={() => void handleContactSupport(order)}>
            <Icon name="headphones" size={24} color="#6E6E73" className={styles.btnIcon} />
            <Text>联系客服</Text>
          </View>
          {can('CANCEL', true) && (
            <View
              className={`${styles.dangerBtn} ${loadingOrderId === order.id ? styles.disabledBtn : ''}`}
              onClick={() => {
                if (loadingOrderId === order.id) return;
                void handleCancelOrder(order);
              }}
            >
              <Text>{loadingOrderId === order.id ? '处理中...' : '取消订单'}</Text>
            </View>
          )}
        </View>
      );
    }

    if (order.status === 'completed') {
      const evaluated = order.is_evaluated;
      return (
        <View className={styles.actionGroup}>
          <View className={styles.secondaryBtn} onClick={() => void handleContactSupport(order)}>
            <Icon name="headphones" size={24} color="#6E6E73" className={styles.btnIcon} />
            <Text>联系客服</Text>
          </View>
          {can('TIP', !!order.provider && !order.service.is_gift) && (
            <View
              className={`${styles.giftBtn} ${loadingOrderId === order.id ? styles.disabledBtn : ''}`}
              onClick={() => void handleTip(order)}
            >
              <Icon name="gift" size={24} color="#fffdf5" className={styles.btnIcon} />
              <Text>{loadingOrderId === order.id ? '赠送中' : '送礼物'}</Text>
            </View>
          )}
          {can('EVALUATE', !evaluated) && (
            <View className={styles.primaryBtn} onClick={() => startEvaluate(order)}>
              <Icon name="star" size={24} color="#fffdf5" className={styles.btnIcon} />
              <Text>评价</Text>
            </View>
          )}
        </View>
      );
    }

    // grabbed / in_progress / cancelled 仅展示客服入口
    return (
      <View className={styles.actionGroup}>
        <View className={styles.secondaryBtn} onClick={() => void handleContactSupport(order)}>
          <Icon name="headphones" size={24} color="#6E6E73" className={styles.btnIcon} />
          <Text>联系客服</Text>
        </View>
      </View>
    );
  };

  return (
    <View className={styles.container}>
      <View className={styles.dashboard}>
        <View className={styles.dashboardTop}>
          <View>
            <Text className={styles.eyebrow}>MY ORDERS</Text>
            <Text className={styles.dashboardTitle}>我的订单</Text>
          </View>
          <View className={styles.liveBadge}>
            <View className={styles.liveDot} />
            <Text>{activeCount} 单进行中</Text>
          </View>
        </View>
        <View className={styles.dashboardBottom}>
          <View className={styles.metricItem}>
            <Text className={styles.metricLabel}>累计订单</Text>
            <Text className={styles.metricValue}>{orders.length} 单</Text>
          </View>
          <View className={styles.metricItem}>
            <Text className={styles.metricLabel}>已完成</Text>
            <Text className={styles.metricValue}>{statusCounts.completed || 0} 单</Text>
          </View>
          <View className={styles.metricItem}>
            <Text className={styles.metricLabel}>累计消费</Text>
            <Text className={styles.metricValue}>{formatXaCoin(totalAmount)}币</Text>
          </View>
        </View>
      </View>

      <ScrollView className={styles.tabs} scrollX>
        {statusTabs.map(tab => (
          <View
            key={tab.value}
            className={`${styles.tabItem} ${activeStatus === tab.value ? styles.active : ''}`}
            onClick={() => setActiveStatus(tab.value)}
          >
            <Text>{tab.label}</Text>
            <Text className={styles.tabCount}>
              {tab.value === 'all' ? orders.length : statusCounts[tab.value] || 0}
            </Text>
          </View>
        ))}
      </ScrollView>

      <ScrollView
        className={styles.orderList}
        scrollY
        scrollIntoView={focusedOrderId ? `order-${focusedOrderId}` : undefined}
      >
        {loading ? (
          <Skeleton variant="card-list" rows={3} />
        ) : (
          <>
        {visibleOrders.map(order => {
          const counterpart = order.provider;
          const displayStatus = order.status;
          const partialRefund = order.payment_status === 'PARTIALLY_REFUNDED';
          const refunded = order.payment_status === 'REFUNDED';
          const displayLabel = partialRefund ? '已部分退款' : statusTextMap[displayStatus];

          return (
            <View
              key={order.id}
              id={`order-${order.id}`}
              className={`${styles.orderCard} ${focusedOrderId === order.id ? styles.focusedOrderCard : ''}`}
            >
              <View className={styles.orderHeader}>
                <Text className={styles.orderNo}>订单号 {order.order_no}</Text>
                <View
                  className={`${styles.orderStatus} ${styles[`status_${displayStatus}`] || ''}`}
                >
                  <Icon
                    name={statusIconMap[displayStatus]}
                    size={24}
                    color={statusColorMap[displayStatus]}
                    className={styles.statusIcon}
                  />
                  <Text className={styles.statusLabel}>{displayLabel}</Text>
                </View>
              </View>

              <View className={styles.productRow}>
                <Image className={styles.productImage} src={resolveImageUrl(order.service.cover_url, 'https://picsum.photos/id/64/200/200')} mode="aspectFill" />
                <View className={styles.productInfo}>
                  <Text className={styles.productName}>{order.service.name}</Text>
                  <Text className={styles.productSpec}>{order.service.description}</Text>
                  <Text className={styles.metaText}>
                    大神：{counterpart?.nickname || '待匹配'}
                  </Text>
                  {!order.service.is_gift && <Text className={styles.metaText}>{order.roulette_result?.name ? '转盘服务目标：1 个' : `局数：${order.game_rounds}局`}</Text>}
                  <Text className={styles.createTime}>下单时间：{formatDateTime(order.created_at)}</Text>
                </View>
                <View className={styles.priceBlock}>
                  <Text className={styles.price}>{formatXaCoin(order.amount ?? 0)}币</Text>
                  <Text className={styles.quantity}>{displayLabel}</Text>
                </View>
              </View>

              {order.fulfillment && <View className={styles.remarkBlock}><Text className={styles.remarkLabel}>玩法要求</Text><Text className={styles.remarkText}>{order.fulfillment}</Text></View>}
              {order.gameplay_snapshot?.rules && <View className={styles.remarkBlock}><Text className={styles.remarkLabel}>约定规则</Text><Text className={styles.remarkText}>{order.gameplay_snapshot.rules}</Text></View>}
              {order.gameplay_snapshot?.pool && order.roulette_result?.name && <>
                <View className={styles.remarkBlock} onClick={() => setWheelOrderId(wheelOrderId === order.id ? null : order.id)}><Text className={styles.remarkText}>{wheelOrderId === order.id ? '收起本单转盘 ↑' : '查看本单转盘与概率 →'}</Text></View>
                {wheelOrderId === order.id && <RouletteWheel pool={order.gameplay_snapshot.pool} result={order.roulette_result} />}
              </>}
              {order.remark && (
                <View className={styles.remarkBlock}>
                  <Text className={styles.remarkLabel}>备注</Text>
                  <Text className={styles.remarkText}>{order.remark}</Text>
                </View>
              )}

              {displayStatus === 'cancelled' && order.cancel_reason && (
                <View className={styles.cancelBlock}>
                  <Text className={styles.cancelLabel}>取消原因</Text>
                  <Text className={styles.cancelText}>{order.cancel_reason}</Text>
                </View>
              )}

              <View
                className={styles.detailToggle}
                onClick={() => setExpandedOrderId(expandedOrderId === order.id ? null : order.id)}
              >
                <Text>{expandedOrderId === order.id ? '收起订单详情' : '查看订单详情'}</Text>
                <Icon name={expandedOrderId === order.id ? 'chevron-up' : 'chevron-down'} size={24} color="#002fa7" />
              </View>

              {expandedOrderId === order.id && (
                <View className={styles.detailPanel}>
                  <View className={styles.detailRow}>
                    <Text className={styles.detailLabel}>游戏大区</Text>
                    <Text className={styles.detailValue}>{order.game_region || '未填写'}</Text>
                  </View>
                  <View className={styles.detailRow}>
                    <Text className={styles.detailLabel}>游戏昵称</Text>
                    <Text className={styles.detailValue}>{order.game_nickname || '未填写'}</Text>
                  </View>
                  <View className={styles.detailRow}>
                    <Text className={styles.detailLabel}>游戏 UID</Text>
                    <Text className={styles.detailValue}>{order.game_uid || '未填写'}</Text>
                  </View>
                  <View className={styles.detailDivider} />
                  <View className={styles.detailRow}>
                    <Text className={styles.detailLabel}>商品原价</Text>
                    <Text className={styles.detailValue}>{formatXaCoin(order.original_amount || order.amount)}币</Text>
                  </View>
                  {(order.boss_discount + order.promo_discount + order.coupon_discount) > 0 && (
                    <View className={styles.detailRow}>
                      <Text className={styles.detailLabel}>合计优惠</Text>
                      <Text className={styles.discountValue}>-{formatXaCoin(order.boss_discount + order.promo_discount + order.coupon_discount)}币</Text>
                    </View>
                  )}
                  <View className={styles.detailRow}>
                    <Text className={styles.detailLabel}>{partialRefund || refunded ? '原实付金额' : '实付金额'}</Text>
                    <Text className={styles.amountValue}>{formatXaCoin(order.amount)}币</Text>
                  </View>
                  {(partialRefund || refunded) && <View className={styles.aftersalePanel}>
                    <Text className={styles.aftersaleTitle}>{partialRefund ? '部分退款已入账' : '退款已入账'}</Text>{order.refund_summary?.amount != null || refunded ? <Text>累计已退 {formatXaCoin(order.refund_summary?.amount ?? order.amount)} 兴安币已退回钱包，实际到账以退款流水为准。</Text> : <Text>退款金额以钱包流水为准。</Text>}
                    {(order.refund_summary?.remaining_amount != null || refunded) && <Text>订单剩余金额：{formatXaCoin(order.refund_summary?.remaining_amount ?? 0)} 兴安币</Text>}
                    {order.refund_summary?.records?.length ? <View className={styles.aftersaleHistory}>{order.refund_summary.records.map(record => <View className={styles.aftersaleHistoryItem} key={record.id}>
                      <Text>退款 #{record.id} · {formatXaCoin(record.amount)} 兴安币</Text><Text>{formatDateTime(record.created_at)}</Text>{record.reason && <Text>退款说明：{record.reason}</Text>}
                    </View>)}</View> : <>
                      {(order.refund_summary?.created_at || order.refunded_at) && <Text>退款时间：{formatDateTime(order.refund_summary?.created_at || order.refunded_at || '')}</Text>}
                      {order.refund_summary?.reason && <Text>退款说明：{order.refund_summary.reason}</Text>}
                    </>}
                    <Button className={styles.aftersaleButton} onClick={() => Taro.navigateTo({ url: '/pages/customer/wallet/index' })}>查看退款流水</Button>
                  </View>}
                  {(order.can_aftersale || order.aftersale_status || (!order.service.is_gift && order.aftersale_deadline)) && <View className={styles.aftersalePanel}>
                    <Text className={styles.aftersaleTitle}>售后服务 · {({ OPEN: '处理中', RESOLVED: '已解决', PARTIALLY_REFUNDED: '已部分退款', REFUNDED: '已退款' })[order.aftersale_status || ''] || '可联系订单客服'}</Text>
                    {order.aftersale_status === 'OPEN' && <Text>售后处理中，等待平台处理结果。</Text>}
                    {afterSaleLoading && <Text>正在读取售后详情…</Text>}
                    {afterSale?.reason && <Text>申请说明：{afterSale.reason}</Text>}
                    {afterSale?.resolution && <Text>处理结果：{afterSale.resolution}</Text>}
                    {!!afterSaleHistory.length && <View className={styles.aftersaleHistory}>
                      <Text className={styles.aftersaleTitle}>历史处理记录</Text>
                      {afterSaleHistory.map((item, index) => <View className={styles.aftersaleHistoryItem} key={item.client_request_id || index}>
                        <Text>{({ OPEN: '处理中', RESOLVED: '已解决', PARTIALLY_REFUNDED: '已部分退款', REFUNDED: '已退款' })[item.status]} · {formatDateTime(item.resolved_at || item.created_at)}</Text><Text>申请说明：{item.reason}</Text>{item.resolution && <Text>处理结果：{item.resolution}</Text>}
                      </View>)}
                    </View>}
                    {afterSaleError && <><Text>{afterSaleError}</Text><Button className={styles.aftersaleButton} disabled={afterSaleLoading} onClick={() => loadAfterSale(order.id)}>重新加载售后详情</Button></>}
                    {canAfterSale(order) && <Button className={styles.aftersaleButton} disabled={loading || loadingOrderId !== null} onClick={() => handleAfterSale(order)}>{['RESOLVED', 'PARTIALLY_REFUNDED'].includes(order.aftersale_status || '') ? '重新申请售后' : '提交售后问题'}</Button>}
                  </View>}
                  {order.evaluation && (
                    <View className={styles.evaluationSummary}>
                      <View className={styles.evaluationHead}>
                        <Text className={styles.evaluationTitle}>我的评价</Text>
                        <Text className={styles.evaluationScore}>{'★'.repeat(order.evaluation.score)}</Text>
                      </View>
                      <Text className={styles.evaluationContent}>{order.evaluation.content || '本次未填写文字评价'}</Text>
                      {order.evaluation.reply_content && (
                        <View className={styles.providerReply}>
                          <Text className={styles.replyLabel}>大神回复</Text>
                          <Text className={styles.replyContent}>{order.evaluation.reply_content}</Text>
                        </View>
                      )}
                    </View>
                  )}
                </View>
              )}

              <View className={styles.orderFooter}>
                <Text className={styles.hint}>
                  {partialRefund ? '订单已部分退款，剩余金额及后续售后以订单详情为准。' : statusHintMap[displayStatus] || '如有问题可联系平台客服处理。'}
                </Text>
                {renderActions(order)}
              </View>
            </View>
          );
        })}

        {visibleOrders.length === 0 && (
          <View className={styles.empty}>
            <View className={styles.emptyIconWrap}>
              <Icon name="package" size={64} color="#AEAEB2" />
            </View>
            <Text className={styles.emptyTitle}>{getStoredToken() ? '暂无相关订单' : '登录后查看订单'}</Text>
            <Text className={styles.emptyDesc}>
              {getStoredToken() ? '完成下单后可在此查看订单进度' : '使用微信快捷登录，同步你的全部订单状态'}
            </Text>
            {!getStoredToken() && (
              <View className={styles.emptyLoginBtn} onClick={() => ensureLogin(() => loadOrders())}>微信快捷登录</View>
            )}
          </View>
        )}
          </>
        )}
      </ScrollView>

      {evaluatingOrder && (
        <View className={styles.evalOverlay} onClick={() => setEvaluatingOrder(null)}>
          <View className={styles.evalPanel} onClick={e => e.stopPropagation()}>
            <Text className={styles.evalTitle}>评价订单</Text>
            <Text className={styles.evalOrderNo}>订单号: {evaluatingOrder.order_no}</Text>

            <Text className={styles.evalLabel}>评分</Text>
            <View className={styles.evalStars}>
              {[1, 2, 3, 4, 5].map(n => (
                <Icon
                  key={n}
                  name="star"
                  size={48}
                  color="#FF9500"
                  fill={n <= evalScore ? '#FF9500' : 'none'}
                  className={styles.starIcon}
                  onClick={() => setEvalScore(n)}
                />
              ))}
            </View>

            <Text className={styles.evalLabel}>评价内容（选填）</Text>
            <Textarea
              className={styles.evalInput}
              value={evalContent}
              onInput={e => setEvalContent(e.detail.value)}
              placeholder="说说你对本次服务的感受..."
              maxlength={500}
              autoHeight
            />

            <View className={styles.evalActions}>
              <View className={styles.evalCancelBtn} onClick={() => setEvaluatingOrder(null)}>
                取消
              </View>
              <View className={styles.evalSubmitBtn} onClick={handleSubmitEval}>
                提交评价
              </View>
            </View>
          </View>
        </View>
      )}
      {afterSaleOrder && <View className={styles.evalOverlay}>
        <View className={styles.evalPanel}>
          <Text className={styles.evalTitle}>提交订单售后</Text><Text className={styles.evalOrderNo}>订单号：{afterSaleOrder.order_no}</Text>
          <Text className={styles.evalLabel}>请说明问题，客服处理完成前暂停确认实销。</Text>
          <Textarea className={styles.evalInput} value={afterSaleReason} maxlength={1000} disabled={loadingOrderId !== null} placeholder='至少2字问题说明' onInput={event => setAfterSaleReason(event.detail.value)} />
          <View className={styles.evalActions}><Button className={styles.evalCancelBtn} disabled={loadingOrderId !== null} onClick={() => setAfterSaleOrder(null)}>取消</Button><Button className={styles.evalSubmitBtn} disabled={loading || loadingOrderId !== null} onClick={handleSubmitAfterSale}>提交售后</Button></View>
        </View>
      </View>}
      {loginSheet}
    </View>
  );
};

export default OrderListPage;
