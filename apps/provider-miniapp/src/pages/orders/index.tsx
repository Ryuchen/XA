import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, ScrollView, Button } from '@tarojs/components';
import Taro, { useDidShow, usePullDownRefresh } from '@tarojs/taro';
import {
  fetchOrders,
  grabOrder,
  startOrder,
  completeOrder,
  rejectOrder,
  fetchProviderStats,
  fetchMyEvaluations,
  replyEvaluation,
} from '@/services/order';
import { Evaluation, ProviderOrder, ProviderStats, statusTextMap, REJECT_REASONS } from '@/types/order';
import { wsService } from '@/services/websocket';
import { formatXaCoin } from '@/utils/format';
import {
  fetchProviderPass, PassProduct, ProviderPassData, purchaseProviderPass,
} from '@/services/user';
import { Empty, Skeleton } from '@/components';
import styles from './index.module.scss';
import PlatformLayout from '@/components/PlatformLayout';
import ReplyDialog from '@/components/ReplyDialog';

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
  const [activeTab, setActiveTab] = useState<TabKey>('pending');
  const [orders, setOrders] = useState<ProviderOrder[]>([]);
  const [stats, setStats] = useState<ProviderStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [passData, setPassData] = useState<ProviderPassData | null>(null);
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [replying, setReplying] = useState<Evaluation | null>(null);
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

  const loadOrders = useCallback(async (tab: TabKey) => {
    setLoading(true);
    try {
      const res = await fetchOrders({ status: tab, role: 'provider' });
      if (res.code === 0 && res.data) {
        setOrders(res.data);
      } else {
        setOrders([]);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadOrders(activeTab);
    loadStats();
    loadPass();
    loadEvaluations();
    // 实时订单推送：任意订单状态变化时刷新当前列表与看板
    const off = wsService.on('order_status_update', () => {
      loadOrders(activeTabRef.current);
      loadStats();
    });
    // 金/银/铜卡和无卡用户的可见时间会自然到点，定时刷新让新订单无需
    // 等待下一条 WebSocket 事件即可出现；抢单接口仍由服务端二次校验。
    const timer = setInterval(() => {
      if (activeTabRef.current === 'pending') loadOrders('pending');
    }, 10000);
    return () => {
      off();
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadOrders(activeTab);
  }, [activeTab, loadOrders]);

  useDidShow(() => {
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
    if (acting) return;
    setActing(true);
    Taro.showLoading({ title: '处理中' });
    try {
      const res = await fn();
      if (res.code === 0) {
        Taro.showToast({ title: successText, icon: 'success' });
        await Promise.all([loadOrders(activeTabRef.current), loadStats()]);
      } else {
        Taro.showToast({ title: res.msg || '操作失败', icon: 'none' });
      }
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : '操作失败', icon: 'none' });
    } finally {
      Taro.hideLoading();
      setActing(false);
    }
  };

  const handleGrab = (order: ProviderOrder) => runAction(() => grabOrder(order.id), '接单成功');

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
          Taro.showModal({
            title: '拒单原因',
            editable: true,
            placeholderText: '请输入拒单原因',
            success: ({ confirm, content }) => {
              if (confirm) runAction(() => rejectOrder(order.id, content?.trim() || '其他'), '已拒单');
            },
          });
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
    if (!passData) return;
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
      const res = await purchaseProviderPass(product.tier);
      if (res.code === 0 && res.data) {
        setPassData(res.data);
        await loadOrders(activeTabRef.current);
        Taro.showToast({ title: '采购成功', icon: 'success' });
      } else Taro.showToast({ title: res.msg || '采购失败', icon: 'none' });
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : '采购失败', icon: 'none' });
    } finally { Taro.hideLoading(); }
  };

  const renderActions = (order: ProviderOrder) => {
    const can = (action: 'GRAB' | 'START' | 'COMPLETE' | 'REJECT', fallback: boolean) =>
      Array.isArray(order.allowed_actions) ? order.allowed_actions.includes(action) : fallback;
    if (order.status === 'PENDING') {
      if (!can('GRAB', true)) return null;
      return (
        <View className={styles.actions}>
          <Button
            className={`${styles.actionBtn} ${styles.primaryBtn}`}
            disabled={acting || reachedLimit}
            onClick={() => reachedLimit
              ? Taro.showToast({ title: `最多同时进行 ${maxConcurrent} 单`, icon: 'none' })
              : handleGrab(order)}
          >
            {reachedLimit ? '已达接单上限' : '抢单'}
          </Button>
        </View>
      );
    }
    if (order.status === 'GRABBED') {
      if (!can('START', Boolean(order.can_operate))) return null;
      return (
        <View className={styles.actions}>
          {can('REJECT', Boolean(order.can_reject)) && <Button className={`${styles.actionBtn} ${styles.rejectBtn}`} disabled={acting} onClick={() => handleReject(order)}>拒单</Button>}
          <Button className={`${styles.actionBtn} ${styles.primaryBtn}`} disabled={acting} onClick={() => handleStart(order)}>
            开始服务
          </Button>
        </View>
      );
    }
    if (order.status === 'IN_SERVICE') {
      if (!can('COMPLETE', Boolean(order.can_operate))) return null;
      return (
        <View className={styles.actions}>
          <Button className={`${styles.actionBtn} ${styles.primaryBtn}`} disabled={acting} onClick={() => handleComplete(order)}>
            完成订单
          </Button>
        </View>
      );
    }
    return null;
  };

  return (
    <PlatformLayout active='orders'><View className={styles.container}>
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

      <ScrollView className={styles.list} scrollY>
        {loading ? (
          <Skeleton variant="card-list" rows={3} />
        ) : orders.length === 0 ? (
          <Empty icon="🎮" title="暂无订单" desc={activeTab === 'pending' ? (reachedLimit ? `已达同时进行 ${maxConcurrent} 单上限，完成订单后可继续抢单` : '待接单池暂时空空如也') : '还没有此类订单'} />
        ) : (
          orders.map(order => {
            const bossType = order.customer?.boss_type;
            const vipColor = bossType?.color || '';
            const cardBg = vipColor ? hexToRgba(vipColor, 0.06) : '';
            const cardStyle = vipColor
              ? { background: cardBg || undefined, borderLeft: `6rpx solid ${vipColor}` }
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
                <View className={styles.serviceBlock}><Text className={styles.serviceName}>{order.service?.name || '陪玩服务'}</Text><Text className={styles.orderNo}>{order.order_no}</Text></View>
                <View className={styles.priceBlock}><Text className={styles.pricePrefix}>订单金额</Text><Text className={styles.amountValue}>{formatXaCoin(order.amount)}币</Text></View>
              </View>
              <View className={styles.chips}>
                <Text className={`${styles.statusTag} ${styles[`status_${order.status}`]}`}>{statusTextMap[order.status]}</Text>
                <Text className={styles.chip}>{order.game_rounds} 局</Text>
                {!!order.game_region && <Text className={styles.chip}>{order.game_region}</Text>}
                {showDiscount && (
                  <Text
                    className={styles.discountChip}
                    style={vipColor ? { color: vipColor, background: hexToRgba(vipColor, 0.12) || undefined } : undefined}
                  >{bossType!.discount_rate}折 VIP价</Text>
                )}
              </View>
              {!!order.game_nickname && (
                <View className={styles.infoRow}>
                  <Text className={styles.infoLabel}>老板游戏昵称</Text>
                  <Text className={styles.infoValue}>{order.game_nickname}</Text>
                </View>
              )}
              <View className={styles.incomePanel}>
                <View><Text className={styles.incomeLabel}>预计到手</Text><Text className={styles.incomeHint}>已扣平台服务费</Text></View>
                <Text className={styles.incomeValue}>{formatXaCoin(order.expected_income)}币</Text>
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
              <View className={styles.cardFooter}>
                <Text className={styles.time}>{new Date(order.created_at).toLocaleString()}</Text>
                {renderActions(order)}
              </View>
            </View>
            );
          })
        )}
      </ScrollView>
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
