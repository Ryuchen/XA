import React from 'react';
import { View, Text } from '@tarojs/components';
import styles from './index.module.scss';

interface LoadingProps {
  text?: string;
  /** 全屏居中遮罩模式 */
  fullscreen?: boolean;
  className?: string;
}

const Loading: React.FC<LoadingProps> = ({ text, fullscreen = false, className = '' }) => {
  return (
    <View
      className={`${styles.loading} ${fullscreen ? styles.fullscreen : ''} ${className}`}
    >
      <View className={styles.spinner} />
      {text && <Text className={styles.text}>{text}</Text>}
    </View>
  );
};

export default Loading;
