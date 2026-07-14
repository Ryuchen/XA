import React from 'react';
import { View, Text } from '@tarojs/components';
import styles from './index.module.scss';

type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost';
type ButtonSize = 'sm' | 'md' | 'lg';

interface ButtonProps {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** 撑满父容器宽度 */
  block?: boolean;
  loading?: boolean;
  disabled?: boolean;
  className?: string;
  onClick?: () => void;
  children?: React.ReactNode;
}

const Button: React.FC<ButtonProps> = ({
  variant = 'primary',
  size = 'md',
  block = false,
  loading = false,
  disabled = false,
  className = '',
  onClick,
  children
}) => {
  const inactive = loading || disabled;

  const classNames = [
    styles.btn,
    styles[variant],
    styles[size],
    block ? styles.block : '',
    inactive ? styles.disabled : '',
    className
  ]
    .filter(Boolean)
    .join(' ');

  const handleClick = () => {
    if (inactive) return;
    onClick?.();
  };

  return (
    <View className={classNames} onClick={handleClick}>
      {loading && <View className={styles.spinner} />}
      <Text className={styles.label}>{children}</Text>
    </View>
  );
};

export default Button;
