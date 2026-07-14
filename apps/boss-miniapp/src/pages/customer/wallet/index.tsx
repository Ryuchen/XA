import React, { useEffect, useState } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import {
  fetchWalletInfo,
  fetchTransactions,
  TransactionRecord,
} from '@/services/wallet';
import { openWecomCustomerService } from '@/services/support';
import { formatXaCoin } from '@/utils/format';
import { Empty, Skeleton } from '@/components';
import Icon from '@/components/Icon';
import styles from './index.module.scss';

const TX_TYPE_LABEL: Record<string, string> = {
  TOPUP: '充值',
  PAY: '订单支付',
  INCOME: '订单收益',
  WITHDRAW: '提现',
  DEPOSIT: '押金',
};

const TX_TABS = [
  { label: '全部', value: '' },
  { label: '充值', value: 'TOPUP' },
  { label: '支付', value: 'PAY' },
] as const;

const WalletPage: React.FC = () => {
  const [balance, setBalance] = useState(0);
  const [transactions, setTransactions] = useState<TransactionRecord[]>([]);
  const [txLoading, setTxLoading] = useState(true);
  const [activeTxType, setActiveTxType] = useState<string>('');

  const loadWallet = async () => {
    const res = await fetchWalletInfo();
    if (res.code === 0 && res.data) {
      setBalance(res.data.balance);
    }
  };

  const loadTransactions = async (txType: string) => {
    setTxLoading(true);
    try {
      const res = await fetchTransactions({ tx_type: txType || undefined, page_size: 50 });
      if (res.code === 0 && res.data) {
        setTransactions(res.data.transactions || []);
        setBalance(res.data.balance);
      }
    } finally {
      setTxLoading(false);
    }
  };

  useEffect(() => {
    loadWallet();
    loadTransactions('');
  }, []);

  const handleTabClick = (txType: string) => {
    setActiveTxType(txType);
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
    <ScrollView className={styles.container}>
      <View className={styles.header}>
        <View className={styles.headerTop}>
          <Icon name="wallet" size={40} color="#FFFFFF" />
          <Text className={styles.headerTitle}>我的钱包</Text>
        </View>
        <Text className={styles.headerLabel}>兴安币余额</Text>
        <Text className={styles.balance}>{formatXaCoin(balance)} <Text className={styles.balanceUnit}>兴安币</Text></Text>
      </View>

      <View className={styles.section}>
        <Text className={styles.sectionTitle}>企微客服充值</Text>
        <View className={styles.rechargeCard}>
          <View className={styles.rechargeIntro}>
            <View className={styles.rechargeIcon}><Icon name="message-circle" size={36} color="#007AFF" /></View>
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
            <Icon name="chevron-right" size={30} color="#FFFFFF" />
          </View>
          <Text className={styles.rechargeHint}>付款后由客服核对金额，兴安币到账后会出现在资金流水中</Text>
        </View>
      </View>

      <View className={styles.section}>
        <View className={styles.sectionHeader}>
          <Icon name="receipt" size={36} color="#007AFF" />
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
                    {tx.amount > 0 ? '+' : ''}{formatXaCoin(Math.abs(tx.amount))}币
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
