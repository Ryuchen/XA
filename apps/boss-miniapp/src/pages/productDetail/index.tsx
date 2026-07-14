import React, { useState, useEffect } from 'react';
import { View, Text, Image, Swiper, SwiperItem, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { fetchServiceDetail, fetchServiceEvaluations, Evaluation } from '@/services/order';
import { toggleFavorite } from '@/services/favorite';
import { ServiceInfo } from '@/types/order';
import { formatXaCoin } from '@/utils/format';
import { useLoginGuard } from '@/hooks/useLoginGuard';
import { Skeleton, Empty } from '@/components';
import Icon from '@/components/Icon';
import styles from './index.module.scss';

const FALLBACK_IMAGE = 'https://picsum.photos/id/1/400/400';

const STAR_ACTIVE = '#FF9500';
const STAR_INACTIVE = '#D1D1D6';

const renderStars = (score: number, size = 26) => (
  <View className={styles.stars}>
    {[1, 2, 3, 4, 5].map(n => (
      <Icon
        key={n}
        name="star"
        size={size}
        color={n <= score ? STAR_ACTIVE : STAR_INACTIVE}
        fill={n <= score ? STAR_ACTIVE : 'none'}
      />
    ))}
  </View>
);

const formatTime = (value: string) =>
  new Date(value).toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });

