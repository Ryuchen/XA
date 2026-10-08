import { Image, Text, View } from '@tarojs/components';
import { resolveImageUrl } from '@/utils/media';
import styles from './index.module.scss';

const symbols: Record<string, string> = { roulette: '✦', experience: '01', companion: '∞', fun: '✧', mission: '✓', extraction: '↗', prison: '▥', custom: '+' };
export default function GameplayCover({ name, theme = 'custom', cover = '', size = 'card' }: { name: string; theme?: string; cover?: string; size?: 'card' | 'hero' | 'thumb' }) {
  return <View className={`${styles.cover} ${styles[theme] || styles.custom} ${styles[size] || ''}`}>
    {cover ? <Image src={resolveImageUrl(cover)} className={styles.image} mode='aspectFill' /> : <>
      <View className={styles.orbit} /><View className={styles.orbitInner} />
      <Text className={styles.mark}>{symbols[theme] || '+'}</Text>
      <Text className={styles.brand}>XA / PLAY</Text>
    </>}
    <View className={styles.shade} /><Text className={styles.name}>{name}</Text>
  </View>;
}
