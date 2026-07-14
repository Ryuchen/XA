import React from 'react';
import { Text, View } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import { useLayoutMode } from '@/hooks/useLayoutMode';

interface Props { title: string; fallbackUrl?: string; }

const PageNav: React.FC<Props> = ({ title, fallbackUrl = '/pages/mine/index' }) => {
  const mode = useLayoutMode();
  if (process.env.TARO_ENV !== 'h5') return null;
  const back = () => Taro.navigateBack({ delta: 1 }).catch(() => Taro.switchTab({ url: fallbackUrl }));
  if (mode === 'desktop') return <View className={styles.webNav}><View className={styles.webBack} onClick={back}><Text>‹</Text><Text>返回工作台</Text></View><Text className={styles.webTitle}>{title}</Text><Text className={styles.webBadge}>WEB</Text></View>;
  return <View className={styles.nav}><View className={styles.back} onClick={back}><Text className={styles.chevron}>‹</Text><Text className={styles.backText}>返回</Text></View><Text className={styles.title}>{title}</Text><View className={styles.placeholder} /></View>;
};
export default PageNav;
