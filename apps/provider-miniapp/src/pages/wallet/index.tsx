import React, { useEffect, useMemo, useState } from 'react';
import { Input, ScrollView, Text, View } from '@tarojs/components';
import Taro, { useDidShow, usePullDownRefresh } from '@tarojs/taro';
import {
  DisposeRecord, fetchProviderBenefits, fetchTransactions, fetchWithdrawOverview,
  GiftRecord, PayeeMethod, ProviderBenefits, TransactionRecord, withdrawRequest,
  WithdrawRecord,
} from '@/services/wallet';
import { formatXaCoin, toRawAmount } from '@/utils/format';
import { Empty, Skeleton } from '@/components';
import PlatformLayout from '@/components/PlatformLayout';
import styles from './index.module.scss';

type RecordTab = 'transactions' | 'dispose' | 'gift' | 'withdraw';

const TX_TYPE_LABEL: Record<string, string> = {
  TOPUP: '充值', PAY: '订单支付', INCOME: '订单收益', WITHDRAW: '提现',
  DEPOSIT: '押金', REWARD: '奖励', PENALTY: '罚款', PASS_PURCHASE: '通行证',
};
const PAYEE_METHODS: { label: string; value: PayeeMethod }[] = [
  { label: '微信', value: 'WECHAT' }, { label: '支付宝', value: 'ALIPAY' }, { label: '银行卡', value: 'BANK' },
];
const STATUS_LABEL: Record<string, string> = { PENDING: '待审核', APPROVED: '已通过', REJECTED: '已驳回' };
const EMPTY_BENEFITS: ProviderBenefits = {
  summary: { total_reward: 0, total_penalty: 0, total_gift_amount: 0, total_gift_income: 0, gift_count: 0 },
  dispose_records: [], gift_records: [],
};

