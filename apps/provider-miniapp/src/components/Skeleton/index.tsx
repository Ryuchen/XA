import React from 'react';
import { View } from '@tarojs/components';
import styles from './index.module.scss';

type SkeletonVariant = 'text' | 'block' | 'avatar' | 'card-list';

interface SkeletonProps {
  variant?: SkeletonVariant;
  /** card-list 模式下的行数 */
  rows?: number;
  /** 自定义宽度，如 '60%'、'200rpx' */
  width?: string;
  /** 自定义高度 */
  height?: string;
  className?: string;
}

const Skeleton: React.FC<SkeletonProps> = ({
  variant = 'text',
  rows = 3,
  width,
  height,
  className = ''
}) => {
  if (variant === 'card-list') {
    return (
      <View className={`${styles.list} ${className}`}>
        {Array.from({ length: rows }).map((_, idx) => (
          <View key={idx} className={styles.cardItem}>
            <View className={`${styles.shimmer} ${styles.avatar}`} />
            <View className={styles.cardBody}>
              <View className={`${styles.shimmer} ${styles.lineLg}`} />
              <View className={`${styles.shimmer} ${styles.lineSm}`} />
              <View className={`${styles.shimmer} ${styles.lineMd}`} />
            </View>
          </View>
        ))}
      </View>
    );
  }

  const variantClass =
    variant === 'avatar' ? styles.avatar : variant === 'block' ? styles.block : styles.lineMd;

  return (
    <View
      className={`${styles.shimmer} ${variantClass} ${className}`}
      style={{ width, height }}
    />
  );
};

export default Skeleton;
