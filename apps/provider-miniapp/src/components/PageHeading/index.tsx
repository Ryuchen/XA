import { ReactNode } from 'react';
import { Text, View } from '@tarojs/components';
import styles from './index.module.scss';

export default function PageHeading({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return <View className={styles.heading}>
    <Text className={styles.title}>{title}</Text>
    {action && <View className={styles.action}>{action}</View>}
    {hint && <Text className={styles.hint}>{hint}</Text>}
  </View>;
}
