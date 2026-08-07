import React, { useEffect, useState } from 'react';
import { View, Text } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { fetchMessageDetail, SiteMessage, MessageType } from '@/services/message';
import { Empty, Loading } from '@/components';
import Icon, { IconName } from '@/components/Icon';
import styles from './index.module.scss';

const TYPE_LABEL: Record<MessageType, string> = {
  SYSTEM: '系统',
  ORDER: '订单',
  SUPPORT: '客服',
  PROMOTION: '活动',
};

interface TypeMeta {
  icon: IconName;
  color: string;
  surface: string;
}

const TYPE_META: Record<MessageType, TypeMeta> = {
  SYSTEM: { icon: 'bell', color: '#007AFF', surface: 'rgba(0, 122, 255, 0.14)' },
  ORDER: { icon: 'receipt', color: '#FF9500', surface: 'rgba(255, 149, 0, 0.14)' },
  SUPPORT: { icon: 'headphones', color: '#34C759', surface: 'rgba(52, 199, 89, 0.14)' },
  PROMOTION: { icon: 'gift', color: '#FF3B30', surface: 'rgba(255, 59, 48, 0.14)' },
};

const formatDateTime = (value?: string): string => {
  if (!value) return '';
  // iOS / 微信小程序对 "YYYY-MM-DD HH:mm:ss" 解析为 Invalid Date，
  // 将空格替换为 T 后按本地时间解析可兼容多端。
  const date = new Date(value.replace(' ', 'T'));
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
};

const MessageDetailPage: React.FC = () => {
  const [message, setMessage] = useState<SiteMessage | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const { id } = Taro.getCurrentInstance().router?.params || {};
    if (!id) {
      setLoading(false);
      return;
    }
    fetchMessageDetail(id)
      .then(res => {
        if (res.code === 0 && res.data) setMessage(res.data);
      })
      .finally(() => setLoading(false));
  }, []);

  const handleAction = () => {
    if (!message) return;
    const target = message.action_url || (
      message.related_order_id ? `/pages/orderList/index?orderId=${message.related_order_id}` : ''
    );
    if (!target) return;
    const path = target.split('?')[0];
    if (['/pages/home/index', '/pages/companions/index', '/pages/category/index', '/pages/chat/index', '/pages/mine/index'].includes(path)) {
      Taro.switchTab({ url: path });
      return;
    }
    Taro.navigateTo({ url: target });
  };

  if (loading) {
    return (
      <View className={styles.container}>
        <Loading fullscreen text="加载中..." />
      </View>
    );
  }

  if (!message) {
    return (
      <View className={styles.container}>
        <Empty icon="📭" title="消息不存在" desc="该消息可能已被删除或链接已失效" />
      </View>
    );
  }

  const detailParagraphs = (message.detail || message.preview || '').split(/\n+/).filter(Boolean);
  const meta = TYPE_META[message.type] || TYPE_META.SYSTEM;

  return (
    <View className={styles.container}>
      <View className={styles.hero}>
        <View className={styles.heroBadge} style={{ backgroundColor: meta.surface }}>
          <Icon name={meta.icon} size={64} color={meta.color} strokeWidth={2} />
        </View>
        <Text className={styles.title}>{message.title}</Text>
        <Text className={styles.subtitle}>
          {formatDateTime(message.created_at)} · {TYPE_LABEL[message.type] || ''}通知
        </Text>
      </View>

      <View className={styles.card}>
        <Text className={styles.summary}>{message.preview}</Text>
        {detailParagraphs.length > 0 && (
          <>
            <View className={styles.divider} />
            {detailParagraphs.map((paragraph, index) => (
              <Text key={index} className={styles.paragraph}>{paragraph}</Text>
            ))}
          </>
        )}
      </View>

      {(message.action_url || message.related_order_id) && (
        <View className={styles.actionBtn} onClick={handleAction}>
          <Text className={styles.actionText}>查看详情</Text>
          <Icon name="arrow-right" size={32} color="#FFFFFF" strokeWidth={2} />
        </View>
      )}
    </View>
  );
};

export default MessageDetailPage;
