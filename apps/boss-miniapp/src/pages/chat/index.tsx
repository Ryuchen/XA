import React, { useEffect, useState } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import { fetchMessages, markAllMessagesRead, SiteMessage, MessageType } from '@/services/message';
import { Empty, Skeleton } from '@/components';
import Icon, { IconName } from '@/components/Icon';
import { useLoginGuard } from '@/hooks/useLoginGuard';
import { getStoredToken } from '@/utils/auth';
import styles from './index.module.scss';
import { openWecomCustomerService } from '@/services/support';

const quickActions: Array<{ icon: IconName; label: string; color: string; url?: string; type?: MessageType; wecom?: boolean }> = [
  { icon: 'message-circle', label: '在线客服', color: '#007AFF', wecom: true },
  { icon: 'package', label: '订单消息', color: '#34C759', type: 'ORDER' },
  { icon: 'bell', label: '系统通知', color: '#FF9500', type: 'SYSTEM' },
];

const FILTERS: Array<{ label: string; value: 'ALL' | MessageType }> = [
  { label: '全部', value: 'ALL' },
  { label: '订单', value: 'ORDER' },
  { label: '系统', value: 'SYSTEM' },
  { label: '活动', value: 'PROMOTION' },
];

const TYPE_ICON: Record<MessageType, IconName> = {
  SYSTEM: 'bell',
  ORDER: 'package',
  SUPPORT: 'message-circle',
  PROMOTION: 'gift',
};

const TYPE_COLOR: Record<MessageType, string> = {
  SYSTEM: '#007AFF',
  ORDER: '#34C759',
  SUPPORT: '#007AFF',
  PROMOTION: '#FF9500',
};

