import React, { useEffect, useState } from 'react';
import { Button, Text, View } from '@tarojs/components';
import Taro, { useDidShow, usePullDownRefresh } from '@tarojs/taro';
import { fetchMessages, markAllMessagesRead, MessageType, SiteMessage } from '@/services/message';
import PlatformLayout from '@/components/PlatformLayout';
import PageHeading from '@/components/PageHeading';
import styles from './index.module.scss';
import { Empty } from '@/components';

const TYPE_META: Record<MessageType, { label: string }> = {
  SYSTEM: { label: '系统通知' },
  ORDER: { label: '订单消息' },
  SUPPORT: { label: '客服消息' },
  PROMOTION: { label: '活动通知' },
};
const timeText = (value: string) => {
  const date = new Date(value); const diff = Date.now() - date.getTime();
  if (diff < 60000) return '刚刚';
  if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
  return `${date.getMonth() + 1}/${date.getDate()}`;
};

const MessagesPage: React.FC = () => {
  const [items, setItems] = useState<SiteMessage[]>([]);
  const [unread, setUnread] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const load = async () => {
    try {
      const res = await fetchMessages();
      if (res.code === 0 && res.data) { setItems(res.data.messages || []); setUnread(res.data.unread || 0); setError(''); }
      else setError(res.msg || '消息加载失败');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '消息加载失败');
    } finally { setLoaded(true); }
  };
  useEffect(() => { load(); }, []);
  useDidShow(() => { load(); });
  usePullDownRefresh(async () => { await load(); Taro.stopPullDownRefresh(); });
  const readAll = async () => {
    const res = await markAllMessagesRead();
    if (res.code === 0) { setItems(prev => prev.map(item => ({ ...item, is_read: true }))); setUnread(0); Taro.showToast({ title: '已全部标记为已读', icon: 'success' }); }
  };
  return (
    <PlatformLayout active='messages'><View className={`xa-provider-page ${styles.page}`}>
      <PageHeading title='消息中心' hint={!loaded ? '正在读取消息' : error ? '消息暂时无法更新' : unread ? `${unread} 条消息等待查看` : '所有消息均已阅读'} action={unread > 0 && <Button className={styles.readAll} onClick={readAll}>全部已读</Button>} />
      <View className={styles.messageList}>
      {!loaded ? <Text className={styles.empty}>加载中…</Text> : error ? <View className={styles.emptyCard}><Empty title='消息暂未加载' desc={error}><Button onClick={load}>重新加载</Button></Empty></View> : items.length === 0 ? <View className={styles.emptyCard}><Empty title='暂无消息' desc='平台通知和订单动态会出现在这里' /></View> : items.map(item => {
        const meta = TYPE_META[item.type] || TYPE_META.SYSTEM;
        return <View className={styles.item} key={item.id} onClick={() => Taro.navigateTo({ url: `/pages/message-detail/index?id=${item.id}` })}>
          <View className={styles.icon}><Text>{meta.label.slice(0, 2)}</Text></View>
          <View className={styles.content}><View className={styles.itemHead}><Text className={styles.itemTitle}>{item.title}</Text><Text className={styles.time}>{timeText(item.created_at)}</Text></View><Text className={styles.type}>{meta.label}</Text><Text className={styles.preview}>{item.preview || item.detail || '点击查看详情'}</Text></View>
          {!item.is_read && <View className={styles.dot} />}
        </View>;
      })}
      </View>
    </View></PlatformLayout>
  );
};
export default MessagesPage;
