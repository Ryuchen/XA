import React, { useState } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import { fetchAnnouncements, Announcement } from '@/services/announcement';
import { Skeleton } from '@/components';
import Icon from '@/components/Icon';
import styles from './index.module.scss';

const AnnouncementListPage: React.FC = () => {
  const [list, setList] = useState<Announcement[]>([]);
  const [loaded, setLoaded] = useState(false);

  const load = async () => {
    try {
      const res = await fetchAnnouncements(20);
      if (res.code === 0 && res.data) setList(res.data);
    } catch (e) {
      console.error('加载公告失败', e);
    } finally {
      setLoaded(true);
    }
  };

  // useDidShow 首次进入也会触发，无需额外 useEffect，避免首屏双份请求
  useDidShow(() => {
    load();
  });

  const handleTap = (item: Announcement) => {
    Taro.navigateTo({ url: `/pages/announcementDetail/index?id=${item.id}` });
  };

  return (
    <ScrollView className={styles.container} scrollY>
      {!loaded && <Skeleton variant="card-list" rows={4} />}
      {list.map(item => (
        <View
          key={item.id}
          className={`${styles.card} ${item.is_pinned ? styles.cardPinned : ''}`}
          onClick={() => handleTap(item)}
        >
          <View className={styles.cardMain}>
            <View className={`${styles.iconWrap} ${item.is_pinned ? styles.iconWrapPinned : ''}`}>
              <Icon
                name="megaphone"
                size={44}
                color={item.is_pinned ? '#FFFFFF' : '#007AFF'}
                strokeWidth={2}
              />
            </View>
            <View className={styles.content}>
              {item.is_pinned && (
                <View className={styles.badgeRow}>
                  <View className={styles.tag}>
                    <Icon name="pin" size={22} color="#FFFFFF" strokeWidth={2} />
                    <Text className={styles.tagText}>置顶</Text>
                  </View>
                </View>
              )}
              <Text className={styles.title}>{item.title}</Text>
              {!!item.content && <Text className={styles.summary}>{item.content}</Text>}
            </View>
          </View>

          <View className={`${styles.metaRow} ${item.is_pinned ? styles.metaRowPinned : ''}`}>
            <View className={styles.time}>
              <Icon name="calendar" size={26} color="#AEAEB2" strokeWidth={2} />
              <Text className={styles.timeText}>{item.created_at?.slice(0, 10)}</Text>
            </View>
            {item.is_pinned && (
              <View className={styles.detailLink}>
                <Text className={styles.detailText}>查看详情</Text>
                <Icon name="chevron-right" size={28} color="#007AFF" strokeWidth={2} />
              </View>
            )}
          </View>
        </View>
      ))}

      {loaded && list.length === 0 && (
        <View className={styles.empty}>
          <View className={styles.emptyIcon}>
            <Icon name="megaphone" size={80} color="#AEAEB2" strokeWidth={1.5} />
          </View>
          <Text className={styles.emptyTitle}>暂无公告</Text>
          <Text className={styles.emptyDesc}>平台最新动态会在这里发布</Text>
        </View>
      )}
    </ScrollView>
  );
};

export default AnnouncementListPage;
