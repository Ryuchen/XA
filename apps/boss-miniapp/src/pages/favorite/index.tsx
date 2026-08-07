import React, { useState } from 'react';
import { View, Text, Image, ScrollView } from '@tarojs/components';
import Taro, { useDidShow, usePullDownRefresh } from '@tarojs/taro';
import { fetchFavorites, toggleFavorite } from '@/services/favorite';
import { ServiceInfo } from '@/types/order';
import { formatXaCoin } from '@/utils/money';
import { Skeleton } from '@/components';
import Icon from '@/components/Icon';
import { resolveImageUrl } from '@/utils/media';
import styles from './index.module.scss';

const PLACEHOLDER_IMAGE = 'https://picsum.photos/id/1/400/400';

const FavoritePage: React.FC = () => {
  const [services, setServices] = useState<ServiceInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [removingId, setRemovingId] = useState<number | null>(null);

  const load = async () => {
    try {
      const res = await fetchFavorites();
      if (res.code === 0 && res.data) {
        setServices(res.data);
        setError(false);
      } else {
        setError(true);
      }
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  };

  useDidShow(() => {
    load();
  });

  usePullDownRefresh(async () => {
    await load();
    Taro.stopPullDownRefresh();
  });

  const handleRetry = () => {
    setLoading(true);
    load();
  };

  const handleClick = (serviceId: number) => {
    Taro.navigateTo({ url: `/pages/productDetail/index?id=${serviceId}` });
  };

  // 取消收藏：二次确认后调用 toggle 接口，成功即从列表移除
  const handleUnfavorite = async (service: ServiceInfo) => {
    if (removingId !== null) return;
    const confirm = await Taro.showModal({
      title: '取消收藏',
      content: `确定不再收藏「${service.name}」吗？`,
      confirmText: '取消收藏',
      confirmColor: '#FF4D4F',
      cancelText: '再想想',
    });
    if (!confirm.confirm) return;

    try {
      setRemovingId(service.id);
      const res = (await toggleFavorite(service.id)) as { code?: number; msg?: string };
      if (res?.code === 0) {
        setServices(prev => prev.filter(item => item.id !== service.id));
        Taro.showToast({ title: '已取消收藏', icon: 'none' });
      } else {
        Taro.showToast({ title: res?.msg || '操作失败', icon: 'none' });
      }
    } catch {
      Taro.showToast({ title: '操作失败，请重试', icon: 'none' });
    } finally {
      setRemovingId(null);
    }
  };

  return (
    <ScrollView className={styles.container} scrollY>
      {loading ? (
        <Skeleton variant="card-list" rows={3} />
      ) : error ? (
        <View className={styles.stateWrap}>
          <View className={styles.stateIcon}>
            <Icon name="heart-off" size={64} color="#AEAEB2" />
          </View>
          <Text className={styles.stateTitle}>加载失败</Text>
          <Text className={styles.stateDesc}>网络异常，请稍后重试</Text>
          <View className={styles.retryBtn} onClick={handleRetry}>
            <Text>重新加载</Text>
          </View>
        </View>
      ) : services.length === 0 ? (
        <View className={styles.stateWrap}>
          <View className={styles.stateIcon}>
            <Icon name="heart-off" size={64} color="#AEAEB2" />
          </View>
          <Text className={styles.stateTitle}>还没有收藏的服务</Text>
          <Text className={styles.stateDesc}>去逛逛，把心仪的服务收藏起来吧</Text>
        </View>
      ) : (
        <View className={styles.grid}>
          {services.map(service => (
            <View key={service.id} className={styles.card} onClick={() => handleClick(service.id)}>
              <View className={styles.imageWrap}>
                <Image
                  className={styles.image}
                  src={resolveImageUrl(service.cover_url, PLACEHOLDER_IMAGE)}
                  mode="aspectFill"
                />
              </View>
              <View
                className={styles.removeBtn}
                onClick={e => {
                  e.stopPropagation();
                  void handleUnfavorite(service);
                }}
              >
                {removingId === service.id ? (
                  <Text className={styles.removingText}>···</Text>
                ) : (
                  <Icon name="heart" size={32} color="#FF3B30" fill="#FF3B30" />
                )}
              </View>
              <View className={styles.info}>
                <Text className={styles.name}>{service.name}</Text>
                <Text className={styles.desc}>{service.description}</Text>
                <View className={styles.priceRow}>
                  <Text className={styles.price}>{formatXaCoin(service.price)}兴安币</Text>
                </View>
              </View>
            </View>
          ))}
        </View>
      )}
    </ScrollView>
  );
};

export default FavoritePage;
