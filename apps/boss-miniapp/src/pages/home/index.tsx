import React, { useState, useEffect } from 'react';
import { View, Text, Image, ScrollView, Swiper, SwiperItem } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import { fetchCustomerAchievements, CustomerAchievement } from '@/services/user';
import { fetchAnnouncements, Announcement } from '@/services/announcement';
import { fetchRankings, RankingData, RankingPeriod } from '@/services/ranking';
import { fetchBanners, Banner } from '@/services/banner';
import { fetchCoupons, claimCoupon, Coupon } from '@/services/coupon';
import { formatXaCoin } from '@xa/money';
import { resolveImageUrl } from '@/utils/media';
import { useLoginGuard } from '@/hooks/useLoginGuard';
import { Skeleton, Icon, XaLogo } from '@/components';
import styles from './index.module.scss';
import { getStoredToken } from '@/utils/auth';
import { openWecomCustomerService } from '@/services/support';

const rankPeriods = [
  { label: '日榜', value: 'day' },
  { label: '周榜', value: 'week' },
  { label: '月榜', value: 'month' }
] as const;

const bossPeriodLabelMap: Record<RankingPeriod, string> = {
  day: '今日',
  week: '本周',
  month: '本月'
};

const DEFAULT_AVATAR = 'https://copilot-cn.bytedance.net/api/ide/v1/text_to_image?prompt=game%20avatar%20round%20icon&image_size=square';

