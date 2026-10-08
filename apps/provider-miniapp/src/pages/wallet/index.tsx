import React, { useEffect, useRef, useState } from 'react';
import { Button, Input, Text, View } from '@tarojs/components';
import Taro, { useDidShow, usePullDownRefresh } from '@tarojs/taro';
import {
  DisposeRecord, fetchProviderBenefits, fetchTransactions, fetchWithdrawOverview,
  GiftRecord, PayeeMethod, ProviderBenefits, TransactionRecord, withdrawRequest,
  WithdrawRecord, fetchWithdrawalAccounts, SavedWithdrawalAccount,
} from '@/services/wallet';
import { formatRmb, formatXaCoin } from '@/utils/format';
import { parseWithdrawAmount, validWithdrawQuote, WithdrawQuote } from '@/utils/withdraw';
import { Empty, Skeleton } from '@/components';
import PlatformLayout from '@/components/PlatformLayout';
import PageHeading from '@/components/PageHeading';
import SurfaceDialog from '@/components/SurfaceDialog';
import styles from './index.module.scss';

type RecordTab = 'transactions' | 'dispose' | 'gift' | 'withdraw';

const TX_TYPE_LABEL: Record<string, string> = {
  TOPUP: '充值', PAY: '订单支付', INCOME: '订单收益', WITHDRAW: '提现',
  DEPOSIT: '押金', REWARD: '奖励', PENALTY: '罚款', PASS_PURCHASE: '通行证',
  GIFT: '客服赠送', REFUND: '退款', ADJUST: '调账',
  EARNINGS_FREEZE: '收益冻结', EARNINGS_RELEASE: '收益解冻',
};
const PAYEE_METHODS: { label: string; value: PayeeMethod }[] = [
  { label: '微信', value: 'WECHAT' }, { label: '支付宝', value: 'ALIPAY' }, { label: '银行卡', value: 'BANK' },
];
const STATUS_LABEL: Record<string, string> = { PENDING: '待审核', APPROVED: '已通过', REJECTED: '已驳回' };
const EMPTY_BENEFITS: ProviderBenefits = {
  summary: { total_reward: 0, total_penalty: 0, total_gift_amount: 0, total_gift_income: 0, gift_count: 0 },
  dispose_records: [], gift_records: [],
  pagination: { page_size: 20, dispose: { page: 1, total: 0 }, gift: { page: 1, total: 0 } },
};

