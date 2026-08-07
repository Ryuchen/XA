import React, { useState } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro, { useDidShow, usePullDownRefresh } from '@tarojs/taro';
import { fetchCoupons, claimCoupon, Coupon } from '@/services/coupon';
import { formatXaCoin } from '@xa/money';
import { Skeleton } from '@/components';
import Icon from '@/components/Icon';
import styles from './index.module.scss';

const CouponPage: React.FC = () => {
  const [coupons, setCoupons] = useState<Coupon[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [claiming, setClaiming] = useState<number | null>(null);

  const load = async () => {
    try {
      const res = await fetchCoupons();
      if (res.code === 0 && res.data) {
        setCoupons(res.data);
        setError(false);
      } else {
        setError(true);
      }
    } catch (e) {
      console.error('加载优惠券失败', e);
      setError(true);
    } finally {
      setLoading(false);
    }
  };

  // useDidShow 首次进入也会触发，无需额外 useEffect，避免首屏双份请求
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

  const handleClaim = async (coupon: Coupon) => {
    if (coupon.is_claimed || !coupon.claimable || claiming != null) return;

    setClaiming(coupon.id);
    try {
      const res = await claimCoupon(coupon.id);
      if (res.code === 0) {
        Taro.showToast({ title: '领取成功', icon: 'success' });
        await load();
      } else {
        Taro.showToast({ title: res.msg || '领取失败', icon: 'none' });
      }
    } catch (e) {
      console.error('领取失败', e);
      Taro.showToast({ title: '领取失败，请稍后重试', icon: 'none' });
    } finally {
      setClaiming(null);
    }
  };

  return (
    <ScrollView className={styles.container} scrollY>
      {loading ? (
        <Skeleton variant="card-list" rows={4} />
      ) : error ? (
        <View className={styles.empty}>
          <View className={styles.emptyIcon}>
            <Icon name="info" size={72} color="#FF3B30" />
          </View>
          <Text className={styles.emptyTitle}>加载失败</Text>
          <Text className={styles.emptyDesc}>网络异常，请稍后重试</Text>
          <View className={styles.retryBtn} onClick={handleRetry}>
            <Text>重新加载</Text>
          </View>
        </View>
      ) : (
        <>
          {coupons.map(coupon => {
            const claimed = coupon.is_claimed;
            const soldOut = !coupon.is_claimed && !coupon.claimable;
            const disabled = claimed || soldOut;
            return (
              <View key={coupon.id} className={styles.card}>
                <View className={styles.notchTop} />
                <View className={styles.notchBottom} />
                <View className={styles.left}>
                  <Text className={styles.amount}>
                    <Text className={styles.currency}>币</Text>
                    {formatXaCoin(coupon.amount)}
                  </Text>
                  <Text className={styles.threshold}>
                    {coupon.discount_type === 'DIRECT'
                      ? '无门槛'
                      : `满${formatXaCoin(coupon.threshold)}兴安币可用`}
                  </Text>
                </View>
                <View className={styles.right}>
                  <View className={styles.info}>
                    <View className={styles.nameRow}>
                      <Icon name="ticket-percent" size={32} color="#FF9500" />
                      <Text className={styles.name}>{coupon.name}</Text>
                    </View>
                    <Text className={styles.valid}>有效期至 {coupon.valid_to?.slice(0, 10)}</Text>
                  </View>
                  <View
                    className={`${styles.btn} ${disabled ? styles.btnDisabled : ''}`}
                    onClick={() => handleClaim(coupon)}
                  >
                    {claimed && <Icon name="check" size={28} color="#AEAEB2" />}
                    <Text className={styles.btnText}>
                      {claimed ? '已领取' : soldOut ? '已抢光' : '立即领取'}
                    </Text>
                  </View>
                </View>
              </View>
            );
          })}

          {coupons.length === 0 && (
            <View className={styles.empty}>
              <View className={styles.emptyIcon}>
                <Icon name="ticket" size={72} color="#AEAEB2" />
              </View>
              <Text className={styles.emptyTitle}>暂无可领取的优惠券</Text>
              <Text className={styles.emptyDesc}>敬请期待更多福利活动</Text>
            </View>
          )}
        </>
      )}
    </ScrollView>
  );
};

export default CouponPage;
