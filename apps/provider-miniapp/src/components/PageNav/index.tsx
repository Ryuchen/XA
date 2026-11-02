import React, { useEffect, useRef, useState } from 'react';
import { Button, Text, View } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import { useLayoutMode } from '@/hooks/useLayoutMode';

interface Props { title: string; fallbackUrl?: string; settleMs?: number; }

const PageNav: React.FC<Props> = ({ title, fallbackUrl = '/pages/mine/index', settleMs = 0 }) => {
  const mode = useLayoutMode();
  const [ready, setReady] = useState(!settleMs);
  const navigating = useRef(false);
  useEffect(() => {
    if (!settleMs) { setReady(true); return; }
    const timer = setTimeout(() => setReady(true), settleMs);
    return () => clearTimeout(timer);
  }, [settleMs]);
  if (process.env.TARO_ENV !== 'h5') return null;
  const back = async () => {
    if (!ready || navigating.current) return;
    navigating.current = true;
    try { await Taro.navigateBack({ delta: 1 }); }
    catch { await Taro.switchTab({ url: fallbackUrl }); }
    finally { navigating.current = false; }
  };
  if (mode === 'desktop') return <View className={styles.webNav}><Button className={styles.webBack} disabled={!ready} onClick={back}>‹ 返回工作台</Button><Text className={styles.webTitle}>{title}</Text></View>;
  return <View className={styles.nav}><Button className={styles.back} disabled={!ready} onClick={back}><Text className={styles.chevron}>‹</Text><Text className={styles.backText}>返回</Text></Button><Text className={styles.title}>{title}</Text><View className={styles.placeholder} /></View>;
};
export default PageNav;
