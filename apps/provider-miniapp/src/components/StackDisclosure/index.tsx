import { PropsWithChildren, useId, useState } from 'react';
import { Button, Text, View } from '@tarojs/components';
import styles from './index.module.scss';

/** Summary stays visible; expanded information keeps its place in the order list. */
export default function StackDisclosure({ title, hint, children }: PropsWithChildren<{ title: string; hint?: string }>) {
  const [open, setOpen] = useState(false);
  const id = `details-${useId().replace(/:/g, '')}`;
  return <View className={`${styles.stack} ${open ? styles.open : ''}`}>
    <Button className={styles.trigger} aria-expanded={open} aria-controls={id} onClick={() => setOpen(value => !value)}>
      <View><Text className={styles.title}>{title}</Text>{hint && <Text className={styles.hint}>{hint}</Text>}</View>
      <Text className={styles.chevron}>{open ? '收起' : '展开'}</Text>
    </Button>
    <View id={id} className={styles.region} aria-hidden={!open}><View className={styles.inner}>{children}</View></View>
  </View>;
}
