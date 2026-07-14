import React from 'react';
import { View, Text, Image, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';

const ChatPage: React.FC = () => {
  const messageList = [
    {
      id: 1,
      type: 'system',
      title: '系统通知',
      content: '欢迎加入兴安电竞，开始您的游戏陪玩之旅！',
      time: '10:00',
      unread: 0
    },
    {
      id: 2,
      type: 'service',
      title: '客服消息',
      content: '您的订单已安排，大神将在5分钟内联系您',
      time: '昨天',
      unread: 2
    },
    {
      id: 3,
      type: 'activity',
      title: '活动优惠',
      content: '限时特惠：三角洲护航服务8折起！',
      time: '昨天',
      unread: 1
    }
  ];

  const quickActions = [
    { icon: '💬', label: '在线客服', color: '#4A9EFF' },
    { icon: '🔔', label: '互动消息', color: '#FF6B6B' },
    { icon: '📢', label: '系统通知', color: '#52C41A' }
  ];

  const handleMessageClick = (id: number) => {
    if (id === 1) {
      Taro.navigateTo({ url: '/pages/serviceCard/index' });
    } else {
      Taro.showToast({ title: '功能开发中', icon: 'none' });
    }
  };

  return (
    <View className={styles.container}>
      {/* 快捷入口 */}
      <View className={styles.quickActions}>
        {quickActions.map((action, index) => (
          <View key={index} className={styles.actionItem}>
            <View 
              className={styles.actionIcon}
              style={{ backgroundColor: `${action.color}20`, color: action.color }}
            >
              {action.icon}
            </View>
            <Text className={styles.actionLabel}>{action.label}</Text>
          </View>
        ))}
      </View>

      {/* 消息列表 */}
      <View className={styles.messageSection}>
        <View className={styles.sectionHeader}>
          <Text className={styles.sectionTitle}>消息列表</Text>
          <Text className={styles.clearBtn}>全部已读</Text>
        </View>
        
        <ScrollView className={styles.messageList} scrollY>
          {messageList.map((msg) => (
            <View 
              key={msg.id} 
              className={styles.messageItem}
              onClick={() => handleMessageClick(msg.id)}
            >
              <View 
                className={styles.msgIcon}
                style={{ 
                  backgroundColor: msg.type === 'system' ? '#E8F4FF' : 
                                  msg.type === 'service' ? '#FFF2F0' : '#F6FFED'
                }}
              >
                {msg.type === 'system' ? '🔔' : msg.type === 'service' ? '💬' : '🎁'}
              </View>
              <View className={styles.msgContent}>
                <View className={styles.msgHeader}>
                  <Text className={styles.msgTitle}>{msg.title}</Text>
                  <Text className={styles.msgTime}>{msg.time}</Text>
                </View>
                <View className={styles.msgBody}>
                  <Text className={styles.msgText} numberOfLines={1}>
                    {msg.content}
                  </Text>
                  {msg.unread > 0 && (
                    <View className={styles.unreadBadge}>
                      <Text className={styles.unreadText}>{msg.unread}</Text>
                    </View>
                  )}
                </View>
              </View>
            </View>
          ))}
        </ScrollView>
      </View>

      {/* 空状态提示 */}
      {messageList.length === 0 && (
        <View className={styles.emptyState}>
          <Text className={styles.emptyIcon}>💬</Text>
          <Text className={styles.emptyText}>暂无消息</Text>
        </View>
      )}
    </View>
  );
};

export default ChatPage;
