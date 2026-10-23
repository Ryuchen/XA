import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Text, View } from '@tarojs/components';
import Taro, { usePullDownRefresh } from '@tarojs/taro';
import { fetchMyEvaluations, replyEvaluation } from '@/services/order';
import { Evaluation } from '@/types/order';
import styles from './index.module.scss';
import PageNav from '@/components/PageNav';
import PlatformLayout from '@/components/PlatformLayout';
import ReplyDialog from '@/components/ReplyDialog';

const EvaluationPage: React.FC = () => {
  const [items, setItems] = useState<Evaluation[]>([]);
  const [loading, setLoading] = useState(true);
  const [replying, setReplying] = useState<Evaluation | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [loadError, setLoadError] = useState('');
  const requestVersion = useRef(0);
  const submitLock = useRef(false);

  const loadData = async () => {
    const version = ++requestVersion.current;
    setLoadError('');
    try {
      const res = await fetchMyEvaluations();
      if (version !== requestVersion.current) return;
      if (res.code === 0 && res.data) setItems(res.data.list || []);
      else throw new Error(res.msg || '评价加载失败');
    } catch (error) {
      if (version === requestVersion.current) setLoadError(error instanceof Error ? error.message : '评价加载失败');
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  };

  useEffect(() => { loadData(); return () => { requestVersion.current++; }; }, []);
  usePullDownRefresh(async () => { await loadData(); Taro.stopPullDownRefresh(); });

  const pending = useMemo(() => items.filter(item => !item.reply_content).length, [items]);
  const openReply = (item: Evaluation) => {
    setReplying(item);
  };

  const submitReply = async (content: string) => {
    if (!replying || submitLock.current) return;
    submitLock.current = true;
    const evaluationId = replying.id;
    setSubmitting(true);
    try {
      const res = await replyEvaluation(evaluationId, content);
      if (res.code !== 0 || !res.data) throw new Error(res.msg || '回复失败');
      requestVersion.current++;
      setItems(prev => prev.map(item => item.id === evaluationId ? res.data! : item));
      setReplying(null);
      Taro.showToast({ title: '回复已发布', icon: 'success' });
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : '回复失败', icon: 'none' });
    } finally {
      submitLock.current = false;
      setSubmitting(false);
    }
  };

  return (
    <PlatformLayout active='mine'><View className={`xa-provider-page ${styles.page}`}>
      <PageNav title="评价管理" />
      <View className={styles.summary}>
        <View><Text className={styles.summaryValue}>{items.length}</Text><Text className={styles.summaryLabel}>全部评价</Text></View>
        <View><Text className={styles.summaryValue}>{pending}</Text><Text className={styles.summaryLabel}>待回复</Text></View>
      </View>

      {loading ? <Text className={styles.empty}>加载中…</Text> : loadError ? <View className={styles.emptyCard}><Text className={styles.emptyText}>{loadError}</Text><Button onClick={loadData}>重新加载</Button></View> : items.length === 0 ? (
        <View className={styles.emptyCard}><Text className={styles.emptyTitle}>暂无评价</Text><Text className={styles.emptyText}>老板完成评价后会显示在这里</Text></View>
      ) : items.map(item => (
        <View className={styles.card} key={item.id}>
          <View className={styles.head}>
            <View><Text className={styles.name}>{item.customer_name || '老板'}</Text><Text className={styles.service}>{item.service_name || item.order_no}</Text></View>
            <Text className={styles.score}>{'★'.repeat(Math.round(item.score))} {item.score.toFixed(1)}</Text>
          </View>
          <Text className={styles.content}>{item.content || '老板未填写文字评价'}</Text>
          <Text className={styles.time}>{new Date(item.created_at).toLocaleString()}</Text>
          {item.reply_content ? (
            <View className={styles.reply}><Text className={styles.replyLabel}>我的回复</Text><Text className={styles.replyContent}>{item.reply_content}</Text></View>
          ) : (
            <Button className={styles.replyButton} onClick={() => openReply(item)}>回复评价</Button>
          )}
        </View>
      ))}

      <ReplyDialog
        open={!!replying}
        title={`回复 ${replying?.customer_name || '老板'} 的评价`}
        initialValue={replying?.reply_content || ''}
        placeholder='感谢老板的认可，也可以说明本次服务情况…'
        submitting={submitting}
        onClose={() => setReplying(null)}
        onSubmit={submitReply}
      />
    </View></PlatformLayout>
  );
};

export default EvaluationPage;
