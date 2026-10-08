import { useState } from 'react';
import { Text, View } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import { formatXaCoin } from '@xa/money';
import { fetchGameplays, Gameplay } from '@/services/gameplay';
import { useLoginGuard } from '@/hooks/useLoginGuard';
import GameplayCover from '@/components/GameplayCover';
import styles from './index.module.scss';

export default function GameplayLobby() {
  const [items, setItems] = useState<Gameplay[]>([]);
  const [error, setError] = useState('');
  const { ensureLogin, loginSheet } = useLoginGuard();
  const load = async () => {
    try { const res = await fetchGameplays(); if (res.code !== 0) throw new Error(res.msg); setItems(res.data || []); setError(''); }
    catch { setError('玩法暂未加载，点击重试'); }
  };
  useDidShow(load);
  return <View className={styles.section}>
    <View className={styles.heading}><Text className={styles.title}>今天，玩点什么</Text><Text className={styles.hint}>手游 / 端游 · 机密 / 绝密</Text></View>
    {error && <Text onClick={load} className={styles.hint}>{error}</Text>}
    <View className={styles.grid}>{items.map(item => <View key={item.id} className={styles.card} onClick={() => ensureLogin(() => Taro.navigateTo({ url: `/pages/gameplay/index?id=${item.id}` }))}>
      <GameplayCover name={item.name} cover={item.cover_url} theme={item.theme} />
      <Text className={styles.subtitle}>{item.subtitle}</Text>
      <View className={styles.meta}><Text>{item.service_count ? `${item.service_count} 款可选` : '等待上新'}</Text><Text className={styles.price}>{item.starting_price !== null ? `${formatXaCoin(item.starting_price)} 币起` : '查看玩法 →'}</Text></View>
    </View>)}</View>
    {!items.length && !error && <Text className={styles.hint}>玩法正在准备中</Text>}
    {loginSheet}
  </View>;
}
