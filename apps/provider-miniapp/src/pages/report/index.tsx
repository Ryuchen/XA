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
import { ProviderOrder, statusTextMap, isGiftOrder, orderServiceName } from '@/types/order';
import { formatXaCoin } from '@/utils/format';
import { getSessionRevision, getStoredToken } from '@/utils/auth';
import { takeReportOrder } from '@/utils/reportNavigation';
import { clearOrderFocus, queueOrderFocus } from '@/utils/orderNavigation';
import { createCoalescedRefresh } from '@/utils/coalescedRefresh';
import { wsService } from '@/services/websocket';
import styles from './index.module.scss';
import { Empty } from '@/components';

function missingEvidence(report: ProviderReport): string[] {
  // An earlier team assignment may leave an image that the current
  // assignment must submit again through the order action.
  return [(!report.entry_image_url || !!report.evidence_upload_block_reasons?.ENTRY) && '入队截图',
    (!report.completion_image_url || !!report.evidence_upload_block_reasons?.COMPLETION) && '结单截图',
    !report.result_image_urls.length && '战绩截图'].filter((item): item is string => !!item);
}

const ReportPage: React.FC = () => {
  const [reports, setReports] = useState<ProviderReport[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [availableOrders, setAvailableOrders] = useState<ProviderOrder[] | null>(null);
  const [orderSearch, setOrderSearch] = useState('');
  const [selectedOrderId, setSelectedOrderId] = useState<number | null>(null);
  const [focusedOrderId, setFocusedOrderId] = useState<number | null>(null);
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
      setFocusedOrderId(null);
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
  useDidShow(() => { visible.current = true; const orderId = takeReportOrder(); if (orderId !== null) setFocusedOrderId(orderId); return load(); });
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
    const revision = getSessionRevision();
    const isCurrent = () => mounted.current && revision === getSessionRevision();
    try {
      const response = await fetchOrders({ role: 'provider' });
      if (!isCurrent()) return;
      if (response.code !== 0) throw new Error(response.msg || '订单加载失败');
      const available = (response.data || []).filter(order => ['GRABBED', 'IN_SERVICE', 'COMPLETED'].includes(order.status) && !isGiftOrder(order) && order.report_entry?.available !== false && !reports.some(report => report.order_id === order.id));
      if (!available.length) { Taro.showToast({ title: '暂无可新建报单的订单', icon: 'none' }); return; }
      setOrderSearch('');
      setSelectedOrderId(null);
      setAvailableOrders(available);
    } catch (e) { if (isCurrent()) Taro.showToast({ title: (e as Error).message || '订单加载失败', icon: 'none' }); }
    finally { unlockAction(); }
  };
  const confirmCreate = async () => {
    if (!selectedOrderId || !availableOrders?.some(order => order.id === selectedOrderId) || !lockAction()) return;
    const orderId = selectedOrderId;
    const revision = getSessionRevision();
    const isCurrent = () => mounted.current && revision === getSessionRevision();
    try {
      const res = await createReport(orderId);
      if (!isCurrent()) return;
      if (!res || typeof res.code !== 'number' || res.code >= 500) throw new Error('结果未确认');
      if (res.code !== 0) { Taro.showToast({ title: res.msg || '报单创建失败', icon: 'none' }); return; }
      setAvailableOrders(null);
      setFocusedOrderId(orderId);
      await load();
      if (isCurrent()) Taro.showToast({ title: '报单已创建', icon: 'success' });
    } catch {
      if (isCurrent()) {
        setAvailableOrders(null);
        setFocusedOrderId(orderId);
        Taro.showToast({ title: '创建结果未确认，请核对报单', icon: 'none' });
        await load();
      }
    }
    finally { unlockAction(); }
  };
  const upload = async (report: ProviderReport, kind: 'ENTRY' | 'COMPLETION' | 'RESULT') => {
    const count = kind === 'RESULT' ? Math.max(0, 9 - report.result_image_urls.length) : 1;
    if (!report.can_edit || !count || !lockAction()) return;
    const revision = getSessionRevision();
    let awaitingUpload = false;
    try {
      const files = await Taro.chooseImage({ count, sizeType: ['compressed'], sourceType: ['album', 'camera'] });
      for (const path of files.tempFilePaths) {
        if (!mounted.current || revision !== getSessionRevision()) return;
        awaitingUpload = true;
        const res = await uploadReportImage(report.id, path, kind);
        awaitingUpload = false;
        if (!mounted.current || revision !== getSessionRevision()) return;
        if (res.code !== 0) throw new Error(res.msg || '截图上传失败');
      }
      if (mounted.current && revision === getSessionRevision()) await load();
    } catch (e) {
      if (mounted.current && revision === getSessionRevision()) {
        if (awaitingUpload || !String((e as { errMsg?: string }).errMsg).includes('cancel')) Taro.showToast({ title: awaitingUpload ? '上传结果未确认，请核对截图' : (e as Error).message || '上传失败，请重试', icon: 'none' });
        await load();
      }
    } finally { unlockAction(); }
  };
  const submit = async (report: ProviderReport) => {
    if (!report.can_submit || actionLock.current) return;
    if (missingEvidence(report).length) {
      Taro.showToast({ title: '请补齐入队、结单和战绩截图', icon: 'none' }); return;
    }
    if (!lockAction()) return;
    const revision = getSessionRevision();
    try {
      const res = await submitReport(report.id);
      if (!mounted.current || revision !== getSessionRevision()) return;
      if (!res || typeof res.code !== 'number' || res.code >= 500) throw new Error('结果未确认');
      if (res.code !== 0) {
        Taro.showToast({ title: res.msg || '提交失败', icon: 'none' });
        await load();
        return;
      }
      Taro.showToast({ title: '已提交审核', icon: 'success' });
      await load();
    } catch {
      if (mounted.current && revision === getSessionRevision()) {
        Taro.showToast({ title: '提交结果未确认，请核对报单状态', icon: 'none' });
        await load();
      }
    }
    finally { unlockAction(); }
  };
  const removeImage = async (report: ProviderReport, imageId: number) => {
    if (!report.can_edit || !lockAction()) return;
    const revision = getSessionRevision();
    try {
      const response = await deleteReportImage(report.id, imageId);
      if (!mounted.current || revision !== getSessionRevision()) return;
      if (!response || typeof response.code !== 'number' || response.code >= 500) throw new Error('结果未确认');
      if (response.code !== 0) { Taro.showToast({ title: response.msg || '删除失败', icon: 'none' }); await load(); return; }
      await load();
    } catch {
      if (mounted.current && revision === getSessionRevision()) {
        Taro.showToast({ title: '删除结果未确认，请核对截图', icon: 'none' });
        await load();
      }
    }
    finally { unlockAction(); }
  };
  const openEvidenceOrder = async (report: ProviderReport) => {
    queueOrderFocus(report.order_id);
    try { await Taro.switchTab({ url: '/pages/orders/index' }); }
    catch { clearOrderFocus(); Taro.showToast({ title: '订单页面暂时无法打开', icon: 'none' }); }
  };
  const filteredOrders = (availableOrders || []).filter(order => `${order.order_no} ${orderServiceName(order)}`.toLowerCase().includes(orderSearch.trim().toLowerCase()));
  const visibleReports = focusedOrderId === null ? reports : reports.filter(report => report.order_id === focusedOrderId);

  return <PlatformLayout active='report'><View className={`xa-provider-page ${styles.page}`}>
    <PageHeading title='服务报单' hint='完成服务后提交凭证，收益进度以每笔报单显示为准。' action={<Button className={styles.button} disabled={busy || loading} onClick={beginReport}>{busy ? '处理中…' : '从订单创建'}</Button>} />
    {focusedOrderId !== null && <View className={styles.notice}><Text>正在查看关联订单的报单</Text><Button className={styles.button} size='mini' onClick={() => setFocusedOrderId(null)}>查看全部报单</Button></View>}
    {error && <View className={styles.notice}><Text>{error}</Text><Button className={styles.button} disabled={loading} onClick={load}>重试</Button></View>}
    {loading && <Text className={styles.hint}>正在加载报单…</Text>}
    {!loading && !visibleReports.length && !error && <View className={styles.card}><Empty title={focusedOrderId === null ? '暂无报单' : '未找到关联报单'} desc={focusedOrderId === null ? '接单后可创建报单，开始和完成服务时上传的截图会自动同步。' : '可查看全部报单或刷新后重试。'} /></View>}
    {visibleReports.map(report => {
      const editable = report.can_edit;
      const partialRefund = report.payment_status === 'PARTIALLY_REFUNDED';
      const refund = report.refund_summary;
      const missing = missingEvidence(report);
      return <View className={styles.card} key={report.id}>
        <View className={styles.cardHeader}><View className={styles.orderIdentity}><Text className={styles.name}>{report.service_name || report.game_name}</Text><Text className={styles.hint}>订单 {report.order_no}</Text></View><Text className={`${styles.status} ${styles[`status_${report.status}`]}`}>{report.status_display}</Text></View>
        {report.aftersale_status === 'OPEN' && <View className={styles.notice}>{report.settled_at ? '售后处理中，请关注处理结果；已结算收益以钱包流水为准。' : '售后处理中，本单暂不结算；处理后仍需满足结算条件。'}</View>}
        {report.fulfillment && <Text className={styles.fulfillment}>履约目标：{report.fulfillment}</Text>}
        <View className={styles.incomeRow}><View><Text className={styles.incomeLabel}>{partialRefund || report.order_status === 'CANCELLED' ? report.settled_at ? '原核定单钱' : '原预计单钱' : '打手单钱'}</Text><Text className={styles.incomeHint}>{report.expected_income == null ? '收益待核对，请联系客服核对' : partialRefund ? '订单部分退款，保留剩余净收益' : report.order_status === 'CANCELLED' ? '历史金额仅供核对，净收益以钱包为准' : '按本单实际收益结算'}</Text></View><Text className={styles.income}>{report.expected_income == null ? '待核对' : <>{formatXaCoin(report.expected_income)}<Text className={styles.unit}> 兴安币</Text></>}</Text></View>
        {report.submitted_at && <Text className={styles.hint}>提交时间：{report.submitted_at.slice(0, 16).replace('T', ' ')}{!report.settled_at && report.order_status !== 'CANCELLED' ? report.status === 'REJECTED' ? report.can_edit ? '；实销计时已停止，补充凭证并重提后，全部应提交报单有效才重新起算' : '' : '；实销从全部应提交报单有效时起等待72小时' : ''}</Text>}
        {report.order_status === 'CANCELLED' && <View className={styles.notice}>订单已取消，报单流程已停止</View>}
        {report.edit_block_reason && ['DRAFT', 'REJECTED'].includes(report.status) && report.order_status !== 'CANCELLED' && <View className={styles.notice}>{report.edit_block_reason}</View>}
        {report.status === 'APPROVED' && <View className={styles.approvedNotice}>{partialRefund ? report.settled_at ? '订单已部分退款，剩余净收益及冻结以钱包流水为准' : '订单已部分退款，收益待核对，请联系客服核对' : report.order_status === 'CANCELLED' ? report.settled_at ? '订单已退款，原结算收益已按退款规则冲回，实际净收益以钱包流水为准' : '订单已取消/退款，不再结算收益' : report.expected_income == null ? '凭证已通过，收益待核对，请联系客服核对' : report.settlement_status === 'SETTLED' ? report.expected_income === 0 ? '已结算，本单收益为 0' : '收益已记入内部钱包，冻结及可用金额以钱包为准' : report.settlement_status === 'WAITING' ? '凭证已通过，等待全部审核及售后窗口结束' : '凭证已通过，收益待核对，请联系客服核对'}</View>}
        {refund && <View className={styles.notice}><View><Text className={styles.sectionTitle}>{partialRefund ? '部分退款核对' : '退款核对'}</Text><Text className={styles.incomeHint}>订单累计已退 {formatXaCoin(refund.amount)}币{refund.remaining_amount != null ? ` · 剩余 ${formatXaCoin(refund.remaining_amount)}币` : ''}</Text>
          {refund.recovered_amount != null && <Text className={styles.incomeHint}>服务收益累计追回 {formatXaCoin(refund.recovered_amount)}币</Text>}
          {refund.net_income != null && <Text className={styles.incomeHint}>服务净收益 {formatXaCoin(refund.net_income)}币</Text>}
          {!!refund.referral_recovered_amount && <Text className={styles.incomeHint}>推荐分佣追回 {formatXaCoin(refund.referral_recovered_amount)}币</Text>}
          {!!refund.referral_net_income && <Text className={styles.incomeHint}>推荐分佣净额 {formatXaCoin(refund.referral_net_income)}币</Text>}
          {refund.income_breakdown_unavailable && <Text className={styles.incomeHint}>收益明细待核对，请查看钱包流水或联系客服</Text>}
          {refund.remaining_frozen_amount != null && <Text className={styles.incomeHint}>本单剩余冻结 {formatXaCoin(refund.remaining_frozen_amount)}币</Text>}
          {refund.records?.map(record => <Text className={styles.incomeHint} key={record.id}>退款 #{record.id} · {formatXaCoin(record.amount)}币{record.recovered_amount != null ? ` · 服务收益追回 ${formatXaCoin(record.recovered_amount)}币` : ''}{record.referral_recovered_amount ? ` · 推荐分佣追回 ${formatXaCoin(record.referral_recovered_amount)}币` : ''} · {new Date(record.created_at).toLocaleString()}</Text>)}
        </View></View>}
        {report.audit_remark && <View className={`${styles.notice} ${styles.rejectedNotice}`}>审核备注：{report.audit_remark}</View>}
        <View className={styles.sectionHeading}><Text className={styles.sectionTitle}>服务凭证</Text><Text className={styles.hint}>{3 - missing.length}/3 类已就绪</Text></View>
        <View className={styles.evidence}>{([{ kind: 'ENTRY', title: '入队截图', url: report.entry_image_url }, { kind: 'COMPLETION', title: '结单截图', url: report.completion_image_url }] as const).map(item => <View className={styles.evidenceItem} key={item.kind}>
          <Text className={styles.evidenceTitle}>{item.title}{item.url && report.evidence_upload_block_reasons?.[item.kind] ? '（需重新提交）' : ''}</Text>
          {item.url ? <Button className={styles.imageButton} aria-label={`预览${item.title}`} onClick={() => previewPrivateImages(item.url, [item.url])}><Image className={styles.image} src={item.url} mode='aspectFill' /></Button> : <View className={styles.imagePlaceholder}><Text>暂无截图</Text></View>}
          {editable && (report.evidence_upload_block_reasons?.[item.kind]
            ? <><Text className={styles.hint}>{report.evidence_upload_block_reasons[item.kind]}</Text><Button className={styles.button} disabled={busy} size='mini' onClick={() => openEvidenceOrder(report)}>前往订单</Button></>
            : <Button className={styles.button} disabled={busy} size='mini' onClick={() => upload(report, item.kind)}>{item.url ? '更换截图' : '上传截图'}</Button>)}
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
        <View className={styles.optionIdentity}><Text className={styles.optionName}>{orderServiceName(order)}</Text><Text className={styles.optionNo}>{order.order_no}</Text></View><Text className={styles.optionState}>{selectedOrderId === order.id ? '已选择' : statusTextMap[order.status]}</Text>
      </Button>)}</View>
      {!filteredOrders.length && <Empty title='没有匹配订单' desc='请调整订单号或服务名称后重试。' />}
    </SurfaceDialog>
  </View></PlatformLayout>;
};
export default ReportPage;
