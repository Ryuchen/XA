import { useEffect, useRef, useState } from 'react';
import { Button, Text, View } from '@tarojs/components';
import { RouletteTarget } from '@/services/gameplay';
import styles from './index.module.scss';

export default function RouletteWheel({ pool, result }: { pool: RouletteTarget[]; result?: { index: number; name: string; description: string } }) {
  const [rotation, setRotation] = useState(0);
  const [spinning, setSpinning] = useState(false);
  const [selected, setSelected] = useState<RouletteTarget>();
  const lock = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  if (pool.length < 2) return null;
  const step = 360 / pool.length;
  const colors = ['#f7f4ec', '#e9eefb', '#e9eefb', '#e9eefb'];
  const gradient = pool.map((_, i) => `${colors[i % colors.length]} ${i * step}deg ${(i + 1) * step}deg`).join(',');
  const spin = () => {
    if (lock.current) return;
    let index = result?.index ?? 0;
    if (!result) {
      let ticket = Math.random() * pool.reduce((sum, item) => sum + item.weight, 0);
      index = pool.findIndex(item => (ticket -= item.weight) < 0);
    }
    if (index < 0 || index >= pool.length) return;
    lock.current = true; setSpinning(true); setSelected(undefined);
    setRotation(previous => Math.ceil(previous / 360) * 360 + 1440 + 360 - (index + .5) * step);
    timer.current = setTimeout(() => { setSelected(pool[index]); setSpinning(false); lock.current = false; }, 3200);
  };
  return <View className={styles.panel}>
    <Text className={styles.title}>{result ? '本单服务目标' : '转盘体验'}</Text>
    <Text className={styles.hint}>{result ? '本单结果已在余额支付成功时确定，动画可重复查看。' : '免费模拟，不扣余额、不生成订单。正式目标随订单保存。'}</Text>
    <View className={styles.stage}><View className={styles.pointer} />
      <View className={styles.wheel} style={{ background: `conic-gradient(${gradient})`, transform: `rotate(${rotation}deg)` }}>
        {pool.map((item, index) => <View key={item.name} className={styles.label} style={{ transform: `rotate(${(index + .5) * step}deg)` }}><View className={styles.radial}><Text className={styles.labelText} style={{ transform: `rotate(-${(index + .5) * step}deg)` }}>{item.name}</Text></View></View>)}
      </View>
      <Button disabled={spinning} className={styles.spin} onClick={spin}>{spinning ? '转动中' : result ? '查看' : '模拟'}</Button>
    </View>
    {selected && <View className={styles.result}><Text>{result ? '本单目标' : '模拟结果'}：{selected.name}</Text><Text className={styles.hint}>{selected.description}</Text></View>}
    <View className={styles.pool}>{pool.map(item => <View key={item.name} className={styles.poolRow}><View><Text>{item.name}</Text><Text className={styles.description}>{item.description}</Text></View><Text className={styles.probability}>{Number(item.probability.toFixed(4))}%</Text></View>)}</View>
    <Text className={styles.hint}>每单 1 个目标；按上方概率确定结果，扇区大小仅用于展示。平台按订单中的目标完成服务。</Text>
  </View>;
}
