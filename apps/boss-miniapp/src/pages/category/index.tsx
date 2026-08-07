import React, { useState, useEffect } from 'react';
import { View, Text, Image, ScrollView, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { formatXaCoin } from '@xa/money';
import { useCatalogStore, useServices } from '@/store';
import { Skeleton } from '@/components';
import Icon from '@/components/Icon';
import { resolveImageUrl } from '@/utils/media';
import styles from './index.module.scss';
import { useLoginGuard } from '@/hooks/useLoginGuard';
import { getStoredToken } from '@/utils/auth';

const PLACEHOLDER_IMAGE = 'https://picsum.photos/id/1/400/400';

const CategoryPage: React.FC = () => {
  // 服务目录走全局缓存，与「陪玩」「自助下单」「结算」等页共享，5 分钟内不重拉
  const services = useServices();
  const loadServices = useCatalogStore(state => state.loadServices);
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState('');
  const { ensureLogin, loginSheet } = useLoginGuard();

  useEffect(() => {
    if (!getStoredToken()) {
      setLoading(false);
      return;
    }
    loadServices().finally(() => setLoading(false));
  }, []);

  const handleProductClick = (serviceId: number) => {
    Taro.navigateTo({
      url: `/pages/productDetail/index?id=${serviceId}`
    });
  };

  const visibleServices = services.filter(service => {
    const query = keyword.trim().toLowerCase();
    if (!query) return true;
    return [service.name, service.description, service.category]
      .filter(Boolean)
      .some(value => String(value).toLowerCase().includes(query));
  });

  return (
    <View className={styles.container}>
      <View className={styles.header}>
        <Text className={styles.eyebrow}>SERVICE CENTER</Text>
        <View className={styles.titleRow}>
          <View>
            <Text className={styles.pageTitle}>挑选服务</Text>
            <Text className={styles.pageSubtitle}>按项目快速找到适合你的陪玩服务</Text>
          </View>
          <View className={styles.countPill}>{services.length} 项</View>
        </View>
        <View className={styles.searchBar}>
          <Icon name="search" size={32} color="rgba(255,255,255,0.7)" />
          <Input
            className={styles.searchInput}
            value={keyword}
            placeholder="搜索游戏、服务或玩法"
            placeholderStyle="color: rgba(255,255,255,0.55)"
            onInput={event => setKeyword(event.detail.value)}
            confirmType="search"
          />
          {!!keyword && (
            <View className={styles.clearSearch} onClick={() => setKeyword('')}>
              <Text>×</Text>
            </View>
          )}
        </View>
      </View>
      <ScrollView className={styles.productList} scrollY>
        <View className={styles.listHeader}>
          <View>
            <Text className={styles.sectionTitle}>全部服务</Text>
            <Text className={styles.sectionSub}>
              {keyword ? `找到 ${visibleServices.length} 个匹配服务` : `${services.length} 个在线服务`}
            </Text>
          </View>
        </View>
        <View className={styles.productGrid}>
          {visibleServices.map((service) => (
            <View
              key={service.id}
              className={styles.productCard}
              onClick={() => handleProductClick(service.id)}
            >
              <Image
                className={styles.productImage}
                src={resolveImageUrl(service.cover_url, PLACEHOLDER_IMAGE)}
                mode="aspectFill"
              />
              <View className={styles.productInfo}>
                <Text className={styles.productName}>{service.name}</Text>
                <Text className={styles.productDesc}>{service.description}</Text>
                <View className={styles.priceRow}>
                  <Text className={styles.price}>{formatXaCoin(service.price)}兴安币</Text>
                </View>
              </View>
            </View>
          ))}
        </View>
        {loading && <Skeleton variant="card-list" rows={4} />}
        {!loading && visibleServices.length === 0 && (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>
              <Icon name="gamepad-2" size={72} color="#AEAEB2" />
            </View>
            <Text className={styles.emptyTitle}>{keyword ? '没有匹配的服务' : '暂无可用服务'}</Text>
            <Text className={styles.emptyDesc}>
              {keyword ? '换个关键词试试看' : getStoredToken() ? '敬请期待更多上新' : '微信快捷登录后即可挑选和下单'}
            </Text>
            {!keyword && !getStoredToken() && (
              <View className={styles.loginBtn} onClick={() => ensureLogin(() => loadServices())}>微信快捷登录</View>
            )}
          </View>
        )}
      </ScrollView>
      {loginSheet}
    </View>
  );
};

export default CategoryPage;
