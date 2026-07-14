import React, { useEffect, useMemo, useState } from 'react';
import { Button, Image, ScrollView, Text, View } from '@tarojs/components';
import Taro, { useDidShow, usePullDownRefresh } from '@tarojs/taro';
import { fetchOrders } from '@/services/order';
import { ProviderOrder } from '@/types/order';
import {
  createOrderReport, fetchReports, ReportRecord, ReportStatus,
  submitOrderReport, uploadReportImage,
} from '@/services/wallet';
import { formatXaCoin } from '@/utils/format';
import { Empty, Skeleton } from '@/components';
import PlatformLayout from '@/components/PlatformLayout';
import styles from './index.module.scss';

const STATUS_LABEL: Record<ReportStatus, string> = {
  DRAFT: '待提交', PENDING: '审核中', APPROVED: '已通过', REJECTED: '需补充',
};

const ReportPage: React.FC = () => {
  const [orders, setOrders] = useState<ProviderOrder[]>([]);
  const [reports, setReports] = useState<ReportRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<ProviderOrder | null>(null);
  const [entryPath, setEntryPath] = useState('');
  const [completionPath, setCompletionPath] = useState('');
  const [resultPaths, setResultPaths] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [orderRes, reportRes] = await Promise.all([
        fetchOrders({ status: 'completed', role: 'provider' }), fetchReports(),
      ]);
      if (orderRes.code === 0 && orderRes.data) setOrders(orderRes.data);
      if (reportRes.code === 0 && reportRes.data) setReports(reportRes.data);
    } finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);
  useDidShow(() => { load(); });
  usePullDownRefresh(async () => { await load(); Taro.stopPullDownRefresh(); });

  const reportMap = useMemo(() => new Map(reports.filter(item => item.order_id).map(item => [item.order_id, item])), [reports]);
  const submittedCount = reports.filter(item => item.order_id && item.status !== 'DRAFT').length;
  const selectedReport = selected ? reportMap.get(selected.id) : undefined;
  const canSubmit = !!selected
    && !!(entryPath || selectedReport?.entry_image_url)
    && !!(completionPath || selectedReport?.completion_image_url)
    && !!(resultPaths.length || selectedReport?.result_image_urls?.length);
  const openUpload = (order: ProviderOrder) => {
    const report = reportMap.get(order.id);
    if (report?.status === 'PENDING' || report?.status === 'APPROVED') return;
    setSelected(order); setEntryPath(''); setCompletionPath(''); setResultPaths([]);
  };
  const chooseSingle = async (setter: (path: string) => void) => {
    const res = await Taro.chooseImage({ count: 1, sizeType: ['compressed'] }).catch(() => null);
    if (res?.tempFilePaths?.[0]) setter(res.tempFilePaths[0]);
  };
  const chooseResults = async () => {
    const remaining = Math.max(1, 9 - resultPaths.length);
    const res = await Taro.chooseImage({ count: remaining, sizeType: ['compressed'] }).catch(() => null);
    if (res?.tempFilePaths?.length) setResultPaths(prev => [...prev, ...res.tempFilePaths].slice(0, 9));
  };
  const handleSubmit = async () => {
    if (!selected || submitting) return;
    const existing = reportMap.get(selected.id);
    if (!entryPath && !existing?.entry_image_url) return void Taro.showToast({ title: '请上传入队截图', icon: 'none' });
    if (!completionPath && !existing?.completion_image_url) return void Taro.showToast({ title: '请上传结单截图', icon: 'none' });
    if (!resultPaths.length && !existing?.result_image_urls?.length) return void Taro.showToast({ title: '请上传至少一张战绩截图', icon: 'none' });
    setSubmitting(true); Taro.showLoading({ title: '上传中' });
    try {
      const draftRes = await createOrderReport(selected.id);
      if (draftRes.code !== 0 || !draftRes.data) throw new Error(draftRes.msg || '创建报单失败');
      const reportId = draftRes.data.id;
      if (entryPath) await uploadReportImage(reportId, 'ENTRY', entryPath);
      if (completionPath) await uploadReportImage(reportId, 'COMPLETION', completionPath);
      for (const path of resultPaths) await uploadReportImage(reportId, 'RESULT', path);
      const submitRes = await submitOrderReport(reportId);
      if (submitRes.code !== 0) throw new Error(submitRes.msg || '提交失败');
      setSelected(null); await load();
      Taro.showToast({ title: '报单已提交', icon: 'success' });
    } catch (error) { Taro.showToast({ title: error instanceof Error ? error.message : '提交失败', icon: 'none' }); }
    finally { Taro.hideLoading(); setSubmitting(false); }
  };

  const uploadBox = (label: string, path: string, onClick: () => void) => path
    ? <Image className={styles.uploadPreview} src={path} mode='aspectFill' onClick={onClick} />
    : <View className={styles.uploadBox} onClick={onClick}><Text className={styles.uploadPlus}>+</Text><Text className={styles.uploadHint}>{label}</Text></View>;

  return <PlatformLayout active='report'><View className={styles.container}>
    <View className={styles.topBar}><View><Text className={styles.topEyebrow}>已完成订单凭证</Text><Text className={styles.pageTitle}>订单报单</Text><Text className={styles.topMeta}>已完成 {orders.length} 单 · 已报 {submittedCount} 单</Text></View></View>
    <View className={styles.guide}><Text className={styles.guideTitle}>报单材料</Text><Text className={styles.guideText}>每笔完成订单需上传入队、结单以及至少一张战绩截图，提交后由后台审核。</Text></View>
    <ScrollView className={styles.list} scrollY>
      {loading ? <Skeleton variant='card-list' rows={3} /> : orders.length === 0 ? <Empty icon='📝' title='暂无已完成订单' desc='完成服务后，订单会自动出现在这里' /> : orders.map(order => {
        const report = reportMap.get(order.id);
        const status = report?.status || 'DRAFT';
        return <View className={styles.reportCard} key={order.id}>
          <View className={styles.cardHeader}><View><Text className={styles.gameName}>{order.service?.name || '陪玩服务'}</Text><Text className={styles.orderNo}>{order.order_no}</Text></View><Text className={`${styles.status} ${styles[`status_${status}`]}`}>{report ? STATUS_LABEL[status] : '待报单'}</Text></View>
          <View className={styles.amountRow}><Text className={styles.amountLabel}>订单金额</Text><Text className={styles.amountValue}>{formatXaCoin(order.amount)}币</Text></View>
          <View className={styles.amountRow}><Text className={styles.amountLabel}>到手金额</Text><Text className={styles.payoutValue}>{formatXaCoin(order.expected_income)}币</Text></View>
          {report?.audit_remark && <Text className={styles.rejectReason}>审核意见：{report.audit_remark}</Text>}
          {report && <View className={styles.evidenceRow}><Text>入队 {report.entry_image_url ? '✓' : '—'}</Text><Text>结单 {report.completion_image_url ? '✓' : '—'}</Text><Text>战绩 {report.result_image_urls.length} 张</Text></View>}
          <View className={styles.cardFooter}><Text className={styles.time}>{new Date(order.created_at).toLocaleString()}</Text>{status !== 'PENDING' && status !== 'APPROVED' && <Button className={styles.reportButton} onClick={() => openUpload(order)}>{report?.status === 'REJECTED' ? '补充材料' : '上传报单'}</Button>}</View>
        </View>;
      })}
    </ScrollView>
    {selected && <View className={styles.mask} onClick={() => !submitting && setSelected(null)}><View className={styles.sheet} onClick={e => e.stopPropagation()}><View className={styles.handle}/><Text className={styles.sheetTitle}>上传订单凭证</Text><Text className={styles.sheetOrder}>{selected.service?.name} · {selected.order_no}</Text>
      <Text className={styles.fieldTitle}>入队截图 <Text>*</Text></Text>{uploadBox('上传入队截图', entryPath, () => chooseSingle(setEntryPath))}
      <Text className={styles.fieldTitle}>结单截图 <Text>*</Text></Text>{uploadBox('上传结单截图', completionPath, () => chooseSingle(setCompletionPath))}
      <Text className={styles.fieldTitle}>战绩截图（最多9张）<Text>*</Text></Text><View className={styles.resultGrid}>{resultPaths.map((path,index)=><Image key={path} className={styles.uploadPreview} src={path} mode='aspectFill' onClick={()=>setResultPaths(prev=>prev.filter((_,i)=>i!==index))}/>) }{resultPaths.length<9&&uploadBox('添加战绩', '', chooseResults)}</View>
      <Button className={styles.submitBtn} disabled={submitting || !canSubmit} loading={submitting} onClick={handleSubmit}>提交审核</Button><Button className={styles.cancelBtn} disabled={submitting} onClick={()=>setSelected(null)}>取消</Button>
    </View></View>}
  </View></PlatformLayout>;
};
export default ReportPage;
