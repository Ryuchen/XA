import React, { useMemo, useState, useEffect, useCallback, useRef } from 'react';
import { View, Text, Image, ScrollView, Textarea } from '@tarojs/components';
import Taro, { useDidShow, usePullDownRefresh, useRouter } from '@tarojs/taro';
import {
  fetchOrders,
  evaluateOrder,
  cancelOrder,
  fetchServices,
  tipOrder,
} from '@/services/order';
import { wsService } from '@/services/websocket';
import { formatXaCoin } from '@/utils/format';
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
import styles from './index.module.scss';
import { useLoginGuard } from '@/hooks/useLoginGuard';
import { getStoredToken } from '@/utils/auth';
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
  grabbed: '#007AFF',
  in_progress: '#007AFF',
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
  const [giftOptions, setGiftOptions] = useState<ServiceInfo[]>([]);
  const { ensureLogin, loginSheet } = useLoginGuard();

  // 用于检测订单状态变化，触发评价引导
  const previousStatusesRef = useRef<Map<number, EscortOrderStatus>>(new Map());

  const loadOrders = useCallback(async () => {
    try {
      const res = await fetchOrders({ role: 'customer' });
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
        setTimeout(() => startEvaluate(newlyCompleted), 600);
      }

      previousStatusesRef.current = new Map(normalized.map(o => [o.id, o.status]));
      setOrders(normalized);
    } catch {
      // silently fail
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (getStoredToken()) {
      void loadOrders();
      fetchServices('GIFT').then(res => {
        if (res.code === 0 && res.data) setGiftOptions(res.data);
      }).catch(() => setGiftOptions([]));
    }
    else setLoading(false);
  }, [loadOrders]);

  useDidShow(() => {
    if (getStoredToken()) void loadOrders();
  });

  usePullDownRefresh(async () => {
    if (getStoredToken()) await loadOrders();
    Taro.stopPullDownRefresh();
  });

  useEffect(() => {
    const unsubscribe = wsService.on('order_status_update', (payload: { status: string; order_no?: string }) => {
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
      void loadOrders();
    });
    return unsubscribe;
  }, [loadOrders]);

  const visibleOrders = useMemo(() => {
    if (activeStatus === 'all') return orders;
    return orders.filter(order => order.status === activeStatus);
  }, [activeStatus, orders]);

  const statusCounts = useMemo(() => orders.reduce<Record<string, number>>((acc, order) => {
    acc[order.status] = (acc[order.status] || 0) + 1;
    return acc;
  }, {}), [orders]);

  const activeCount = (statusCounts.pending || 0) + (statusCounts.grabbed || 0) + (statusCounts.in_progress || 0);
  const totalAmount = orders
    .filter(order => order.status !== 'cancelled')
    .reduce((sum, order) => sum + Number(order.amount || 0), 0);

  const formatDateTime = (value: string) => {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
  };

  const handleCancelOrder = async (order: EscortOrder) => {
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
    if (!confirm.confirm) return;

    // 4. 调用 API
    try {
      setLoadingOrderId(order.id);
      const res = (await cancelOrder(order.id, chosenReason)) as { code?: number; msg?: string };
      if (res?.code === 0) {
        Taro.showToast({ title: '已取消，款项原路退回', icon: 'success' });
        void loadOrders();
      } else {
        Taro.showToast({ title: res?.msg || '取消失败', icon: 'none' });
      }
    } catch {
      Taro.showToast({ title: '取消失败', icon: 'none' });
    } finally {
      setLoadingOrderId(null);
    }
  };

  const handleContactSupport = async (order?: EscortOrder) => {
    Taro.showLoading({ title: '正在连接客服' });
    const result = await openWecomCustomerService({
      title: order ? `订单 ${order.order_no} 咨询` : '订单售后咨询',
      path: order
        ? `/pages/orderList/index?orderId=${order.id}`
        : '/pages/orderList/index',
    });
    Taro.hideLoading();
    if (!result.opened) Taro.navigateTo({ url: '/pages/serviceCard/index' });
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
            <Icon name="headphones" size={26} color="#6E6E73" className={styles.btnIcon} />
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
            <Icon name="headphones" size={26} color="#6E6E73" className={styles.btnIcon} />
            <Text>联系客服</Text>
          </View>
          {can('TIP', !!order.provider && !order.service.is_gift) && (
            <View
              className={`${styles.giftBtn} ${loadingOrderId === order.id ? styles.disabledBtn : ''}`}
              onClick={() => void handleTip(order)}
            >
              <Icon name="gift" size={26} color="#FFFFFF" className={styles.btnIcon} />
              <Text>{loadingOrderId === order.id ? '赠送中' : '送礼物'}</Text>
            </View>
          )}
          {can('EVALUATE', !evaluated) && (
            <View className={styles.primaryBtn} onClick={() => startEvaluate(order)}>
              <Icon name="star" size={26} color="#FFFFFF" className={styles.btnIcon} />
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
          <Icon name="headphones" size={26} color="#6E6E73" className={styles.btnIcon} />
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
                  <Text className={styles.statusLabel}>{statusTextMap[displayStatus]}</Text>
                </View>
              </View>

              <View className={styles.productRow}>
                <Image className={styles.productImage} src={order.service.cover_url || 'https://picsum.photos/id/64/200/200'} mode="aspectFill" />
                <View className={styles.productInfo}>
                  <Text className={styles.productName}>{order.service.name}</Text>
                  <Text className={styles.productSpec}>{order.service.description}</Text>
                  <Text className={styles.metaText}>
                    大神：{counterpart?.nickname || '待匹配'}
                  </Text>
                  {!order.service.is_gift && <Text className={styles.metaText}>局数：{order.game_rounds}局</Text>}
                  <Text className={styles.createTime}>下单时间：{formatDateTime(order.created_at)}</Text>
                </View>
                <View className={styles.priceBlock}>
                  <Text className={styles.price}>{formatXaCoin(order.amount ?? 0)}币</Text>
                  <Text className={styles.quantity}>{statusTextMap[displayStatus]}</Text>
                </View>
              </View>

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
                <Icon name={expandedOrderId === order.id ? 'chevron-up' : 'chevron-down'} size={28} color="#007AFF" />
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
                    <Text className={styles.detailLabel}>实付金额</Text>
                    <Text className={styles.amountValue}>{formatXaCoin(order.amount)}币</Text>
                  </View>
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
                  {statusHintMap[displayStatus] || '如有问题可联系平台客服处理。'}
                </Text>
                {renderActions(order)}
              </View>
            </View>
          );
        })}

        {visibleOrders.length === 0 && (
          <View className={styles.empty}>
            <View className={styles.emptyIconWrap}>
              <Icon name="package" size={80} color="#AEAEB2" />
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
                  size={56}
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
      {loginSheet}
    </View>
  );
};

export default OrderListPage;
