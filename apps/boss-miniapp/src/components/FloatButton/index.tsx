import React from 'react';
import { View, Text } from '@tarojs/components';
import styles from './index.module.scss';

interface FloatButtonProps {
  icon?: string;
  label?: string;
  className?: string;
  onClick?: () => void;
}

const FloatButton: React.FC<FloatButtonProps> = ({ icon = '+', label, className = '', onClick }) => {
  return (
    <View className={`${styles.float} ${label ? styles.withLabel : ''} ${className}`} onClick={onClick}>
      <Text className={styles.icon}>{icon}</Text>
      {label && <Text className={styles.label}>{label}</Text>}
    </View>
  );
};

export default FloatButton;