const ProductDetailPage: React.FC = () => {
  const [service, setService] = useState<ServiceInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [gameRounds, setGameRounds] = useState(1);
  const [currentImageIndex, setCurrentImageIndex] = useState(0);
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [evalTotal, setEvalTotal] = useState(0);
  const [evalAvg, setEvalAvg] = useState(0);
  const [favorited, setFavorited] = useState(false);
  const [favoriting, setFavoriting] = useState(false);
  const { ensureLogin, loginSheet } = useLoginGuard();

  useEffect(() => {
    const { id } = Taro.getCurrentInstance().router?.params || {};
    if (!id) {
      setLoading(false);
      return;
    }
    fetchServiceDetail(id)
      .then(res => {
        if (res.code === 0 && res.data) {
          setService(res.data);
          setFavorited(!!res.data.is_favorited);
        }
      })
      .finally(() => setLoading(false));
    fetchServiceEvaluations(id).then(res => {
      if (res.code === 0 && res.data) {
        setEvaluations(res.data.list);
        setEvalTotal(res.data.total);
        setEvalAvg(res.data.avg_score);
      }
    });
  }, []);

  const handleToggleFavorite = async () => {
    if (!service || favoriting) return;
    setFavoriting(true);
    const res = await toggleFavorite(service.id);
    setFavoriting(false);
    if (res.code === 0 && res.data) {
      setFavorited(res.data.favorited);
      Taro.showToast({ title: res.data.favorited ? '已收藏' : '已取消收藏', icon: 'none' });
    } else {
      Taro.showToast({ title: res.msg || '操作失败', icon: 'none' });
    }
  };

  const handleBuyNow = () => {
    if (!service) return;
    // 下单主链路：详情 → 选择陪玩 → 确认下单。带上 serviceId 与局数，供后续页面预置。
    ensureLogin(() => {
      Taro.navigateTo({
        url: `/pages/players/index?serviceId=${service.id}&game_rounds=${gameRounds}`
      });
    });
  };

  if (loading) {
    return (
      <View className={styles.container}>
        <Skeleton variant="block" height="560rpx" />
        <View style={{ padding: '32rpx' }}>
          <Skeleton variant="text" width="40%" height="48rpx" />
          <Skeleton variant="text" width="80%" />
          <Skeleton variant="text" width="60%" />
        </View>
        <Skeleton variant="card-list" rows={2} />
      </View>
    );
  }

  if (!service) {
    return (
      <View className={styles.container}>
        <Empty icon="🔍" title="服务不存在" desc="该服务可能已下架，去看看其他服务吧" />
      </View>
    );
  }

  const swiperImages: string[] = (service.images && service.images.length > 0)
    ? service.images
    : (service.cover_url ? [service.cover_url] : [FALLBACK_IMAGE]);

  return (
    <ScrollView className={styles.container} scrollY>
      <Swiper
        className={styles.swiper}
        indicatorColor="rgba(255,255,255,0.5)"
        indicatorActiveColor="#fff"
        current={currentImageIndex}
        onChange={(e) => setCurrentImageIndex(e.detail.current)}
        circular
      >
        {swiperImages.map((src, index) => (
          <SwiperItem key={index}>
            <Image className={styles.swiperImage} src={src} mode="aspectFill" />
          </SwiperItem>
        ))}
      </Swiper>

      <View className={styles.info}>
        <View className={styles.priceRow}>
          <Text className={styles.price}>{formatXaCoin(service.price)}兴安币</Text>
          {typeof service.sales_count === 'number' && (
            <Text className={styles.salesText}>已售 {service.sales_count}</Text>
          )}
        </View>
        <Text className={styles.productName}>{service.name}</Text>
        <Text className={styles.productDesc}>{service.description}</Text>
        <Text className={styles.unit}>单价：{formatXaCoin(service.price)}兴安币/次</Text>
      </View>

      {service.highlights && service.highlights.length > 0 && (
        <View className={styles.section}>
          <Text className={styles.sectionTitle}>服务亮点</Text>
          <View className={styles.guarantees}>
            {service.highlights.map((tip, idx) => (
              <View key={idx} className={styles.highlightItem}>
                <Icon name="check" size={30} color="#007AFF" className={styles.highlightIcon} />
                <Text className={styles.guaranteeTitle}>{tip}</Text>
              </View>
            ))}
          </View>
        </View>
      )}

      <View className={styles.section}>
        <Text className={styles.sectionTitle}>游戏局数</Text>
        <View className={styles.quantitySelector}>
          <View className={styles.quantityBtn} onClick={() => setGameRounds(Math.max(1, gameRounds - 1))}>
            <Icon name="minus" size={30} color="#1D1D1F" />
          </View>
          <Text className={styles.quantityValue}>{gameRounds}</Text>
          <View className={styles.quantityBtn} onClick={() => setGameRounds(gameRounds + 1)}>
            <Icon name="plus" size={30} color="#1D1D1F" />
          </View>
        </View>
      </View>

      <View className={styles.section}>
        <Text className={styles.sectionTitle}>商品详情</Text>
        <Text className={styles.productDesc}>{service.description}</Text>
        <View className={styles.guarantees}>
          <View className={styles.guaranteeItem}>
            <Text className={styles.guaranteeTitle}>真人陪玩</Text>
            <Text className={styles.guaranteeText}>客服核验档期，按需求匹配</Text>
          </View>
          <View className={styles.guaranteeItem}>
            <Text className={styles.guaranteeTitle}>可约时间</Text>
            <Text className={styles.guaranteeText}>晚高峰优先排班，支持备注</Text>
          </View>
          <View className={styles.guaranteeItem}>
            <Text className={styles.guaranteeTitle}>售后保障</Text>
            <Text className={styles.guaranteeText}>服务异常可联系官方客服</Text>
          </View>
        </View>
      </View>

      <View className={styles.section}>
        <View className={styles.evalHeader}>
          <Text className={styles.sectionTitle}>用户评价（{evalTotal}）</Text>
          {evalTotal > 0 && (
            <View className={styles.evalScore}>
              <Text className={styles.evalScoreValue}>{evalAvg.toFixed(1)}</Text>
              {renderStars(Math.round(evalAvg), 24)}
            </View>
          )}
        </View>

        {evaluations.length === 0 ? (
          <Text className={styles.evalEmpty}>暂无评价，下单后可率先评价</Text>
        ) : (
          evaluations.map(item => (
            <View key={item.id} className={styles.evalItem}>
              <View className={styles.evalItemHeader}>
                <Image
                  className={styles.evalAvatar}
                  src={item.customer_avatar || 'https://picsum.photos/id/1005/100/100'}
                  mode="aspectFill"
                />
                <Text className={styles.evalName}>{item.customer_name}</Text>
                {renderStars(item.score, 22)}
              </View>
              {!!item.content && <Text className={styles.evalContent}>{item.content}</Text>}
              <Text className={styles.evalTime}>{formatTime(item.created_at)}</Text>
              {!!item.reply_content && (
                <View className={styles.evalReply}>
                  <Text className={styles.evalReplyLabel}>陪玩回复：</Text>
                  <Text className={styles.evalReplyText}>{item.reply_content}</Text>
                </View>
              )}
            </View>
          ))
        )}
      </View>

      <View className={styles.footer}>
        <View className={styles.favBtn} onClick={handleToggleFavorite}>
          <Icon
            name="heart"
            size={44}
            color={favorited ? '#FF3B30' : '#6E6E73'}
            fill={favorited ? '#FF3B30' : 'none'}
          />
          <Text className={styles.favText}>{favorited ? '已收藏' : '收藏'}</Text>
        </View>
        <View className={`${styles.actionBtn} ${styles.buyBtn}`} onClick={handleBuyNow}>
          <Icon name="shopping-cart" size={36} color="#FFFFFF" className={styles.buyIcon} />
          <Text>选陪玩下单</Text>
        </View>
      </View>
      {loginSheet}
    </ScrollView>
  );
};

export default ProductDetailPage;
