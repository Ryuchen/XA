import React, { useState } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import { useDidShow } from '@tarojs/taro';
import { fetchMyCoupons, UserCoupon, UserCouponStatus } from '@/services/coupon';
import { formatXaCoin } from '@/utils/format';
import Icon, { IconName } from '@/components/Icon';
import styles from './index.module.scss';

const TABS: { label: string; value: UserCouponStatus }[] = [
  { label: '未使用', value: 'UNUSED' },
  { label: '已使用', value: 'USED' },
  { label: '已过期', value: 'EXPIRED' },
];

const STATUS_LABEL: Record<UserCouponStatus, string> = {
  UNUSED: '未使用',
  USED: '已使用',
  EXPIRED: '已过期',
};

const STATUS_ICON: Record<UserCouponStatus, IconName> = {
  UNUSED: 'check-circle',
  USED: 'check-check',
  EXPIRED: 'info',
};

const MyCouponPage: React.FC = () => {
  const [coupons, setCoupons] = useState<UserCoupon[]>([]);
  const [activeStatus, setActiveStatus] = useState<UserCouponStatus>('UNUSED');

  const load = async () => {
    const res = await fetchMyCoupons();
    if (res.code === 0 && res.data) setCoupons(res.data);
  };

  useDidShow(() => {
    load();
  });

  const visible = coupons.filter(c => c.status === activeStatus);

  return (
    <View className={styles.container}>
      <View className={styles.tabRow}>
        {TABS.map(tab => (
          <View
            key={tab.value}
            className={`${styles.tab} ${activeStatus === tab.value ? styles.tabActive : ''}`}
            onClick={() => setActiveStatus(tab.value)}
          >
            <Text>{tab.label}</Text>
          </View>
        ))}
      </View>

      <ScrollView className={styles.list} scrollY>
        {visible.map(coupon => (
          <View
            key={coupon.id}
            className={`${styles.card} ${coupon.status !== 'UNUSED' ? styles.cardDisabled : ''}`}
          >
            <View className={styles.notchTop} />
            <View className={styles.notchBottom} />
            <View className={styles.left}>
              <Text className={styles.amount}>
                <Text className={styles.amountSymbol}>币</Text>
                {formatXaCoin(coupon.amount)}
              </Text>
              <Text className={styles.threshold}>
                {coupon.discount_type === 'DIRECT'
                  ? '无门槛'
                  : `满${formatXaCoin(coupon.threshold)}兴安币可用`}
              </Text>
            </View>
            <View className={styles.divider} />
            <View className={styles.right}>
              <Text className={styles.name}>{coupon.name}</Text>
              <Text className={styles.valid}>有效期至 {coupon.valid_to?.slice(0, 10)}</Text>
            </View>
            <View className={styles.statusTag}>
              <Icon
                name={STATUS_ICON[coupon.status]}
                size={28}
                color={coupon.status === 'UNUSED' ? '#007AFF' : '#AEAEB2'}
              />
              <Text className={styles.statusText}>{STATUS_LABEL[coupon.status]}</Text>
            </View>
          </View>
        ))}

        {visible.length === 0 && (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>
              <Icon name="ticket" size={120} color="#AEAEB2" />
            </View>
            <Text className={styles.emptyTitle}>暂无{STATUS_LABEL[activeStatus]}的优惠券</Text>
            <Text className={styles.emptyDesc}>去领券中心领取更多优惠</Text>
          </View>
        )}
      </ScrollView>
    </View>
  );
};

export default MyCouponPage;
