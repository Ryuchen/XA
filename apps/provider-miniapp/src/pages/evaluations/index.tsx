import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Text, View } from '@tarojs/components';
import Taro, { useDidHide, useDidShow, usePullDownRefresh } from '@tarojs/taro';
import { fetchMyEvaluations, replyEvaluation } from '@/services/order';
import { Evaluation } from '@/types/order';
import styles from './index.module.scss';
import PageNav from '@/components/PageNav';
import PlatformLayout from '@/components/PlatformLayout';
import ReplyDialog from '@/components/ReplyDialog';
import { getSessionRevision } from '@/utils/auth';
import { createCoalescedRefresh, isPersonalOrderUpdate } from '@/utils/coalescedRefresh';
import { wsService } from '@/services/websocket';

const EvaluationPage: React.FC = () => {
  const [items, setItems] = useState<Evaluation[]>([]);
  const [loading, setLoading] = useState(true);
  const [replying, setReplying] = useState<Evaluation | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [replyConflict, setReplyConflict] = useState(false);
  const [loadError, setLoadError] = useState('');
  const requestVersion = useRef(0);
  const submitLock = useRef(false);
  const visible = useRef(true);
  const sessionRef = useRef(getSessionRevision());

  const loadData = useCallback(async () => {
    const version = ++requestVersion.current, session = getSessionRevision();
    setLoadError('');
    try {
      const res = await fetchMyEvaluations();
      if (version !== requestVersion.current || session !== getSessionRevision()) return;
      if (res.code === 0 && res.data) setItems(res.data.list || []);
      else throw new Error(res.msg || '评价加载失败');
    } catch (error) {
      if (version === requestVersion.current && session === getSessionRevision()) setLoadError(error instanceof Error ? error.message : '评价加载失败');
    } finally {
      if (version === requestVersion.current && session === getSessionRevision()) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const refresh = createCoalescedRefresh(loadData);
    const offOrder = wsService.on('order_status_update', data => {
      if (visible.current && isPersonalOrderUpdate(data) && (data as {status?: string}).status === 'COMPLETED') refresh.request();
    });
    const offConnected = wsService.on('connected', () => { if (visible.current) refresh.request(); });
    const onAuthChanged = () => {
      if (sessionRef.current === getSessionRevision()) return;
      sessionRef.current = getSessionRevision();
      requestVersion.current++; setItems([]); setReplying(null); setReplyConflict(false); setLoadError('');
      if (visible.current) void loadData();
    };
    Taro.eventCenter.on('xa:auth.changed', onAuthChanged);
    void loadData();
    return () => { requestVersion.current++; offOrder(); offConnected(); refresh.dispose(); Taro.eventCenter.off('xa:auth.changed', onAuthChanged); };
  }, [loadData]);
  useDidHide(() => { visible.current = false; });
  useDidShow(() => { visible.current = true; void loadData(); });
  usePullDownRefresh(async () => { await loadData(); Taro.stopPullDownRefresh(); });

  const pending = useMemo(() => items.filter(item => !item.reply_content).length, [items]);
  const openReply = (item: Evaluation) => {
    setReplyConflict(false);
    setReplying(item);
  };

  const submitReply = async (content: string) => {
    if (!replying || submitLock.current || replyConflict) return;
    submitLock.current = true;
    const evaluationId = replying.id, session = getSessionRevision();
    setSubmitting(true);
    try {
      const res = await replyEvaluation(evaluationId, content, replying.reply_version);
      if (session !== getSessionRevision()) return;
      if (res.errorCode === 'EVALUATION_REPLY_CONFLICT') { setReplyConflict(true); return; }
      if (res.code !== 0 || !res.data) throw new Error(res.msg || '回复失败');
      requestVersion.current++;
      setItems(prev => prev.map(item => item.id === evaluationId ? res.data! : item));
      setReplying(null);
      Taro.showToast({ title: '回复已发布', icon: 'success' });
    } catch (error) {
      if (session === getSessionRevision()) Taro.showToast({ title: error instanceof Error ? error.message : '回复失败', icon: 'none' });
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
            <View className={styles.reply}><Text className={styles.replyLabel}>{item.reply_author === 'PROVIDER' ? '我的回复' : item.reply_author === 'STAFF' ? '客服回复' : '回复（来源未记录）'}</Text><Text className={styles.replyContent}>{item.reply_content}</Text><Button className={styles.replyButton} onClick={() => openReply(item)}>修改回复</Button></View>
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
        blocked={replyConflict}
        hint={replyConflict ? '回复已变化，草稿仍在此处。关闭后将刷新评价，请核对最新回复。' : replying?.reply_content && replying.reply_author !== 'PROVIDER' ? '保存后将替换当前回复，并显示为你的回复。' : undefined}
        onClose={() => { setReplying(null); if (replyConflict) void loadData(); setReplyConflict(false); }}
        onSubmit={submitReply}
      />
    </View></PlatformLayout>
  );
};

export default EvaluationPage;
