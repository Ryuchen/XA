import React, { useCallback, useState } from 'react';
import { Button, Text, View } from '@tarojs/components';
import Image from '@/components/PrivateImage';
import { previewPrivateImages } from '@/utils/privateMedia';
import Taro, { useDidShow } from '@tarojs/taro';
import PlatformLayout from '@/components/PlatformLayout';
import PageHeading from '@/components/PageHeading';
import { createReport, deleteReportImage, fetchReports, ProviderReport, submitReport, uploadReportImage } from '@/services/report';
import { fetchOrders } from '@/services/order';
import { formatXaCoin } from '@/utils/format';
import styles from './index.module.scss';
import { Empty } from '@/components';

const ReportPage: React.FC = () => {
  const [reports, setReports] = useState<ProviderReport[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchReports();
      if (res.code !== 0) throw new Error(res.msg || '报单加载失败');
      setReports(res.data || []);
      setError('');
    } catch (e) { setError((e as Error).message || '网络异常，请重试'); }
    finally { setLoading(false); }
  }, []);
  useDidShow(load);
  const beginReport = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const response = await fetchOrders({ role: 'provider' });
      if (response.code !== 0) throw new Error(response.msg || '订单加载失败');
      const available = (response.data || []).filter(o => ['GRABBED', 'IN_SERVICE', 'COMPLETED'].includes(o.status) && !reports.some(r => r.order_id === o.id));
      if (!available.length) { Taro.showToast({ title: '暂无可新建报单的订单', icon: 'none' }); return; }
      const { tapIndex } = await Taro.showActionSheet({ itemList: available.slice(0, 6).map(o => `${o.order_no} ${o.service?.name || ''}`) });
      const res = await createReport(available[tapIndex].id);
      if (res.code !== 0) throw new Error(res.msg);
      await load();
    } catch (e) {
      if (!String((e as { errMsg?: string }).errMsg).includes('cancel')) Taro.showToast({ title: (e as Error).message || '操作失败', icon: 'none' });
    } finally { setBusy(false); }
  };
  const upload = async (report: ProviderReport, kind: 'ENTRY' | 'COMPLETION' | 'RESULT') => {
    if (busy) return;
    setBusy(true);
    try {
      const files = await Taro.chooseImage({ count: kind === 'RESULT' ? 9 - report.result_image_urls.length : 1, sizeType: ['compressed'], sourceType: ['album', 'camera'] });
      for (const path of files.tempFilePaths) {
        const res = await uploadReportImage(report.id, path, kind);
        if (res.code !== 0) throw new Error(res.msg);
      }
      await load();
    } catch (e) {
      if (!String((e as { errMsg?: string }).errMsg).includes('cancel')) Taro.showToast({ title: (e as Error).message || '上传失败，请重试', icon: 'none' });
      await load();
    } finally { setBusy(false); }
  };
  const submit = async (report: ProviderReport) => {
    if (busy) return;
    if (!report.entry_image_url || !report.completion_image_url || !report.result_image_urls.length) {
      Taro.showToast({ title: '请补齐入队、结单和战绩截图', icon: 'none' }); return;
    }
    setBusy(true);
    try {
      const res = await submitReport(report.id);
      if (res.code !== 0) throw new Error(res.msg);
      Taro.showToast({ title: '已提交审核', icon: 'success' });
      await load();
    } catch (e) { Taro.showToast({ title: (e as Error).message || '提交失败', icon: 'none' }); }
    finally { setBusy(false); }
  };
  const preview = previewPrivateImages;
  const removeImage = async (report: ProviderReport, imageId: number) => {
    if (busy) return;
    setBusy(true);
    try {
      const response = await deleteReportImage(report.id, imageId);
      if (response.code !== 0) throw new Error(response.msg || '删除失败');
      await load();
    } catch (e) { Taro.showToast({ title: (e as Error).message || '删除失败', icon: 'none' }); }
    finally { setBusy(false); }
  };
  return <PlatformLayout active='report'><View className={`xa-provider-page ${styles.page}`}>
    <PageHeading title='服务报单' hint='完成服务后提交凭证。报单全部通过、售后窗口结束且无未结客诉后自动结算。' action={<Button className={styles.button} disabled={busy} onClick={beginReport}>从订单创建</Button>} />
    {error && <View className={styles.notice}><Text>{error}</Text><Button className={styles.button} onClick={load}>重试</Button></View>}
    {loading && <Text className={styles.hint}>正在加载报单…</Text>}
    {!loading && !reports.length && !error && <View className={styles.card}><Empty title='暂无报单' desc='接单后可创建报单，开始和完成服务时上传的截图会自动同步。' /></View>}
    {reports.map(report => {
      const editable = report.can_edit;
      return <View className={styles.card} key={report.id}>
        <View className={styles.row}><Text className={styles.name}>{report.game_name}</Text><Text className={styles.status}>{report.status_display}</Text></View>
        <Text className={styles.hint}>订单 {report.order_no}</Text>
        {report.submitted_at && <Text className={styles.hint}>有效报单：{report.submitted_at.slice(0,16).replace('T',' ')}，实销从全部有效报单起等待72小时</Text>}
        {report.fulfillment && <Text className={styles.hint}>履约目标：{report.fulfillment}</Text>}
        {report.order_status === 'CANCELLED' && <Text className={styles.hint}>订单已取消，报单流程已停止</Text>}
        {report.edit_block_reason && ['DRAFT', 'REJECTED'].includes(report.status) && report.order_status !== 'CANCELLED' && <Text className={styles.hint}>{report.edit_block_reason}</Text>}
        <View className={styles.row}><Text>打手单钱</Text><Text>{formatXaCoin(report.expected_income)} 币</Text></View>
        {report.status === 'APPROVED' && <Text className={styles.hint}>{report.settled_at ? '收益已结算，可在钱包查看流水' : '凭证已通过，等待全部审核及售后窗口结束'}</Text>}
        {report.audit_remark && <View className={styles.notice}>驳回原因：{report.audit_remark}</View>}
        <View className={styles.evidence}>{([{ kind: 'ENTRY', title: '入队截图', url: report.entry_image_url }, { kind: 'COMPLETION', title: '结单截图', url: report.completion_image_url }] as const).map(item => <View key={item.kind}>
          <Text>{item.title}</Text>{item.url && <Image className={styles.image} src={item.url} mode='aspectFill' onClick={() => preview(item.url, [item.url])} />}
          {editable && <Button className={styles.button} disabled={busy} size='mini' onClick={() => upload(report, item.kind)}>{item.url ? '更换截图' : '上传截图'}</Button>}
        </View>)}</View>
        <Text>战绩截图（{report.result_image_urls.length}/9）</Text>
        <View className={styles.images}>{report.result_images.map(picture => <View key={picture.id}><Image className={styles.image} src={picture.url} mode='aspectFill' onClick={() => preview(picture.url, report.result_image_urls)} />{editable && <Button className={styles.button} size='mini' disabled={busy} onClick={() => removeImage(report, picture.id)}>删除此图</Button>}</View>)}</View>
        {editable && <View className={styles.row}><Button className={styles.button} size='mini' disabled={busy || report.result_image_urls.length >= 9} onClick={() => upload(report, 'RESULT')}>添加战绩截图</Button><Button className={`xa-button-primary ${styles.button} ${styles.primary}`} size='mini' disabled={busy || !report.can_submit} onClick={() => submit(report)}>{report.can_submit ? '提交审核' : '服务完成后提交'}</Button></View>}
      </View>;
    })}
  </View></PlatformLayout>;
};
export default ReportPage;