const formatTime = (dateStr: string): string => {
  if (!dateStr) return '';
  const date = new Date(dateStr);
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes}分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}小时前`;
  return `${date.getMonth() + 1}/${date.getDate()}`;
};

const ChatPage: React.FC = () => {
  const [messages, setMessages] = useState<SiteMessage[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [activeFilter, setActiveFilter] = useState<'ALL' | MessageType>('ALL');
  const [unread, setUnread] = useState(0);
  const { ensureLogin, loginSheet } = useLoginGuard();

  const loadMessages = (filter: 'ALL' | MessageType = activeFilter) => {
    setLoaded(false);
    fetchMessages({ page_size: 50, type: filter === 'ALL' ? undefined : filter })
      .then(res => {
        if (res.code === 0 && res.data) {
          setMessages(res.data.messages || []);
          setUnread(res.data.unread || 0);
        }
      })
      .catch(() => setMessages([]))
      .finally(() => setLoaded(true));
  };

  useEffect(() => {
    if (getStoredToken()) loadMessages();
    else setLoaded(true);
  }, []);

  useDidShow(() => {
    if (getStoredToken()) loadMessages();
  });

  const handleMessageClick = (msg: SiteMessage) => {
    // 始终先进入消息详情，由详情接口可靠地完成已读落库，再跳业务页面。
    Taro.navigateTo({ url: `/pages/messageDetail/index?id=${msg.id}` });
  };

  const handleQuickAction = (action: typeof quickActions[number]) => {
    if (action.wecom) {
      ensureLogin(async () => {
        Taro.showLoading({ title: '正在连接客服' });
        const result = await openWecomCustomerService({
          title: '我要咨询在线客服',
          path: '/pages/chat/index',
        });
        Taro.hideLoading();
        if (!result.opened) Taro.navigateTo({ url: '/pages/serviceCard/index' });
      });
      return;
    }
    if (action.url) {
      Taro.navigateTo({ url: action.url });
      return;
    }
    if (action.type) {
      ensureLogin(() => {
        setActiveFilter(action.type!);
        loadMessages(action.type!);
      });
    }
  };

  const handleFilter = (filter: 'ALL' | MessageType) => {
    if (filter === activeFilter) return;
    ensureLogin(() => {
      setActiveFilter(filter);
      loadMessages(filter);
    });
  };

  const handleMarkAllRead = async () => {
    if (!getStoredToken()) {
      ensureLogin(() => loadMessages());
      return;
    }
    const res = await markAllMessagesRead();
    if (res.code === 0) {
      Taro.showToast({ title: '已全部标记为已读', icon: 'success' });
      loadMessages();
    } else {
      Taro.showToast({ title: res.msg || '操作失败', icon: 'none' });
    }
  };

  return (
    <View className={styles.container}>
      <View className={styles.hero}>
        <View>
          <Text className={styles.eyebrow}>MESSAGE CENTER</Text>
          <Text className={styles.heroTitle}>消息中心</Text>
          <Text className={styles.heroSub}>订单进度、活动和平台通知集中管理</Text>
        </View>
        <View className={styles.unreadPill}>{unread > 0 ? `${unread} 条未读` : '全部已读'}</View>
      </View>

      <View className={styles.quickActions}>
        {quickActions.map((action, index) => (
          <View key={index} className={styles.actionItem} onClick={() => handleQuickAction(action)}>
            <View className={styles.actionIcon} style={{ backgroundColor: action.color }}>
              <Icon name={action.icon} size={44} color="#FFFFFF" strokeWidth={2} />
            </View>
            <Text className={styles.actionLabel}>{action.label}</Text>
          </View>
        ))}
      </View>

      <View className={styles.messageSection}>
        <View className={styles.sectionHeader}>
          <Text className={styles.sectionTitle}>消息列表</Text>
          <View className={styles.clearBtn} onClick={handleMarkAllRead}>
            <Icon name="check-check" size={30} color="#007AFF" strokeWidth={2} />
            <Text className={styles.clearBtnText}>全部已读</Text>
          </View>
        </View>

        <View className={styles.filters}>
          <View className={styles.filterTrack}>
            {FILTERS.map(filter => (
              <View
                key={filter.value}
                className={`${styles.filterItem} ${activeFilter === filter.value ? styles.filterActive : ''}`}
                onClick={() => handleFilter(filter.value)}
              >
                {filter.label}
              </View>
            ))}
          </View>
        </View>

        <ScrollView className={styles.messageList} scrollY>
          {!loaded && <Skeleton variant="card-list" rows={4} />}
          {messages.map((msg) => (
            <View
              key={msg.id}
              className={styles.messageItem}
              onClick={() => handleMessageClick(msg)}
            >
              <View
                className={styles.msgIcon}
                style={{ backgroundColor: TYPE_COLOR[msg.type] || '#007AFF' }}
              >
                <Icon name={TYPE_ICON[msg.type] || 'bell'} size={40} color="#FFFFFF" strokeWidth={2} />
              </View>
              <View className={styles.msgContent}>
                <View className={styles.msgHeader}>
                  <Text className={styles.msgTitle}>{msg.title}</Text>
                  <Text className={styles.msgTime}>{formatTime(msg.created_at)}</Text>
                </View>
                <View className={styles.msgBody}>
                  <Text className={styles.msgText} numberOfLines={1}>
                    {msg.preview}
                  </Text>
                  {!msg.is_read && (
                    <View className={styles.unreadBadge}>
                      <Text className={styles.unreadText}>·</Text>
                    </View>
                  )}
                </View>
              </View>
            </View>
          ))}
        </ScrollView>
      </View>

      {loaded && messages.length === 0 && (
        <Empty
          icon="💬"
          title={getStoredToken() ? '暂无消息' : '登录后查看消息'}
          desc={getStoredToken() ? '新的订单与系统通知会出现在这里' : '使用微信快捷登录，同步订单和平台通知'}
        >
          {!getStoredToken() && (
            <View className={styles.loginBtn} onClick={() => ensureLogin(() => loadMessages())}>微信快捷登录</View>
          )}
        </Empty>
      )}
      {loginSheet}
    </View>
  );
};

export default ChatPage;
