import React, { useEffect, useState } from 'react';
import { ScrollView, Text, View } from '@tarojs/components';
import Taro, { useDidShow, usePullDownRefresh } from '@tarojs/taro';
import { fetchMessages, markAllMessagesRead, MessageType, SiteMessage } from '@/services/message';
import PlatformLayout from '@/components/PlatformLayout';
import styles from './index.module.scss';

const TYPE_META: Record<MessageType, { icon: string; label: string; color: string }> = {
  SYSTEM: { icon: '🔔', label: '系统通知', color: '#007AFF' },
  ORDER: { icon: '📦', label: '订单消息', color: '#34C759' },
  SUPPORT: { icon: '💬', label: '客服消息', color: '#5856D6' },
  PROMOTION: { icon: '🎁', label: '活动通知', color: '#FF9500' },
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
  const load = async () => {
    try {
      const res = await fetchMessages();
      if (res.code === 0 && res.data) { setItems(res.data.messages || []); setUnread(res.data.unread || 0); }
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
    <PlatformLayout active='messages'><ScrollView className={styles.page} scrollY>
      <View className={styles.header}><View><Text className={styles.eyebrow}>通知与动态</Text><Text className={styles.title}>消息中心</Text><Text className={styles.subtitle}>{unread ? `${unread} 条消息等待查看` : '所有消息均已阅读'}</Text></View>{unread > 0 && <Text className={styles.readAll} onClick={readAll}>全部已读</Text>}</View>
      {!loaded ? <Text className={styles.empty}>加载中…</Text> : items.length === 0 ? <View className={styles.emptyCard}><Text className={styles.emptyIcon}>📭</Text><Text className={styles.emptyTitle}>暂无消息</Text><Text className={styles.emptyText}>平台通知和订单动态会出现在这里</Text></View> : items.map(item => {
        const meta = TYPE_META[item.type] || TYPE_META.SYSTEM;
        return <View className={styles.item} key={item.id} onClick={() => Taro.navigateTo({ url: `/pages/message-detail/index?id=${item.id}` })}>
          <View className={styles.icon} style={{ backgroundColor: `${meta.color}18` }}><Text>{meta.icon}</Text></View>
          <View className={styles.content}><View className={styles.itemHead}><Text className={styles.itemTitle}>{item.title}</Text><Text className={styles.time}>{timeText(item.created_at)}</Text></View><Text className={styles.type}>{meta.label}</Text><Text className={styles.preview}>{item.preview || item.detail || '点击查看详情'}</Text></View>
          {!item.is_read && <View className={styles.dot} />}
        </View>;
      })}
    </ScrollView></PlatformLayout>
  );
};
export default MessagesPage;
