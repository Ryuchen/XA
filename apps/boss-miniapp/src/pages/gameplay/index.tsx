import { useEffect, useState } from 'react';
import { Button, Text, View, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { formatXaCoin } from '@xa/money';
import { fetchGameplays, fetchGameplayServices, Gameplay } from '@/services/gameplay';
import { ServiceInfo } from '@/types/order';
import GameplayCover from '@/components/GameplayCover';
import styles from './index.module.scss';

export default function GameplayPage() {
  const id = Number(Taro.getCurrentInstance().router?.params.id);
  const [gameplay, setGameplay] = useState<Gameplay>();
  const params = Taro.getCurrentInstance().router?.params || {};
  const [platform, setPlatform] = useState(['MOBILE', 'PC'].includes(params.platform || '') ? params.platform! : '');
  const [level, setLevel] = useState(['CONFIDENTIAL', 'TOP_SECRET'].includes(params.level || '') ? params.level! : '');
  const [items, setItems] = useState<ServiceInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    fetchGameplays().then(res => { if (!active) return; const item = res.data?.find(x => x.id === id); setGameplay(item); if (!item) setError('玩法已下架或不存在'); }).catch(() => active && setError('玩法加载失败，请重试'));
    return () => { active = false; };
  }, [id, retry]);
  useEffect(() => {
    if (!platform || !level) return;
    let active = true; setLoading(true); setItems([]); setError('');
    fetchGameplayServices(id, platform, level).then(res => { if (!active) return; if (res.code !== 0) throw new Error(res.msg); setItems(res.data || []); }).catch(() => active && setError('服务加载失败，请重试')).finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [id, platform, level, retry]);
  return <ScrollView scrollY className={styles.page}>
    {gameplay && <><GameplayCover name={gameplay.name} cover={gameplay.cover_url} theme={gameplay.theme} /><Text className={styles.subtitle}>{gameplay.subtitle}</Text></>}
    <View className={styles.section}><Text className={styles.step}>01 / 选择游戏平台</Text><View className={styles.options}>
      {[['MOBILE', '手游', '随时开局，移动战场'], ['PC', '端游', '键鼠操作，全力出击']].map(([key, name, hint]) => <View key={key} className={`${styles.option} ${platform === key ? styles.active : ''}`} onClick={() => { setPlatform(key); setLevel(''); setItems([]); }}><Text className={styles.optionName}>{name}</Text><Text className={styles.hint}>{hint}</Text></View>)}
    </View></View>
    {platform && <View className={styles.section}><Text className={styles.step}>02 / 选择行动难度</Text><View className={styles.options}>
      {[['CONFIDENTIAL', '机密'], ['TOP_SECRET', '绝密']].map(([key, name]) => <Button key={key} className={`${styles.level} ${level === key ? styles.active : ''}`} onClick={() => setLevel(key)}>{name}</Button>)}
    </View></View>}
    {platform && level && <View className={styles.section}><Text className={styles.step}>03 / 选择服务方案</Text>
      {loading ? <Text className={styles.hint}>正在加载对应方案…</Text> : items.map(item => <View className={styles.product} key={item.id} onClick={() => Taro.navigateTo({ url: `/pages/productDetail/index?id=${item.id}` })}>
        <View className={styles.productTop}><Text className={styles.optionName}>{item.name}</Text><Text className={styles.price}>{formatXaCoin(item.price)} 币</Text></View><Text className={styles.hint}>{item.description}</Text><Text className={styles.link}>查看详情与下单 →</Text>
      </View>)}
      {!loading && !items.length && !error && <View className={styles.empty}><Text>该平台与难度暂无上架方案</Text><Text className={styles.hint}>可以切换难度，或联系客服咨询定制。</Text><Button className={`${styles.actionBtn} ${styles.actionBtnPrimary}`} onClick={() => Taro.navigateTo({ url: '/pages/serviceCard/index' })}>咨询客服</Button></View>}
    </View>}
    {error && <Button className={`${styles.actionBtn} ${styles.actionBtnOutline}`} onClick={() => setRetry(value => value + 1)}>{error}</Button>}
    {gameplay?.rules && <View className={styles.section}><Text className={styles.step}>玩法须知</Text><Text className={styles.rules}>{gameplay.rules}</Text></View>}
  </ScrollView>;
}
