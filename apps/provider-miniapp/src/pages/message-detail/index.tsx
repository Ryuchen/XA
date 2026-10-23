import React, { useEffect, useState } from 'react';
import { Text, View } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { fetchMessageDetail, SiteMessage } from '@/services/message';
import styles from './index.module.scss';
import PageNav from '@/components/PageNav';
import PlatformLayout from '@/components/PlatformLayout';

const MessageDetailPage: React.FC = () => {
  const [message, setMessage] = useState<SiteMessage | null>(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    const id = Taro.getCurrentInstance().router?.params?.id;
    if (!id) { setLoaded(true); return; }
    fetchMessageDetail(id).then(res => { if (res.code === 0 && res.data) setMessage(res.data); }).catch(() => setMessage(null)).finally(() => setLoaded(true));
  }, []);
  return <PlatformLayout active='messages'><View className={`xa-provider-page ${styles.page}`}><PageNav title="消息详情" fallbackUrl="/pages/messages/index" />{!loaded || !message ? <View className={styles.card}><Text className={styles.empty}>{loaded ? '消息不存在或暂时无法加载' : '加载中…'}</Text></View> : <View className={styles.card}><Text className={styles.title}>{message.title}</Text><Text className={styles.time}>{new Date(message.created_at).toLocaleString()}</Text><View className={styles.divider} /><Text className={styles.preview}>{message.preview}</Text>{(message.detail || '').split(/\n+/).filter(Boolean).map((line, i) => <Text className={styles.paragraph} key={i}>{line}</Text>)}</View>}</View></PlatformLayout>;
};
export default MessageDetailPage;
