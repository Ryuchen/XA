import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro, { useDidHide, useDidShow, usePullDownRefresh } from '@tarojs/taro';
import { fetchTransactions, TransactionRecord } from '@/services/wallet';
import { openWecomCustomerService } from '@/services/support';
import { formatXaCoin } from '@xa/money';
import { useWalletStore } from '@/store';
import { Empty, Skeleton } from '@/components';
import Icon from '@/components/Icon';
import { getSessionRevision, getStoredToken, getStoredUser } from '@/utils/auth';
import { createCoalescedRefresh } from '@/utils/coalescedRefresh';
import { wsService } from '@/services/websocket';
import styles from './index.module.scss';

const TX_TYPE_LABEL: Record<string, string> = {
  TOPUP: '充值',
  PAY: '订单支付',
  INCOME: '订单收益',
  WITHDRAW: '提现',
  DEPOSIT: '押金',
  REFUND: '订单退款',
};

const TX_TABS = [
  { label: '全部', value: '' },
  { label: '充值', value: 'TOPUP' },
  { label: '支付', value: 'PAY' },
  { label: '退款', value: 'REFUND' },
  { label: '收益', value: 'INCOME' },
  { label: '提现', value: 'WITHDRAW' },
  { label: '押金', value: 'DEPOSIT' },
] as const;