const WalletPage: React.FC = () => {
  const [balance, setBalance] = useState(0);
  const [frozenAmount, setFrozenAmount] = useState(0);
  const [minAmount, setMinAmount] = useState(0);
  const [taxRate, setTaxRate] = useState(0);
  const [transactions, setTransactions] = useState<TransactionRecord[]>([]);
  const [withdrawRecords, setWithdrawRecords] = useState<WithdrawRecord[]>([]);
  const [benefits, setBenefits] = useState<ProviderBenefits>(EMPTY_BENEFITS);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<RecordTab>('transactions');
  const [showWithdraw, setShowWithdraw] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [withdrawInput, setWithdrawInput] = useState('');
  const [payeeMethod, setPayeeMethod] = useState<PayeeMethod>('WECHAT');
  const [payeeAccount, setPayeeAccount] = useState('');
  const [payeeName, setPayeeName] = useState('');
  const [withdrawPreview, setWithdrawPreview] = useState<{ tax_amount: number; actual_amount: number } | null>(null);

  const loadAll = async () => {
    setLoading(true);
    try {
      const [withdrawRes, txRes, benefitRes] = await Promise.all([
        fetchWithdrawOverview(), fetchTransactions({ page_size: 50 }), fetchProviderBenefits(),
      ]);
      if (withdrawRes.code === 0 && withdrawRes.data) {
        setBalance(withdrawRes.data.balance); setFrozenAmount(withdrawRes.data.frozen_amount);
        setMinAmount(withdrawRes.data.min_amount); setTaxRate(withdrawRes.data.tax_rate);
        setWithdrawRecords(withdrawRes.data.requests || []);
      }
      if (txRes.code === 0 && txRes.data) setTransactions(txRes.data.transactions || []);
      if (benefitRes.code === 0 && benefitRes.data) setBenefits(benefitRes.data);
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : '钱包加载失败', icon: 'none' });
    } finally { setLoading(false); }
  };
  useEffect(() => { loadAll(); }, []);
  useDidShow(() => { loadAll(); });
  usePullDownRefresh(async () => { await loadAll(); Taro.stopPullDownRefresh(); });

  const totalIncome = useMemo(() => transactions
    .filter(item => item.tx_type === 'INCOME' && item.amount > 0)
    .reduce((sum, item) => sum + item.amount, 0), [transactions]);
  const parsedWithdraw = parseFloat(withdrawInput);
  const withdrawAmount = toRawAmount(parsedWithdraw);

  // 提现试算：输入有效金额时调用 T8-a 试算接口，用后端口径回显税额与实际到账。
  // 空串 / 非法值（withdrawAmount <= 0）不请求、不报错。
  useEffect(() => {
    if (withdrawAmount <= 0) {
      setWithdrawPreview(null);
      return;
    }
    let cancelled = false;
    fetchWithdrawOverview(withdrawAmount)
      .then((res) => {
        if (cancelled) return;
        if (res.code !== 0 || !res.data || res.data.amount !== withdrawAmount) {
          setWithdrawPreview(null);
          return;
        }
        setWithdrawPreview({
          tax_amount: Number(res.data.tax_amount) || 0,
          actual_amount: Number(res.data.actual_amount) || 0,
        });
      })
      .catch(() => { if (!cancelled) setWithdrawPreview(null); });
    return () => { cancelled = true; };
  }, [withdrawAmount]);

  const validateWithdraw = (): string | null => {
    if (!Number.isFinite(parsedWithdraw) || withdrawAmount <= 0) return '请输入有效提现金额';
    if (withdrawAmount < minAmount) return `最低提现 ${formatXaCoin(minAmount)} 兴安币`;
    if (withdrawAmount > balance) return '提现金额超出可用余额';
    if (!payeeAccount.trim() || !payeeName.trim()) return '请填写收款账号与姓名';
    return null;
  };
  const canWithdraw = !submitting && !validateWithdraw();

  const handleWithdraw = async () => {
    if (submitting) return;
    const error = validateWithdraw();
    if (error) return void Taro.showToast({ title: error, icon: 'none' });
    const amount = withdrawAmount;
    setSubmitting(true);
    try {
      const res = await withdrawRequest({ amount, payee_method: payeeMethod, payee_account: payeeAccount.trim(), payee_name: payeeName.trim() });
      if (res.code !== 0) throw new Error(res.msg || '提现失败');
      Taro.showToast({ title: '提现申请已提交', icon: 'success' });
      setShowWithdraw(false); setWithdrawInput(''); setPayeeAccount(''); setPayeeName(''); setActiveTab('withdraw');
      await loadAll();
    } catch (error) { Taro.showToast({ title: error instanceof Error ? error.message : '提现失败', icon: 'none' }); }
    finally { setSubmitting(false); }
  };

  const renderDispose = (item: DisposeRecord) => (
    <View className={styles.listItem} key={item.id}>
      <View className={`${styles.itemIcon} ${item.type === 'REWARD' ? styles.rewardIcon : styles.penaltyIcon}`}>{item.type === 'REWARD' ? '奖' : '罚'}</View>
      <View className={styles.itemInfo}><Text className={styles.itemTitle}>{item.type_display}</Text><Text className={styles.itemDesc}>{item.reason || '暂无说明'}</Text><Text className={styles.itemTime}>{new Date(item.created_at).toLocaleString()}</Text></View>
      <Text className={item.type === 'REWARD' ? styles.positive : styles.negative}>{item.type === 'REWARD' ? '+' : '-'}{formatXaCoin(item.amount)}币</Text>
    </View>
  );
  const renderGift = (item: GiftRecord) => (
    <View className={styles.listItem} key={item.id}>
      <View className={`${styles.itemIcon} ${styles.giftIcon}`}>礼</View>
      <View className={styles.itemInfo}><Text className={styles.itemTitle}>{item.service_name}</Text><Text className={styles.itemDesc}>{item.customer_name} · {item.order_no}</Text><Text className={styles.itemTime}>{new Date(item.completed_at).toLocaleString()}</Text></View>
      <View className={styles.amountColumn}><Text className={styles.positive}>+{formatXaCoin(item.provider_income)}币</Text><Text className={styles.itemTime}>流水 {formatXaCoin(item.amount)}币</Text></View>
    </View>
  );

  return (
    <PlatformLayout active='wallet'><ScrollView className={styles.container} scrollY>
      <View className={styles.walletCard}>
        <View className={styles.cardTop}><Text className={styles.cardLabel}>可提现兴安币</Text><Text className={styles.eye}>●●●</Text></View>
        <Text className={styles.balance}>{formatXaCoin(balance)}币</Text>
        <View className={styles.cardBottom}>
          <View><Text className={styles.miniLabel}>累计订单收益</Text><Text className={styles.miniValue}>{formatXaCoin(totalIncome)}币</Text></View>
          <View><Text className={styles.miniLabel}>冻结金额</Text><Text className={styles.miniValue}>{formatXaCoin(frozenAmount)}币</Text></View>
          <View className={`${styles.withdrawButton} ${balance <= 0 ? styles.controlDisabled : ''}`} onClick={() => balance > 0 && setShowWithdraw(true)}>提现</View>
        </View>
      </View>

      <View className={styles.overview}>
        <View className={styles.overviewItem}><Text className={styles.overviewValue}>+{formatXaCoin(benefits.summary.total_reward)}币</Text><Text className={styles.overviewLabel}>奖励</Text></View>
        <View className={styles.overviewItem}><Text className={`${styles.overviewValue} ${styles.red}`}>-{formatXaCoin(benefits.summary.total_penalty)}币</Text><Text className={styles.overviewLabel}>处罚</Text></View>
        <View className={styles.overviewItem}><Text className={styles.overviewValue}>{formatXaCoin(benefits.summary.total_gift_income)}币</Text><Text className={styles.overviewLabel}>礼物实得</Text></View>
      </View>

      <View className={styles.tabs}>
        {([['transactions', '流水'], ['dispose', '奖惩'], ['gift', '礼物'], ['withdraw', '提现']] as [RecordTab, string][]).map(([value, label]) => (
          <View key={value} className={`${styles.tab} ${activeTab === value ? styles.tabActive : ''}`} onClick={() => setActiveTab(value)}>{label}</View>
        ))}
      </View>

      <View className={styles.listCard}>
        {loading ? <Skeleton variant='card-list' rows={4} /> : activeTab === 'transactions' ? (
          transactions.length ? transactions.map(item => <View className={styles.listItem} key={item.id}>
            <View className={`${styles.itemIcon} ${styles.txIcon}`}>币</View>
            <View className={styles.itemInfo}><Text className={styles.itemTitle}>{TX_TYPE_LABEL[item.tx_type] || item.tx_type}</Text><Text className={styles.itemDesc}>{item.remark || item.tx_no}</Text><Text className={styles.itemTime}>{new Date(item.created_at).toLocaleString()}</Text></View>
            <Text className={item.amount > 0 ? styles.positive : styles.negative}>{item.amount > 0 ? '+' : '-'}{formatXaCoin(Math.abs(item.amount))}币</Text>
          </View>) : <Empty icon='💰' title='暂无资金流水' desc='订单收益和钱包变动会显示在这里' />
        ) : activeTab === 'dispose' ? (
          benefits.dispose_records.length ? benefits.dispose_records.map(renderDispose) : <Empty icon='⚖️' title='暂无奖惩记录' desc='平台奖励或处罚后会显示在这里' />
        ) : activeTab === 'gift' ? (
          benefits.gift_records.length ? benefits.gift_records.map(renderGift) : <Empty icon='🎁' title='暂无礼物打赏' desc='已完成的礼物订单会显示在这里' />
        ) : withdrawRecords.length ? withdrawRecords.map(item => <View className={styles.listItem} key={item.id}>
          <View className={`${styles.itemIcon} ${styles.withdrawIcon}`}>提</View>
          <View className={styles.itemInfo}><Text className={styles.itemTitle}>{item.payee_method_display} · {item.payee_name}</Text><Text className={styles.itemDesc}>{item.status === 'REJECTED' && item.audit_remark ? `驳回：${item.audit_remark}` : '提现申请'}</Text><Text className={styles.itemTime}>{new Date(item.created_at).toLocaleString()}</Text></View>
          <View className={styles.amountColumn}><Text className={styles.negative}>-{formatXaCoin(item.amount)}币</Text><Text className={styles.itemTime}>{item.tax_amount > 0 ? `税${formatXaCoin(item.tax_amount)} · 到账${formatXaCoin(item.actual_amount)}币` : ''}</Text><Text className={`${styles.status} ${styles[`status_${item.status}`]}`}>{STATUS_LABEL[item.status]}</Text></View>
        </View>) : <Empty icon='↗️' title='暂无提现记录' desc='提交提现后可在这里查看审核进度' />}
      </View>

      {showWithdraw && <View className={styles.mask} onClick={() => !submitting && setShowWithdraw(false)}>
        <View className={styles.sheet} onClick={event => event.stopPropagation()}>
          <View className={styles.handle} /><View className={styles.sheetHead}><Text className={styles.sheetTitle}>申请提现</Text><Text className={styles.close} onClick={() => setShowWithdraw(false)}>取消</Text></View>
          <Text className={styles.formLabel}>提现兴安币</Text><View className={styles.moneyInput}><Text>币</Text><Input type='digit' value={withdrawInput} placeholder='0' onInput={e => setWithdrawInput(e.detail.value)} /></View>
          <Text className={styles.formHint}>可提现 {formatXaCoin(balance)}币 · 最低 {formatXaCoin(minAmount)}币 · 本次提现 {formatXaCoin(withdrawAmount)}币</Text>
          <Text className={styles.formHint}>税率 {taxRate}% · 应扣税 {formatXaCoin(withdrawPreview?.tax_amount ?? 0)}币 · 实际到账 {formatXaCoin(withdrawPreview?.actual_amount ?? 0)}币</Text>
          <Text className={styles.formLabel}>收款方式</Text><View className={styles.methodRow}>{PAYEE_METHODS.map(item => <View key={item.value} className={`${styles.method} ${payeeMethod === item.value ? styles.methodActive : ''}`} onClick={() => setPayeeMethod(item.value)}>{item.label}</View>)}</View>
          <Text className={styles.formLabel}>收款账号</Text><Input className={styles.formInput} value={payeeAccount} placeholder='微信号 / 支付宝账号 / 银行卡号' onInput={e => setPayeeAccount(e.detail.value)} />
          <Text className={styles.formLabel}>收款人姓名</Text><Input className={styles.formInput} value={payeeName} placeholder='请输入真实姓名' onInput={e => setPayeeName(e.detail.value)} />
          <View className={`${styles.confirm} ${!canWithdraw ? styles.controlDisabled : ''}`} onClick={() => canWithdraw && handleWithdraw()}>{submitting ? '提交中…' : '确认提现'}</View>
        </View>
      </View>}
    </ScrollView></PlatformLayout>
  );
};
export default WalletPage;
