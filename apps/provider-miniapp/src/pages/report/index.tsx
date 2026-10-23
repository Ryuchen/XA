import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Input, Text, View } from '@tarojs/components';
import Image from '@/components/PrivateImage';
import { previewPrivateImages } from '@/utils/privateMedia';
import Taro, { useDidHide, useDidShow, usePullDownRefresh } from '@tarojs/taro';
import PlatformLayout from '@/components/PlatformLayout';
import PageHeading from '@/components/PageHeading';
import SurfaceDialog from '@/components/SurfaceDialog';
import { createReport, deleteReportImage, fetchReports, ProviderReport, submitReport, uploadReportImage } from '@/services/report';
import { fetchOrders } from '@/services/order';
import { ProviderOrder, statusTextMap } from '@/types/order';
import { formatXaCoin } from '@/utils/format';
import { getSessionRevision, getStoredToken } from '@/utils/auth';
import { createCoalescedRefresh } from '@/utils/coalescedRefresh';
import { wsService } from '@/services/websocket';
import styles from './index.module.scss';
import { Empty } from '@/components';

const ReportPage: React.FC = () => {
  const [reports, setReports] = useState<ProviderReport[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [availableOrders, setAvailableOrders] = useState<ProviderOrder[] | null>(null);
  const [orderSearch, setOrderSearch] = useState('');
  const [selectedOrderId, setSelectedOrderId] = useState<number | null>(null);
  const actionLock = useRef(false);
  const loadSequence = useRef(0);
  const mounted = useRef(true);
  const visible = useRef(true);
  const sessionRevision = useRef(getSessionRevision());
  const load = useCallback(async () => {
    if (!mounted.current || !getStoredToken()) return;
    const sequence = ++loadSequence.current;
    const revision = getSessionRevision();
    const isCurrent = () => mounted.current && sequence === loadSequence.current && revision === getSessionRevision();
    setLoading(true);
    try {
      const res = await fetchReports();
      if (!isCurrent()) return;
      if (res.code !== 0) throw new Error(res.msg || '报单加载失败');
      setReports(res.data || []);
      setError('');
    } catch (e) {
      if (isCurrent()) setError((e as Error).message || '网络异常，请重试');
    } finally { if (isCurrent()) setLoading(false); }
  }, []);
  useEffect(() => {
    mounted.current = true;
    const refresh = createCoalescedRefresh(async () => {
      if (visible.current) await load();
    });
    const requestRefresh = () => {
      if (visible.current && getStoredToken()) refresh.request();
    };
    const offReport = wsService.on('report_status_update', requestRefresh);
    const offOrder = wsService.on('order_status_update', requestRefresh);
    // Pub/sub does not replay audits missed while this socket was offline.
    const offConnected = wsService.on('connected', requestRefresh);
    const onAuthChanged = () => {
      const revision = getSessionRevision();
      if (revision === sessionRevision.current) return; // Access-token refresh keeps the same account.
      sessionRevision.current = revision;
      ++loadSequence.current;
      setReports([]);
      setAvailableOrders(null);
      setOrderSearch('');
      setSelectedOrderId(null);
      setError('');
      setLoading(false);
      if (visible.current && getStoredToken()) void load();
    };
    Taro.eventCenter.on('xa:auth.changed', onAuthChanged);
    return () => {
      mounted.current = false;
      visible.current = false;
      ++loadSequence.current;
      offReport();
      offOrder();
      offConnected();
      refresh.dispose();
      Taro.eventCenter.off('xa:auth.changed', onAuthChanged);
    };
  }, [load]);
  useDidHide(() => { visible.current = false; });
  useDidShow(() => { visible.current = true; return load(); });
  usePullDownRefresh(async () => {
    try { await load(); } finally { Taro.stopPullDownRefresh(); }
  });

  const lockAction = () => {
    if (actionLock.current) return false;
    actionLock.current = true;
    setBusy(true);
    return true;
  };
  const unlockAction = () => { actionLock.current = false; setBusy(false); };
  const beginReport = async () => {
    if (!lockAction()) return;
    try {
      const response = await fetchOrders({ role: 'provider' });
      if (response.code !== 0) throw new Error(response.msg || '订单加载失败');
      const available = (response.data || []).filter(order => ['GRABBED', 'IN_SERVICE', 'COMPLETED'].includes(order.status) && !order.service?.is_gift && !order.source_order && !reports.some(report => report.order_id === order.id));
      if (!available.length) { Taro.showToast({ title: '暂无可新建报单的订单', icon: 'none' }); return; }
      setOrderSearch('');
      setSelectedOrderId(null);
      setAvailableOrders(available);
    } catch (e) { Taro.showToast({ title: (e as Error).message || '订单加载失败', icon: 'none' }); }
    finally { unlockAction(); }
  };
  const confirmCreate = async () => {
    if (!selectedOrderId || !availableOrders?.some(order => order.id === selectedOrderId) || !lockAction()) return;
    try {
      const res = await createReport(selectedOrderId);
      if (res.code !== 0) throw new Error(res.msg || '报单创建失败');
      setAvailableOrders(null);
      await load();
      Taro.showToast({ title: '报单已创建', icon: 'success' });
    } catch (e) { Taro.showToast({ title: (e as Error).message || '报单创建失败', icon: 'none' }); }
    finally { unlockAction(); }
  };
  const upload = async (report: ProviderReport, kind: 'ENTRY' | 'COMPLETION' | 'RESULT') => {
    const count = kind === 'RESULT' ? Math.max(0, 9 - report.result_image_urls.length) : 1;
    if (!report.can_edit || !count || !lockAction()) return;
    try {
      const files = await Taro.chooseImage({ count, sizeType: ['compressed'], sourceType: ['album', 'camera'] });
      for (const path of files.tempFilePaths) {
        const res = await uploadReportImage(report.id, path, kind);
        if (res.code !== 0) throw new Error(res.msg || '截图上传失败');
      }
      await load();
    } catch (e) {
      if (!String((e as { errMsg?: string }).errMsg).includes('cancel')) Taro.showToast({ title: (e as Error).message || '上传失败，请重试', icon: 'none' });
      await load();
    } finally { unlockAction(); }
  };
  const submit = async (report: ProviderReport) => {
    if (!report.can_submit || actionLock.current) return;
    if (!report.entry_image_url || !report.completion_image_url || !report.result_image_urls.length) {
      Taro.showToast({ title: '请补齐入队、结单和战绩截图', icon: 'none' }); return;
    }
    if (!lockAction()) return;
    try {
      const res = await submitReport(report.id);
      if (res.code !== 0) throw new Error(res.msg || '提交失败');
      Taro.showToast({ title: '已提交审核', icon: 'success' });
      await load();
    } catch (e) { Taro.showToast({ title: (e as Error).message || '提交失败', icon: 'none' }); }
    finally { unlockAction(); }
  };
  const removeImage = async (report: ProviderReport, imageId: number) => {
    if (!report.can_edit || !lockAction()) return;
    try {
      const response = await deleteReportImage(report.id, imageId);
      if (response.code !== 0) throw new Error(response.msg || '删除失败');
      await load();
    } catch (e) { Taro.showToast({ title: (e as Error).message || '删除失败', icon: 'none' }); }
    finally { unlockAction(); }
  };
  const filteredOrders = (availableOrders || []).filter(order => `${order.order_no} ${order.service?.name || ''}`.toLowerCase().includes(orderSearch.trim().toLowerCase()));

  return <PlatformLayout active='report'><View className={`xa-provider-page ${styles.page}`}>
    <PageHeading title='服务报单' hint='完成服务后提交凭证。报单全部通过、售后窗口结束且无未结客诉后自动结算。' action={<Button className={styles.button} disabled={busy || loading} onClick={beginReport}>{busy ? '处理中…' : '从订单创建'}</Button>} />
    {error && <View className={styles.notice}><Text>{error}</Text><Button className={styles.button} disabled={loading} onClick={load}>重试</Button></View>}
    {loading && <Text className={styles.hint}>正在加载报单…</Text>}
    {!loading && !reports.length && !error && <View className={styles.card}><Empty title='暂无报单' desc='接单后可创建报单，开始和完成服务时上传的截图会自动同步。' /></View>}
    {reports.map(report => {
      const editable = report.can_edit;
      const partialRefund = report.payment_status === 'PARTIALLY_REFUNDED';
      const refund = report.refund_summary;
      const missing = [!report.entry_image_url && '入队截图', !report.completion_image_url && '结单截图', !report.result_image_urls.length && '战绩截图'].filter(Boolean);
      return <View className={styles.card} key={report.id}>
        <View className={styles.cardHeader}><View className={styles.orderIdentity}><Text className={styles.name}>{report.service_name || report.game_name}</Text><Text className={styles.hint}>订单 {report.order_no}</Text></View><Text className={`${styles.status} ${styles[`status_${report.status}`]}`}>{report.status_display}</Text></View>
        {report.fulfillment && <Text className={styles.fulfillment}>履约目标：{report.fulfillment}</Text>}
        <View className={styles.incomeRow}><View><Text className={styles.incomeLabel}>{partialRefund || report.order_status === 'CANCELLED' ? report.settled_at ? '原核定单钱' : '原预计单钱' : '打手单钱'}</Text><Text className={styles.incomeHint}>{partialRefund ? '订单部分退款，保留剩余净收益' : report.order_status === 'CANCELLED' ? '历史金额仅供核对，净收益以钱包为准' : '按本单实际收益结算'}</Text></View><Text className={styles.income}>{formatXaCoin(report.expected_income)}<Text className={styles.unit}> 兴安币</Text></Text></View>
        {report.submitted_at && <Text className={styles.hint}>提交时间：{report.submitted_at.slice(0, 16).replace('T', ' ')}{!report.settled_at && report.order_status !== 'CANCELLED' ? '；实销从全部有效报单起等待72小时' : ''}</Text>}
        {report.order_status === 'CANCELLED' && <View className={styles.notice}>订单已取消，报单流程已停止</View>}
        {report.edit_block_reason && ['DRAFT', 'REJECTED'].includes(report.status) && report.order_status !== 'CANCELLED' && <View className={styles.notice}>{report.edit_block_reason}</View>}
        {report.status === 'APPROVED' && <View className={styles.approvedNotice}>{partialRefund ? report.settled_at ? '订单已部分退款，剩余净收益及冻结以钱包流水为准' : '订单已部分退款，后续按剩余金额结算，实际净收益以钱包为准' : report.order_status === 'CANCELLED' ? report.settled_at ? '订单已退款，原结算收益已按退款规则冲回，实际净收益以钱包流水为准' : '订单已取消/退款，不再结算收益' : report.settled_at ? '收益已结算，可在钱包查看流水' : '凭证已通过，等待全部审核及售后窗口结束'}</View>}
        {refund && <View className={styles.notice}><View><Text className={styles.sectionTitle}>{partialRefund ? '部分退款核对' : '退款核对'}</Text><Text className={styles.incomeHint}>订单累计已退 {formatXaCoin(refund.amount)}币{refund.remaining_amount != null ? ` · 剩余 ${formatXaCoin(refund.remaining_amount)}币` : ''}</Text>
          {refund.recovered_amount != null && <Text className={styles.incomeHint}>本人累计追回收益 {formatXaCoin(refund.recovered_amount)}币</Text>}
          {refund.net_income != null && <Text className={styles.incomeHint}>本单实际净收益 {formatXaCoin(refund.net_income)}币</Text>}
          {refund.remaining_frozen_amount != null && <Text className={styles.incomeHint}>本单剩余冻结 {formatXaCoin(refund.remaining_frozen_amount)}币</Text>}
          {refund.records?.map(record => <Text className={styles.incomeHint} key={record.id}>退款 #{record.id} · {formatXaCoin(record.amount)}币{record.recovered_amount != null ? ` · 本人追回 ${formatXaCoin(record.recovered_amount)}币` : ''} · {new Date(record.created_at).toLocaleString()}</Text>)}
        </View></View>}
        {report.audit_remark && <View className={`${styles.notice} ${styles.rejectedNotice}`}>审核备注：{report.audit_remark}</View>}
        <View className={styles.sectionHeading}><Text className={styles.sectionTitle}>服务凭证</Text><Text className={styles.hint}>{3 - missing.length}/3 类已上传</Text></View>
        <View className={styles.evidence}>{([{ kind: 'ENTRY', title: '入队截图', url: report.entry_image_url }, { kind: 'COMPLETION', title: '结单截图', url: report.completion_image_url }] as const).map(item => <View className={styles.evidenceItem} key={item.kind}>
          <Text className={styles.evidenceTitle}>{item.title}</Text>
          {item.url ? <Button className={styles.imageButton} aria-label={`预览${item.title}`} onClick={() => previewPrivateImages(item.url, [item.url])}><Image className={styles.image} src={item.url} mode='aspectFill' /></Button> : <View className={styles.imagePlaceholder}><Text>暂无截图</Text></View>}
          {editable && <Button className={styles.button} disabled={busy} size='mini' onClick={() => upload(report, item.kind)}>{item.url ? '更换截图' : '上传截图'}</Button>}
        </View>)}</View>
        <View className={styles.sectionHeading}><Text className={styles.sectionTitle}>战绩截图</Text><Text className={styles.hint}>{report.result_image_urls.length}/9 张</Text></View>
        <View className={styles.images}>{report.result_images.map((picture, index) => <View className={styles.resultItem} key={picture.id}><Button className={styles.imageButton} aria-label={`预览第 ${index + 1} 张战绩截图`} onClick={() => previewPrivateImages(picture.url, report.result_image_urls)}><Image className={styles.image} src={picture.url} mode='aspectFill' /></Button>{editable && <Button className={styles.button} size='mini' disabled={busy} aria-label={`删除第 ${index + 1} 张战绩截图`} onClick={() => removeImage(report, picture.id)}>删除此图</Button>}</View>)}</View>
        {!report.result_image_urls.length && <Text className={styles.hint}>至少上传一张战绩截图</Text>}
        {editable && <View className={styles.reportActions}><View><Button className={styles.button} size='mini' disabled={busy || report.result_image_urls.length >= 9} onClick={() => upload(report, 'RESULT')}>添加战绩截图</Button><Text className={styles.actionHint}>{missing.length ? `待补齐：${missing.join('、')}` : '三类凭证已齐全'}</Text></View><Button className={`xa-button-primary ${styles.button} ${styles.primary}`} disabled={busy || !report.can_submit || !!missing.length} onClick={() => submit(report)}>{!report.can_submit ? '服务完成后提交' : busy ? '处理中…' : '提交审核'}</Button></View>}
      </View>;
    })}
    <SurfaceDialog open={availableOrders !== null} title='选择报单订单' busy={busy} onClose={() => setAvailableOrders(null)} footer={<Button className={`xa-button-primary ${styles.button} ${styles.primary}`} disabled={busy || selectedOrderId === null} onClick={confirmCreate}>{busy ? '创建中…' : '创建报单'}</Button>}>
      <Text className={styles.hint}>请选择需要补充服务凭证的订单，已有报单可在列表中继续编辑。</Text>
      <Input className={styles.searchInput} aria-label='搜索订单号或服务名称' placeholder='搜索订单号或服务名称' value={orderSearch} onInput={event => setOrderSearch(event.detail.value)} />
      <Text className={styles.selectionSummary}>{filteredOrders.length} 笔可选订单{selectedOrderId !== null ? ' · 已选择 1 笔' : ''}</Text>
      <View className={styles.orderOptions}>{filteredOrders.map(order => <Button key={order.id} className={`${styles.orderOption} ${selectedOrderId === order.id ? styles.orderOptionSelected : ''}`} aria-pressed={selectedOrderId === order.id} disabled={busy} onClick={() => setSelectedOrderId(order.id)}>
        <View className={styles.optionIdentity}><Text className={styles.optionName}>{order.service?.name || '陪玩服务'}</Text><Text className={styles.optionNo}>{order.order_no}</Text></View><Text className={styles.optionState}>{selectedOrderId === order.id ? '已选择' : statusTextMap[order.status]}</Text>
      </Button>)}</View>
      {!filteredOrders.length && <Empty title='没有匹配订单' desc='请调整订单号或服务名称后重试。' />}
    </SurfaceDialog>
  </View></PlatformLayout>;
};
export default ReportPage;