const WalletPage: React.FC = () => {
  const [balance, setBalance] = useState(0);
  const [frozenAmount, setFrozenAmount] = useState(0);
  const [minAmount, setMinAmount] = useState(0);
  const [balanceThreshold, setBalanceThreshold] = useState<number | null>(null);
  const [taxRate, setTaxRate] = useState(0);
  const [transactions, setTransactions] = useState<TransactionRecord[]>([]);
  const [totalIncome, setTotalIncome] = useState(0);
  const [transactionTotal, setTransactionTotal] = useState(0);
  const [transactionPage, setTransactionPage] = useState(1);
  const [loadingMore, setLoadingMore] = useState(false);
  const loadingMoreRef = useRef(false);
  const requestVersion = useRef(0);
  const [withdrawRecords, setWithdrawRecords] = useState<WithdrawRecord[]>([]);
  const [benefits, setBenefits] = useState<ProviderBenefits>(EMPTY_BENEFITS);
  const [loadingBenefits, setLoadingBenefits] = useState({ dispose: false, gift: false });
  const loadingBenefitsRef = useRef({ dispose: false, gift: false });
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [activeTab, setActiveTab] = useState<RecordTab>('transactions');
  const [showWithdraw, setShowWithdraw] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const [withdrawInput, setWithdrawInput] = useState('');
  const [payeeMethod, setPayeeMethod] = useState<PayeeMethod>('WECHAT');
  const initialPayeeSelected = useRef(false);
  const [withdrawalAccounts, setWithdrawalAccounts] = useState<SavedWithdrawalAccount[]>([]);
  const [withdrawPreview, setWithdrawPreview] = useState<WithdrawQuote | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [quoteRevision, setQuoteRevision] = useState(0);

  const loadAll = async () => {
    const version = ++requestVersion.current;
    setLoading(true);
    setLoadError('');
    try {
      const [withdrawRes, txRes, benefitRes, accountsRes] = await Promise.all([
        fetchWithdrawOverview(), fetchTransactions({ page_size: 50 }), fetchProviderBenefits(), fetchWithdrawalAccounts(),
      ]);
      if (version !== requestVersion.current) return;
      if (withdrawRes.code !== 0 || !withdrawRes.data) throw new Error(withdrawRes.msg || '提现信息加载失败');
      if (txRes.code !== 0 || !txRes.data) throw new Error(txRes.msg || '流水加载失败');
      if (benefitRes.code !== 0 || !benefitRes.data) throw new Error(benefitRes.msg || '收益信息加载失败');
      if (accountsRes.code !== 0 || !accountsRes.data) throw new Error(accountsRes.msg || '收款账户加载失败');
      const accounts = accountsRes.data;
      setWithdrawalAccounts(accounts);
      if (!initialPayeeSelected.current) {
        setPayeeMethod(previous => accounts.some(row => row.payee_method === previous) ? previous : accounts[0]?.payee_method || previous);
        initialPayeeSelected.current = true;
      }
      if (withdrawRes.code === 0 && withdrawRes.data) {
        setBalance(withdrawRes.data.balance); setFrozenAmount(withdrawRes.data.frozen_amount);
        setMinAmount(withdrawRes.data.min_amount); setTaxRate(withdrawRes.data.tax_rate);
        setBalanceThreshold(withdrawRes.data.balance_threshold ?? null);
        setWithdrawRecords(withdrawRes.data.requests || []);
      }
      if (txRes.code === 0 && txRes.data) {
        setTransactions(txRes.data.transactions || []);
        setTotalIncome(txRes.data.total_income || 0);
        setTransactionTotal(txRes.data.total);
        setTransactionPage(1);
      }
      if (benefitRes.code === 0 && benefitRes.data) setBenefits(benefitRes.data);
    } catch (error) {
      if (version !== requestVersion.current) return;
      setLoadError(error instanceof Error ? error.message : '钱包加载失败');
      Taro.showToast({ title: error instanceof Error ? error.message : '钱包加载失败', icon: 'none' });
    } finally { if (version === requestVersion.current) setLoading(false); }
  };
  useEffect(() => { loadAll(); return () => { requestVersion.current++; }; }, []);
  useDidShow(() => { loadAll(); });
  usePullDownRefresh(async () => { await loadAll(); Taro.stopPullDownRefresh(); });

  const loadMoreTransactions = async () => {
    if (loading || loadingMoreRef.current || transactions.length >= transactionTotal) return;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    const version = requestVersion.current;
    try {
      const result = await fetchTransactions({ page: transactionPage + 1, page_size: 50 });
      if (version !== requestVersion.current) return;
      if (result.code !== 0 || !result.data) throw new Error(result.msg || '流水加载失败');
      const data = result.data;
      setTransactions(previous => [...previous, ...data.transactions.filter(item => !previous.some(old => old.id === item.id))]);
      setTransactionTotal(data.total);
      setTransactionPage(data.page);
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : '流水加载失败', icon: 'none' });
    } finally { loadingMoreRef.current = false; setLoadingMore(false); }
  };
  const withdrawAmount = parseWithdrawAmount(withdrawInput);
  const selectedAccount = withdrawalAccounts.find(row => row.payee_method === payeeMethod);

  const loadMoreBenefits = async (kind: 'dispose' | 'gift') => {
    const recordKey = kind === 'dispose' ? 'dispose_records' : 'gift_records';
    if (loading || loadError || loadingBenefitsRef.current[kind] || benefits[recordKey].length >= benefits.pagination[kind].total) return;
    loadingBenefitsRef.current[kind] = true;
    setLoadingBenefits(previous => ({ ...previous, [kind]: true }));
    const version = requestVersion.current;
    try {
      const result = await fetchProviderBenefits({
        dispose_page: benefits.pagination.dispose.page, gift_page: benefits.pagination.gift.page,
        [`${kind}_page`]: benefits.pagination[kind].page + 1, page_size: benefits.pagination.page_size,
      });
      if (version !== requestVersion.current) return;
      if (result.code !== 0 || !result.data) throw new Error(result.msg || '明细加载失败');
      const data = result.data;
      setBenefits(previous => ({
        ...previous, summary: data.summary,
        [recordKey]: [...previous[recordKey], ...data[recordKey].filter(item => !previous[recordKey].some(old => old.id === item.id))],
        pagination: { ...previous.pagination, [kind]: data.pagination[kind] },
      }));
    } catch (error) {
      if (version === requestVersion.current) Taro.showToast({ title: error instanceof Error ? error.message : '明细加载失败', icon: 'none' });
    } finally {
      loadingBenefitsRef.current[kind] = false;
      setLoadingBenefits(previous => ({ ...previous, [kind]: false }));
    }
  };

  // 提现试算：输入有效金额时调用 T8-a 试算接口，用后端口径回显税额与实际到账。
  // 空串 / 非法值（withdrawAmount <= 0）不请求、不报错。
  useEffect(() => {
    setWithdrawPreview(null);
    setPreviewError('');
    if (!showWithdraw || withdrawAmount <= 0) {
      setPreviewLoading(false);
      return;
    }
    let cancelled = false;
    setPreviewLoading(true);
    fetchWithdrawOverview(withdrawAmount)
      .then((res) => {
        if (cancelled) return;
        if (res.code !== 0 || !validWithdrawQuote(withdrawAmount, res.data)) throw new Error(res.msg || '提现试算不完整，请重试');
        setWithdrawPreview(res.data);
        setBalance(res.data.balance); setFrozenAmount(res.data.frozen_amount);
        setMinAmount(res.data.min_amount); setTaxRate(res.data.tax_rate);
        setBalanceThreshold(res.data.balance_threshold ?? null);
      })
      .catch(error => { if (!cancelled) { setWithdrawPreview(null); setPreviewError(error instanceof Error ? error.message : '试算失败，请重试'); } })
      .finally(() => { if (!cancelled) setPreviewLoading(false); });
    return () => { cancelled = true; };
  }, [withdrawAmount, showWithdraw, quoteRevision]);

  const validateWithdraw = (): string | null => {
    if (loading || loadError) return '请先重新加载钱包信息';
    if (withdrawAmount <= 0) return '请输入有效金额，最多一位小数';
    if (previewLoading || !validWithdrawQuote(withdrawAmount, withdrawPreview)) return '请等待完整试算后再提交';
    if (withdrawPreview.can_withdraw === false) return '可用余额须大于50元才可提现';
    if (withdrawAmount < withdrawPreview.min_amount) return `最低提现 ${formatXaCoin(withdrawPreview.min_amount)} 兴安币`;
    if (withdrawAmount > withdrawPreview.balance) return '提现金额超出可用余额';
    if (!selectedAccount) return '请先绑定该方式的收款账户';
    return null;
  };
  const canWithdraw = !submitting && !validateWithdraw();
  const activeQuote = validWithdrawQuote(withdrawAmount, withdrawPreview) ? withdrawPreview : null;

  const handleWithdraw = async () => {
    if (submittingRef.current) return;
    const error = validateWithdraw();
    if (error) return void Taro.showToast({ title: error, icon: 'none' });
    const amount = withdrawAmount;
    const quote = withdrawPreview!;
    submittingRef.current = true;
    setSubmitting(true);
    try {
      const res = await withdrawRequest({ amount, withdrawal_account_id: selectedAccount!.id, expected_payee_version: selectedAccount!.version,
        expected_tax_rate: quote.tax_rate, expected_tax_amount: quote.tax_amount, expected_actual_amount: quote.actual_amount });
      if (res.code === 409) {
        setShowWithdraw(false); setWithdrawInput(''); setActiveTab('withdraw');
        await loadAll();
        Taro.showToast({ title: '该申请已提交，请核对提现记录', icon: 'none' });
        return;
      }
      if (res.code !== 0) {
        if (res.errorCode === 'PAYEE_CHANGED') await loadAll();
        setWithdrawPreview(null); setQuoteRevision(value => value + 1);
        throw new Error(res.msg || '提现失败');
      }
      Taro.showToast({ title: '提现申请已提交', icon: 'success' });
      setShowWithdraw(false); setWithdrawInput(''); setActiveTab('withdraw');
      await loadAll();
    } catch (error) { Taro.showToast({ title: error instanceof Error ? error.message : '提现失败', icon: 'none' }); }
    finally { submittingRef.current = false; setSubmitting(false); }
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
      <View className={styles.amountColumn}>
        <Text className={item.received_income > 0 ? styles.positive : styles.negative}>{item.received_income > 0 ? '+' : ''}{formatXaCoin(item.received_income)}币</Text>
        <Text className={styles.itemTime}>{item.settlement_status === 'SETTLED' ? '已入账' : item.settlement_status === 'PENDING' ? '待入账' : '请联系客服核对'}</Text>
        {item.settlement_status !== 'SETTLED' && <Text className={styles.itemTime}>应得 {formatXaCoin(item.provider_income)}币</Text>}
        <Text className={styles.itemTime}>流水 {formatXaCoin(item.amount)}币</Text>
      </View>
    </View>
  );

  return (
    <PlatformLayout active='wallet'><View className={`xa-provider-page ${styles.container}`}>
      <PageHeading title='资产钱包' hint='查看收益、资金流水与提现进度' />
      <View className={styles.walletCard}>
        <View className={styles.cardTop}><Text className={styles.cardLabel}>可提现兴安币</Text><Text className={styles.eye}>资产钱包</Text></View>
        <Text className={styles.balance}>{formatXaCoin(balance)}币</Text>
        <View className={styles.cardBottom}>
          <View><Text className={styles.miniLabel}>累计订单收益</Text><Text className={styles.miniValue}>{formatXaCoin(totalIncome)}币</Text></View>
          <View><Text className={styles.miniLabel}>冻结金额</Text><Text className={styles.miniValue}>{formatXaCoin(frozenAmount)}币</Text></View>
          <Button className={styles.withdrawButton} disabled={balance <= 0 || loading || !!loadError} onClick={() => setShowWithdraw(true)}>提现</Button>
        </View>
      </View>

      <Button className={styles.supportButton} onClick={() => Taro.navigateTo({ url: '/pages/support/index' })}>联系客服充值 / 钱包咨询</Button>
      <View className={styles.accountEntry}><View><Text className={styles.itemTitle}>提现账户</Text><Text className={styles.formHint}>微信、支付宝、银行卡各可绑定一个</Text></View><Button size='mini' disabled={submitting} onClick={() => Taro.navigateTo({ url: '/pages/withdrawAccounts/index' })}>管理账户</Button></View>
      {!!loadError && <View className={styles.loadError}><Text>{loadError}</Text><Button size='mini' onClick={loadAll}>重新加载</Button></View>}
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
          <View className={styles.itemInfo}><Text className={styles.itemTitle}>{item.payee_method_display} · {item.payee_name}</Text><Text className={styles.itemDesc}>{item.status === 'REJECTED' && item.audit_remark ? `驳回：${item.audit_remark}` : `${item.status === 'APPROVED' ? '已打款' : '预计打款'} ¥${formatRmb(item.actual_amount)}`}</Text>{!!item.payout_reference && <Text className={styles.itemDesc}>打款凭证：{item.payout_reference}</Text>}<Text className={styles.itemTime}>{new Date(item.paid_at || item.created_at).toLocaleString()}</Text></View>
          <View className={styles.amountColumn}><Text className={styles.negative}>-{formatXaCoin(item.amount)}币</Text><Text className={styles.itemTime}>{item.tax_amount > 0 ? `税${formatXaCoin(item.tax_amount)} · 到账${formatXaCoin(item.actual_amount)}币` : ''}</Text><Text className={`${styles.status} ${styles[`status_${item.status}`]}`}>{STATUS_LABEL[item.status]}</Text></View>
        </View>) : <Empty icon='↗️' title='暂无提现记录' desc='提交提现后可在这里查看审核进度' />}
      </View>

      {activeTab === 'transactions' && transactions.length < transactionTotal && (
        <Button loading={loadingMore} disabled={loading || loadingMore} onClick={loadMoreTransactions}>加载更多流水（{transactions.length}/{transactionTotal}）</Button>
      )}
      {(activeTab === 'dispose' || activeTab === 'gift') && benefits[activeTab === 'dispose' ? 'dispose_records' : 'gift_records'].length < benefits.pagination[activeTab].total && (
        <Button loading={loadingBenefits[activeTab]} disabled={loading || !!loadError || loadingBenefits[activeTab]} onClick={() => loadMoreBenefits(activeTab)}>
          加载更多{activeTab === 'dispose' ? '奖惩' : '礼物'}（{benefits[activeTab === 'dispose' ? 'dispose_records' : 'gift_records'].length}/{benefits.pagination[activeTab].total}）
        </Button>
      )}

      <SurfaceDialog open={showWithdraw} title='申请提现' busy={submitting} onClose={() => setShowWithdraw(false)} footer={<Button className={`xa-button-primary ${styles.confirm}`} disabled={!canWithdraw} loading={submitting} onClick={handleWithdraw}>{submitting ? '提交中…' : '确认提现'}</Button>}>
          <Text className={styles.formLabel}>提现兴安币</Text><View className={styles.moneyInput}><Text>币</Text><Input type='digit' disabled={submitting} maxlength={12} value={withdrawInput} placeholder='0' onInput={e => setWithdrawInput(e.detail.value)} /></View>
          <Text className={styles.formHint}>可用 {formatXaCoin(balance)}币 · {balanceThreshold != null ? `余额大于${formatRmb(balanceThreshold)}元可申请，单次金额不限` : `最低${formatXaCoin(minAmount)}币`} · 本次提现 {formatXaCoin(withdrawAmount)}币</Text>
          {activeQuote ? <Text className={styles.formHint}>税率 {activeQuote.tax_rate}% · 应扣税 {formatXaCoin(activeQuote.tax_amount)}币 · 预计打款人民币 ¥{formatRmb(activeQuote.actual_amount)}</Text>
            : <Text className={styles.formHint}>{previewLoading ? '正在试算到账金额…' : previewError || (withdrawInput ? '请输入有效金额，最多一位小数' : `当前税率 ${taxRate}%，填写金额后试算`)}</Text>}
          {!!previewError && <Button size='mini' disabled={submitting} onClick={() => setQuoteRevision(value => value + 1)}>重新试算</Button>}
          <Text className={styles.formLabel}>收款方式</Text><View className={styles.methodRow}>{PAYEE_METHODS.map(item => <View key={item.value} className={`${styles.method} ${payeeMethod === item.value ? styles.methodActive : ''}`} onClick={() => !submittingRef.current && setPayeeMethod(item.value)}>{item.label}</View>)}</View>
          <View className={styles.selectedAccount}>
            {selectedAccount ? <><Text className={styles.itemTitle}>{selectedAccount.payee_name}</Text><Text className={styles.accountNumber}>{selectedAccount.payee_account}</Text><Text className={styles.formHint}>本次将使用以上收款资料，请核对。</Text></> : <Text className={styles.formHint}>尚未绑定{PAYEE_METHODS.find(item => item.value === payeeMethod)?.label}收款账户</Text>}
            <Button size='mini' disabled={submitting || loading} onClick={() => Taro.navigateTo({ url: '/pages/withdrawAccounts/index?method=' + payeeMethod })}>{selectedAccount ? '修改账户' : '绑定账户'}</Button>
          </View>
      </SurfaceDialog>
    </View></PlatformLayout>
  );
};
export default WalletPage;
