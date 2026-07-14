import React from 'react';
import { View, Text, Image } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';

const MinePage: React.FC = () => {
  const orderStats = [
    { icon: '💳', label: '待支付', count: 0 },
    { icon: '📦', label: '待发货', count: 0 },
    { icon: '🚚', label: '待收货', count: 0 },
    { icon: '✅', label: '已完成', count: 2 }
  ];

  const menuItems = [
    { icon: '📋', text: '我的订单', arrow: true },
    { icon: '❤️', text: '我的收藏', arrow: true },
    { icon: '📍', text: '收货地址', arrow: true },
    { icon: '⚙️', text: '设置', arrow: true }
  ];

  return (
    <View className={styles.container}>
      <View className={styles.header}>
        <View className={styles.userInfo}>
          <Image
            className={styles.avatar}
            src="https://picsum.photos/id/64/200/200"
            mode="aspectFill"
          />
          <View className={styles.userDetail}>
            <Text className={styles.nickname}>小明</Text>
            <Text className={styles.userId}>ID: 888888</Text>
          </View>
        </View>
      </View>

      <View className={styles.orderCard}>
        <View className={styles.orderHeader}>
          <Text className={styles.orderTitle}>我的订单</Text>
          <Text className={styles.viewAll}>查看全部 ›</Text>
        </View>
        <View className={styles.orderStats}>
          {orderStats.map((stat, index) => (
            <View key={index} className={styles.orderStatItem}>
              <Text className={styles.orderIcon}>{stat.icon}</Text>
              <Text className={styles.orderCount}>{stat.count}</Text>
              <Text className={styles.orderLabel}>{stat.label}</Text>
            </View>
          ))}
        </View>
      </View>

      <View className={styles.menuCard}>
        {menuItems.map((item, index) => (
          <View key={index} className={styles.menuItem}>
            <View className={styles.menuIcon}>{item.icon}</View>
            <Text className={styles.menuText}>{item.text}</Text>
            {item.arrow && <Text className={styles.menuArrow}>›</Text>}
          </View>
        ))}
      </View>
    </View>
  );
};

export default MinePage;