const WalletPage: React.FC = () => {
  const balance = useWalletStore(state => state.balance);
  const setBalance = useWalletStore(state => state.setBalance);
  const [transactions, setTransactions] = useState<TransactionRecord[]>([]);
  const [txLoading, setTxLoading] = useState(true);
  const [activeTxType, setActiveTxType] = useState<string>('');
  const alive = useRef(true), visible = useRef(true), sequence = useRef(0), activeType = useRef('');
  const session = useRef(getSessionRevision());
  activeType.current = activeTxType;

  const loadTransactions = useCallback(async (txType: string) => {
    if (!alive.current || !getStoredToken()) { setTxLoading(false); return; }
    const version = ++sequence.current, revision = getSessionRevision();
    const current = () => alive.current && version === sequence.current && revision === getSessionRevision();
    setTxLoading(true);
    try {
      const res = await fetchTransactions({ tx_type: txType || undefined, page_size: 50 });
      if (!current()) return;
      if (res.code === 0 && res.data) {
        setTransactions(res.data.transactions || []);
        // 流水接口已返回最新余额，顺带回填 store，省掉一次 /wallet/info/
        setBalance(res.data.balance);
      }
    } catch {
      if (current()) Taro.showToast({ title: '加载失败', icon: 'none' });
    } finally {
      if (current()) setTxLoading(false);
    }
  }, [setBalance]);

  useEffect(() => {
    alive.current = true;
    const refresh = createCoalescedRefresh(async () => { if (visible.current) await loadTransactions(activeType.current); });
    const requestRefresh = () => { if (visible.current && getStoredToken()) { ++sequence.current; setTxLoading(true); refresh.request(); } };
    const offWallet = wsService.on('wallet_update', data => {
      const accountId = (data as { account_id?: unknown } | null)?.account_id;
      if (accountId != null && String(accountId) === String(getStoredUser()?.id)) requestRefresh();
    });
    const offConnected = wsService.on('connected', requestRefresh);
    const onSession = () => {
      if (session.current === getSessionRevision()) return;
      session.current = getSessionRevision(); ++sequence.current;
      setTransactions([]); setBalance(0); setActiveTxType(''); activeType.current = '';
      if (visible.current && getStoredToken()) void loadTransactions(''); else setTxLoading(false);
    };
    Taro.eventCenter.on('xa:auth.changed', onSession);
    return () => { alive.current = false; ++sequence.current; offWallet(); offConnected(); refresh.dispose(); Taro.eventCenter.off('xa:auth.changed', onSession); };
  }, [loadTransactions, setBalance]);

  // useDidShow 首次进入也会触发，无需再写 useEffect，避免首屏双份请求
  useDidShow(() => {
    visible.current = true;
    return loadTransactions(activeType.current);
  });
  useDidHide(() => { visible.current = false; });

  usePullDownRefresh(async () => {
    try { await loadTransactions(activeType.current); } finally { Taro.stopPullDownRefresh(); }
  });

  const handleTabClick = (txType: string) => {
    setActiveTxType(txType);
    activeType.current = txType;
    loadTransactions(txType);
  };

  const handleContactRecharge = async () => {
    Taro.showLoading({ title: '正在连接客服' });
    const result = await openWecomCustomerService({
      title: '我要充值兴安币',
      path: '/pages/customer/wallet/index',
    });
    Taro.hideLoading();
    if (!result.opened) {
      Taro.navigateTo({ url: '/pages/serviceCard/index?scene=recharge' });
    }
  };

  return (
    <ScrollView className={styles.container} scrollY>
      <View className={styles.header}>
        <View className={styles.headerTop}>
          <Icon name="wallet" size={40} color="#fffdf5" />
          <Text className={styles.headerTitle}>我的钱包</Text>
        </View>
        <Text className={styles.headerLabel}>兴安币余额</Text>
        <Text className={styles.balance}>{formatXaCoin(balance)} <Text className={styles.balanceUnit}>兴安币</Text></Text>
      </View>

      <View className={styles.section}>
        <Text className={styles.sectionTitle}>企微客服充值</Text>
        <View className={styles.rechargeCard}>
          <View className={styles.rechargeIntro}>
            <View className={styles.rechargeIcon}><Icon name="message-circle" size={32} color="#002fa7" /></View>
            <View className={styles.rechargeCopy}>
              <Text className={styles.rechargeTitle}>兴安币充值 · 客服确认入账</Text>
              <Text className={styles.rechargeDesc}>统一兑换比例：1:10，到账统一记为兴安币</Text>
            </View>
          </View>
          <View className={styles.exchangeExamples}>
            {['500兴安币', '1000兴安币', '2000兴安币'].map(item => (
              <Text key={item} className={styles.exchangeChip}>{item}</Text>
            ))}
          </View>
          <View className={styles.rechargeButton} onClick={handleContactRecharge}>
            <Text>联系企业微信客服充值</Text>
            <Icon name="chevron-right" size={32} color="#fffdf5" />
          </View>
          <Text className={styles.rechargeHint}>付款后由客服核对金额，兴安币到账后会出现在资金流水中</Text>
        </View>
      </View>

      <View className={styles.section}>
        <View className={styles.sectionHeader}>
          <Icon name="receipt" size={32} color="#002fa7" />
          <Text className={styles.sectionTitle}>资金流水</Text>
        </View>
        <ScrollView className={styles.tabRow} scrollX>
          {TX_TABS.map(t => (
            <View
              key={t.value || 'all'}
              className={`${styles.tab} ${activeTxType === t.value ? styles.active : ''}`}
              onClick={() => handleTabClick(t.value)}
            >
              <Text>{t.label}</Text>
            </View>
          ))}
        </ScrollView>

        <View className={styles.txList}>
          {txLoading ? (
            <Skeleton variant="card-list" rows={4} />
          ) : (
            <>
              {transactions.map(tx => (
                <View key={tx.id} className={styles.txItem}>
                  <View className={`${styles.txIcon} ${tx.amount > 0 ? styles.txIconIncome : styles.txIconExpense}`}>
                    <Icon
                      name={tx.amount > 0 ? 'arrow-down-left' : 'arrow-up-right'}
                      size={32}
                      color={tx.amount > 0 ? '#34C759' : '#6E6E73'}
                    />
                  </View>
                  <View className={styles.txInfo}>
                    <Text className={styles.txType}>{TX_TYPE_LABEL[tx.tx_type] || tx.tx_type}</Text>
                    <Text className={styles.txRemark}>{tx.remark || tx.tx_no}</Text>
                    <Text className={styles.txRemark}>{tx.created_at}</Text>
                  </View>
                  <Text className={`${styles.txAmount} ${tx.amount > 0 ? styles.positive : styles.negative}`}>
                    {tx.amount > 0 ? '+' : ''}{formatXaCoin(tx.amount)}币
                  </Text>
                </View>
              ))}
              {transactions.length === 0 && (
                <Empty icon="🧾" title="暂无流水记录" desc="充值或下单后将在这里展示明细" />
              )}
            </>
          )}
        </View>
      </View>
    </ScrollView>
  );
};

export default WalletPage;
