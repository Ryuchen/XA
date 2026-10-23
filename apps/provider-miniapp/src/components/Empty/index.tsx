import React from 'react';
import { View, Text } from '@tarojs/components';
import styles from './index.module.scss';
import TabIcon from '@/components/TabIcon';

interface EmptyProps {
  /** 兼容现有调用的类型标识，使用统一线性图标展示。 */
  icon?: string;
  title?: string;
  desc?: string;
  className?: string;
  children?: React.ReactNode;
}

const Empty: React.FC<EmptyProps> = ({
  icon = '📭',
  title = '暂无数据',
  desc,
  className = '',
  children
}) => {
  return (
    <View className={`${styles.empty} ${className}`}>
      <View className={styles.iconWrap}>
        <TabIcon name={icon === '🎮' ? 'gamepad' : icon === '💰' || icon === '🎁' || icon === '⚖️' ? 'wallet' : 'receipt'} color='#336b9a' size={64} />
      </View>
      <Text className={styles.title}>{title}</Text>
      {desc && <Text className={styles.desc}>{desc}</Text>}
      {children && <View className={styles.action}>{children}</View>}
    </View>
  );
};

export default Empty;