const HomePage: React.FC = () => {
  const [bossRankPeriod, setBossRankPeriod] = useState<RankingPeriod>('month');
  const [banners, setBanners] = useState<Banner[]>([]);
  const [coupons, setCoupons] = useState<Coupon[]>([]);
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  const [bossRanking, setBossRanking] = useState<RankingData>({ consume_rank: [], order_rank: [] });
  const [achievements, setAchievements] = useState<CustomerAchievement[]>([]);
  const [claiming, setClaiming] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const { ensureLogin, loginSheet } = useLoginGuard();

  useDidShow(() => {
    if (getStoredToken()) loadAll();
    else setLoading(false);
  });

  useEffect(() => {
    if (!getStoredToken()) return;
    fetchRankings(bossRankPeriod)
      .then(res => {
        if (res.code === 0 && res.data) setBossRanking(res.data);
      })
      .catch(e => console.error('加载老板消费榜失败', e));
  }, [bossRankPeriod]);

  const loadAll = async () => {
    setLoading(true);
    await loadHomeExtras();
    setLoading(false);
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    await Promise.all([
      loadHomeExtras(),
      fetchRankings(bossRankPeriod)
        .then(res => {
          if (res.code === 0 && res.data) setBossRanking(res.data);
        })
        .catch(() => undefined),
    ]);
    setRefreshing(false);
  };

  const loadHomeExtras = async () => {
    try {
      const [bannerRes, couponRes, annRes, achRes] = await Promise.all([
        fetchBanners(),
        fetchCoupons(),
        fetchAnnouncements(10),
        fetchCustomerAchievements()
      ]);
      if (bannerRes.code === 0 && bannerRes.data) setBanners(bannerRes.data);
      if (couponRes.code === 0 && couponRes.data) setCoupons(couponRes.data);
      if (annRes.code === 0 && annRes.data) setAnnouncements(annRes.data);
      if (achRes.code === 0 && achRes.data) setAchievements(achRes.data);
    } catch (e) {
      console.error('加载首页内容失败', e);
    }
  };

  const handleBannerTap = (banner: Banner) => {
    if (banner.link_type === 'PRODUCT' && banner.link_value) {
      Taro.navigateTo({ url: `/pages/productDetail/index?id=${banner.link_value}` });
    } else if (banner.link_type === 'ANNOUNCEMENT' && banner.link_value) {
      Taro.navigateTo({ url: `/pages/announcementDetail/index?id=${banner.link_value}` });
    } else if (banner.link_type === 'URL' && banner.link_value) {
      Taro.setClipboardData({ data: banner.link_value });
    }
  };

  const handleSelfOrder = () => {
    ensureLogin(() => {
      Taro.navigateTo({ url: '/pages/selfOrder/index' });
    });
  };

  const handleSupportOrder = () => {
    ensureLogin(async () => {
      Taro.showLoading({ title: '正在连接客服' });
      const result = await openWecomCustomerService({
        title: '我要咨询客服下单',
        path: '/pages/home/index?source=wecom_service',
      });
      Taro.hideLoading();
      if (!result.opened) {
        if (result.reason === 'failed') {
          Taro.showToast({ title: '企业微信客服暂时无法打开', icon: 'none' });
        }
        Taro.navigateTo({ url: '/pages/serviceCard/index' });
      }
    });
  };

  const handleAnnouncementMore = () => {
    Taro.navigateTo({ url: '/pages/announcement/index' });
  };

  const handleAnnouncementTap = (item: Announcement) => {
    Taro.navigateTo({ url: `/pages/announcementDetail/index?id=${item.id}` });
  };

  const handleCouponMore = () => {
    Taro.navigateTo({ url: '/pages/coupon/index' });
  };

  const handleClaim = async (coupon: Coupon) => {
    if (coupon.is_claimed || !coupon.claimable || claiming != null) return;
    setClaiming(coupon.id);
    try {
      const res = await claimCoupon(coupon.id);
      if (res.code === 0) {
        Taro.showToast({ title: '领取成功', icon: 'success' });
        const couponRes = await fetchCoupons();
        if (couponRes.code === 0 && couponRes.data) setCoupons(couponRes.data);
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

  // 老板消费风云榜（独立板块）：复用 consume_rank 数据
  const bossRank = bossRanking.consume_rank;
  const bossTop3 = bossRank.slice(0, 3);
  const bossRest = bossRank.slice(3, 8);
  const bossPodiumOrder = [bossTop3[1], bossTop3[0], bossTop3[2]];
  const handleBossRankMore = () => {
    ensureLogin(() => Taro.navigateTo({ url: '/pages/orderList/index' }));
  };

  return (
    <ScrollView
      className={styles.container}
      scrollY
      refresherEnabled
      refresherTriggered={refreshing}
      onRefresherRefresh={handleRefresh}
    >
      <View className={styles.header}>
        <View className={styles.brandRow}>
          <View className={styles.brandLogo}>
            <XaLogo size={44} color="#FFFFFF" />
          </View>
          <Text className={styles.headerTitle}>兴安电竞</Text>
        </View>
        <Text className={styles.headerSubtitle}>专业电竞陪玩 · 高手在线</Text>
      </View>

      {loading ? (
        <Skeleton variant="card-list" rows={4} />
      ) : (
        <>
      {banners.length > 0 && (
        <View className={styles.section}>
          <Swiper
            className={styles.bannerSwiper}
            indicatorDots
            autoplay
            circular
            indicatorActiveColor="#007AFF"
          >
            {banners.map(banner => (
              <SwiperItem key={banner.id} onClick={() => handleBannerTap(banner)}>
                <Image className={styles.bannerImage} src={resolveImageUrl(banner.image_url)} mode="aspectFill" />
              </SwiperItem>
            ))}
          </Swiper>
        </View>
      )}

      <View className={styles.section}>
        <View className={styles.quickRow}>
          <View className={styles.quickCard} onClick={() => handleSelfOrder()}>
            <Icon name="gamepad-2" size={48} color="#FFFFFF" className={styles.quickIcon} />
            <Text className={styles.quickTitle}>自助下单</Text>
            <Text className={styles.quickDesc}>自己挑选心仪大神</Text>
            <Icon name="chevron-right" size={36} color="rgba(255,255,255,0.7)" className={styles.quickArrow} />
          </View>
          <View className={`${styles.quickCard} ${styles.quickCardAlt}`} onClick={handleSupportOrder}>
            <Icon name="headphones" size={48} color="#007AFF" className={styles.quickIcon} />
            <Text className={styles.quickTitle}>客服下单</Text>
            <Text className={styles.quickDesc}>专属客服为你安排</Text>
            <Icon name="chevron-right" size={36} color="#AEAEB2" className={styles.quickArrow} />
          </View>
        </View>
      </View>

      {/* 老板消费风云榜 · 独立突出板块 */}
      <View className={styles.section}>
        <View className={styles.bossRank}>
          <View className={styles.bossHeader}>
            <View className={styles.bossTitleWrap}>
              <Icon name="crown" size={48} color="#FFE9B8" fill="#F5C761" className={styles.bossCrown} />
              <View>
                <Text className={styles.bossTitle}>老板消费风云榜</Text>
                <Text className={styles.bossSubtitle}>{bossPeriodLabelMap[bossRankPeriod]}豪气值 · 实时更新</Text>
              </View>
            </View>
            <View className={styles.bossMore} onClick={handleBossRankMore}>
              <Text className={styles.bossMoreText}>我的账单</Text>
              <Icon name="chevron-right" size={28} color="#FFE9B8" />
            </View>
          </View>

          <View className={styles.bossPeriods}>
            {rankPeriods.map(period => (
              <View
                key={period.value}
                className={`${styles.bossPeriod} ${bossRankPeriod === period.value ? styles.bossPeriodActive : ''}`}
                onClick={() => setBossRankPeriod(period.value)}
              >
                <Text className={styles.bossPeriodText}>{period.label}</Text>
              </View>
            ))}
          </View>

          {bossRank.length === 0 ? (
            <View className={styles.bossEmpty}>
              <Text className={styles.bossEmptyText}>虚位以待，快来成为{bossPeriodLabelMap[bossRankPeriod]}第一豪气老板</Text>
            </View>
          ) : (
            <>
              <View className={styles.bossPodium}>
                {bossPodiumOrder.map((item, idx) => {
                  if (!item) return <View key={`boss-podium-empty-${idx}`} className={styles.bossPodiumItem} />;
                  const place = item.rank;
                  const placeClass =
                    place === 1 ? styles.bossFirst : place === 2 ? styles.bossSecond : styles.bossThird;
                  return (
                    <View key={item.user_id} className={`${styles.bossPodiumItem} ${placeClass}`}>
                      <View className={styles.bossAvatarWrap}>
                        {place === 1 && <Icon name="crown" size={36} color="#F5C761" fill="#F5C761" className={styles.bossPodiumCrown} />}
                        <Image className={styles.bossAvatar} src={resolveImageUrl(item.avatar, DEFAULT_AVATAR)} mode="aspectFill" />
                        <Text className={styles.bossRankNo}>{place}</Text>
                      </View>
                      <Text className={styles.bossName}>{item.nickname}</Text>
                      <Text className={styles.bossAmount}>{formatXaCoin(item.total_amount)}币</Text>
                    </View>
                  );
                })}
              </View>

              {bossRest.length > 0 && (
                <View className={styles.bossList}>
                  {bossRest.map(item => (
                    <View key={item.user_id} className={styles.bossListItem}>
                      <Text className={styles.bossListNo}>{item.rank}</Text>
                      <Image className={styles.bossListAvatar} src={resolveImageUrl(item.avatar, DEFAULT_AVATAR)} mode="aspectFill" />
                      <Text className={styles.bossListName}>{item.nickname}</Text>
                      <Text className={styles.bossListAmount}>{formatXaCoin(item.total_amount)}币</Text>
                    </View>
                  ))}
                </View>
              )}
            </>
          )}
        </View>
      </View>

      {announcements.length > 0 && (
        <View className={styles.section}>
          <View className={styles.announceBar} onClick={handleAnnouncementMore}>
            <View className={styles.announceBadge}>
              <Icon name="megaphone" size={24} color="#007AFF" />
              <Text className={styles.announceBadgeText}>公告</Text>
            </View>
            <Swiper
              className={styles.announceSwiper}
              vertical
              autoplay
              circular
              interval={3000}
              displayMultipleItems={1}
            >
              {announcements.map(item => (
                <SwiperItem key={item.id}>
                  <View
                    className={styles.announceSwiperItem}
                    onClick={() => handleAnnouncementTap(item)}
                  >
                    <Text className={styles.announceSwiperText}>{item.title}</Text>
                  </View>
                </SwiperItem>
              ))}
            </Swiper>
            <Icon name="chevron-right" size={28} color="#AEAEB2" className={styles.announceMore} />
          </View>
        </View>
      )}

      {achievements.length > 0 && (
        <View className={styles.section}>
          <View className={styles.sectionHeader}>
            <Text className={styles.sectionTitle}>成就专区</Text>
            <Text className={styles.sectionDesc}>
              已解锁 {achievements.filter(a => a.unlocked).length}/{achievements.length}
            </Text>
          </View>
          <ScrollView className={styles.chipScroll} scrollX>
            <View className={styles.achievementRow}>
              {achievements.map(item => (
                <View
                  key={item.code}
                  className={`${styles.achievementCard} ${item.unlocked ? styles.achievementUnlocked : ''}`}
                >
                  <Text className={styles.achievementIcon}>{item.icon}</Text>
                  <Text className={styles.achievementTitle}>{item.title}</Text>
                  <Text className={styles.achievementDesc}>{item.desc}</Text>
                  {!item.unlocked && (
                    <Text className={styles.achievementProgress}>{item.current}/{item.target}</Text>
                  )}
                </View>
              ))}
            </View>
          </ScrollView>
        </View>
      )}

      {coupons.length > 0 && (
        <View className={styles.section}>
          <View className={styles.sectionHeader}>
            <Text className={styles.sectionTitle}>优惠专区</Text>
            <View className={styles.sectionMore} onClick={handleCouponMore}>
              <Text className={styles.sectionDesc}>全部</Text>
              <Icon name="chevron-right" size={28} color="#AEAEB2" />
            </View>
          </View>
          <ScrollView className={styles.chipScroll} scrollX>
            <View className={styles.couponRow}>
              {coupons.map(coupon => (
                <View key={coupon.id} className={styles.couponCard}>
                  <View className={styles.couponLeft}>
                    <Text className={styles.couponAmount}>{formatXaCoin(coupon.amount)}币</Text>
                    <Text className={styles.couponThreshold}>
                      {coupon.discount_type === 'DIRECT'
                        ? '无门槛'
                        : `满${formatXaCoin(coupon.threshold)}币可用`}
                    </Text>
                  </View>
                  <View className={styles.couponRight}>
                    <Text className={styles.couponName}>{coupon.name}</Text>
                    <View
                      className={`${styles.couponBtn} ${coupon.is_claimed || !coupon.claimable ? styles.couponBtnDisabled : ''}`}
                      onClick={() => handleClaim(coupon)}
                    >
                      <Text className={styles.couponBtnText}>
                        {coupon.is_claimed ? '已领' : !coupon.claimable ? '抢光' : '领取'}
                      </Text>
                    </View>
                  </View>
                </View>
              ))}
            </View>
          </ScrollView>
        </View>
      )}

        </>
      )}
      {loginSheet}
    </ScrollView>
  );
};

export default HomePage;
