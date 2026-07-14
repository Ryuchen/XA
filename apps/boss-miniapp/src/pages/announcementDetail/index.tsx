import React, { useEffect, useState } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { fetchAnnouncementDetail, Announcement } from '@/services/announcement';
import { Loading } from '@/components';
import Icon from '@/components/Icon';
import styles from './index.module.scss';

const AnnouncementDetailPage: React.FC = () => {
  const [detail, setDetail] = useState<Announcement | null>(null);

  useEffect(() => {
    const { id } = Taro.getCurrentInstance().router?.params || {};
    if (!id) return;
    fetchAnnouncementDetail(id)
      .then(res => {
        if (res.code === 0 && res.data) {
          setDetail(res.data);
        } else {
          Taro.showToast({ title: res.msg || '公告不存在', icon: 'none' });
        }
      })
      .catch(e => console.error('加载公告详情失败', e));
  }, []);

  if (!detail) {
    return (
      <View className={styles.container}>
        <Loading fullscreen text="加载中…" />
      </View>
    );
  }

  return (
    <ScrollView className={styles.container} scrollY>
      <View className={styles.header}>
        <View className={styles.badge}>
          <Icon name="megaphone" size={26} color="#007AFF" strokeWidth={2} />
          <Text className={styles.badgeText}>平台公告</Text>
        </View>
        <Text className={styles.title}>{detail.title}</Text>
        <View className={styles.meta}>
          <View className={styles.author}>
            <Icon name="badge-check" size={28} color="#007AFF" strokeWidth={2} />
            <Text className={styles.authorName}>兴安电竞官方</Text>
          </View>
          <Text className={styles.dot}>·</Text>
          <Text className={styles.time}>{detail.created_at?.slice(0, 10)}</Text>
        </View>
      </View>

      <View className={styles.card}>
        <Text className={styles.content}>{detail.content}</Text>
      </View>
    </ScrollView>
  );
};

export default AnnouncementDetailPage;
