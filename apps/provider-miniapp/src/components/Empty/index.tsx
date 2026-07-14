import React from 'react';
import { View, Text } from '@tarojs/components';
import styles from './index.module.scss';

interface EmptyProps {
  /** 插画 emoji（默认空盒子） */
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
        <Text className={styles.icon}>{icon}</Text>
      </View>
      <Text className={styles.title}>{title}</Text>
      {desc && <Text className={styles.desc}>{desc}</Text>}
      {children && <View className={styles.action}>{children}</View>}
    </View>
  );
};

export default Empty;
