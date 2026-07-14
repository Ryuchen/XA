import React from 'react';
import { View } from '@tarojs/components';
import styles from './index.module.scss';

interface CardProps {
  /** 是否启用点击态反馈（用于可点击卡片） */
  pressable?: boolean;
  /** 是否启用入场动画 */
  animated?: boolean;
  /** 去除内边距（用于内容自带 padding 的场景） */
  noPadding?: boolean;
  className?: string;
  onClick?: () => void;
  children?: React.ReactNode;
}

const Card: React.FC<CardProps> = ({
  pressable = false,
  animated = false,
  noPadding = false,
  className = '',
  onClick,
  children
}) => {
  const classNames = [
    styles.card,
    pressable ? styles.pressable : '',
    animated ? styles.animated : '',
    noPadding ? styles.noPadding : '',
    className
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <View className={classNames} onClick={onClick}>
      {children}
    </View>
  );
};

export default Card;
