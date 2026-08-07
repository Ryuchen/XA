import React, { useEffect, useMemo, useState } from 'react';
import { View, Text, Image, Input, ScrollView } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import { fetchEscorts, EscortProfile } from '@/services/user';
import { fetchRankings, RankingData, RankingPeriod } from '@/services/ranking';
import { formatXaCoin } from '@xa/money';
import { useCatalogStore, useServices } from '@/store';
import { getStoredToken } from '@/utils/auth';
import { useLoginGuard } from '@/hooks/useLoginGuard';
import { useVoicePreview } from '@/hooks/useVoicePreview';
import { Empty, Icon, Skeleton } from '@/components';
import { resolveImageUrl } from '@/utils/media';
import styles from './index.module.scss';

const rankPeriods = [
  { label: '日榜', value: 'day' },
  { label: '周榜', value: 'week' },
  { label: '月榜', value: 'month' }
] as const;

const statusMeta = (status: string) => {
  const value = (status || '').toLowerCase();
  if (value === 'available' || value === 'online') return { key: 'online', text: '在线' };
  if (value === 'busy') return { key: 'busy', text: '忙碌' };
  return { key: 'offline', text: '离线' };
};

const CompanionsPage: React.FC = () => {
  const [searchValue, setSearchValue] = useState('');
  const [rankPeriod, setRankPeriod] = useState<RankingPeriod>('month');
  const [ranking, setRanking] = useState<RankingData>({ consume_rank: [], order_rank: [] });
  const [escorts, setEscorts] = useState<EscortProfile[]>([]);
  // 礼物服务与游戏分类属于平台目录，跨页共享缓存
  const gifts = useServices('GIFT');
  const gameCategories = useCatalogStore(state => state.gameCategories);
  const loadCatalogServices = useCatalogStore(state => state.loadServices);
  const loadCatalogGameCategories = useCatalogStore(state => state.loadGameCategories);
  const [selectedCategoryId, setSelectedCategoryId] = useState<number | null>(null);
  const [escortLoading, setEscortLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const { ensureLogin, loginSheet } = useLoginGuard();
  const { playingId, toggle: toggleVoice } = useVoicePreview();

  const loadEscorts = async (categoryId: number | null = selectedCategoryId) => {
    const res = await fetchEscorts(undefined, categoryId ?? undefined);
    if (res.code === 0 && res.data) setEscorts(res.data);
  };

  const loadRanking = async (period: RankingPeriod) => {
    const res = await fetchRankings(period);
    // 后端可能只返回 consume_rank；以响应为主、缺失字段补默认值，避免 order_rank 为 undefined 导致渲染崩溃
    if (res.code === 0 && res.data) {
      setRanking({ ...res.data, consume_rank: res.data.consume_rank || [], order_rank: res.data.order_rank || [] });
    }
  };

  useDidShow(() => {
    if (!getStoredToken()) {
      setLoading(false);
      return;
    }
    setLoading(true);
    Promise.all([
      loadEscorts(selectedCategoryId),
      loadCatalogGameCategories(),
      loadCatalogServices('GIFT'),
    ])
      .catch(e => console.error('加载陪玩人员失败', e))
      .finally(() => setLoading(false));
  });

  useEffect(() => {
    if (!getStoredToken()) return;
    let active = true;
    fetchRankings(rankPeriod)
      .then(res => {
        if (active && res.code === 0 && res.data) {
          setRanking({ ...res.data, consume_rank: res.data.consume_rank || [], order_rank: res.data.order_rank || [] });
        }
      })
      .catch(e => console.error('加载陪玩接单榜失败', e));
    return () => { active = false; };
  }, [rankPeriod]);

  const filteredEscorts = useMemo(() => {
    const keyword = searchValue.trim().toLowerCase();
    if (!keyword) return escorts;
    return escorts.filter(item =>
      [item.nickname, item.rank, item.bio, item.city]
        .filter(Boolean)
        .some(value => value.toLowerCase().includes(keyword))
    );
  }, [escorts, searchValue]);

  const handleRefresh = async () => {
    if (!getStoredToken()) return;
    setRefreshing(true);
    await Promise.all([
      loadEscorts(selectedCategoryId).catch(() => undefined),
      // 手动下拉刷新时强制穿透目录缓存
      loadCatalogGameCategories(true).catch(() => undefined),
      loadCatalogServices('GIFT', true).catch(() => undefined),
      loadRanking(rankPeriod).catch(() => undefined)
    ]);
    setRefreshing(false);
  };

  const handleGameChange = async (categoryId: number | null) => {
    if (categoryId === selectedCategoryId || escortLoading) return;
    const previousId = selectedCategoryId;
    setSelectedCategoryId(categoryId);
    setEscortLoading(true);
    try {
      await loadEscorts(categoryId);
    } catch (e) {
      setSelectedCategoryId(previousId);
      console.error('按游戏筛选陪玩失败', e);
      Taro.showToast({ title: '筛选失败，请稍后重试', icon: 'none' });
    } finally {
      setEscortLoading(false);
    }
  };

  const handleOrder = (escortId: number) => {
    ensureLogin(() => Taro.navigateTo({ url: `/pages/players/index?escortId=${escortId}` }));
  };

  const handleGiftTap = (giftId: number) => {
    Taro.navigateTo({ url: `/pages/productDetail/index?id=${giftId}` });
  };

  const handleLogin = () => {
    ensureLogin(() => {
      setLoading(true);
      Promise.all([
        loadEscorts(selectedCategoryId),
        loadCatalogGameCategories(),
        loadCatalogServices('GIFT'),
        loadRanking(rankPeriod)
      ]).finally(() => setLoading(false));
    });
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
        <Text className={styles.headerTitle}>发现心仪陪玩</Text>
        <Text className={styles.headerSubtitle}>查看实力排行，也可以按昵称、段位与城市搜索</Text>
        <View className={styles.searchBar}>
          <Icon name="search" size={32} color="#8E8E93" />
          <Input
            className={styles.searchInput}
            value={searchValue}
            placeholder="搜索陪玩昵称、段位或城市"
            onInput={event => setSearchValue(event.detail.value)}
          />
        </View>
      </View>

      {!getStoredToken() ? (
        <View className={styles.loginState}>
          <Empty icon="🎮" title="登录后查看陪玩" desc="微信快捷登录后即可查看排行榜、档期并选择陪玩">
            <View className={styles.loginButton} onClick={handleLogin}>微信快捷登录</View>
          </Empty>
        </View>
      ) : loading ? (
        <View className={styles.loadingWrap}><Skeleton variant="card-list" rows={5} /></View>
      ) : (
        <>
          <View className={styles.section}>
            <View className={styles.rankCard}>
              <View className={styles.rankHeader}>
                <View>
                  <Text className={styles.rankTitle}>陪玩接单榜</Text>
                  <Text className={styles.rankSubtitle}>按完成接单量排序 · 实时更新</Text>
                </View>
                <View className={styles.rankPeriods}>
                  {rankPeriods.map(period => (
                    <View
                      key={period.value}
                      className={`${styles.rankPeriod} ${rankPeriod === period.value ? styles.rankPeriodActive : ''}`}
                      onClick={() => setRankPeriod(period.value)}
                    >
                      <Text className={styles.rankPeriodText}>{period.label}</Text>
                    </View>
                  ))}
                </View>
              </View>

              {ranking.order_rank.length === 0 ? (
                <View className={styles.rankEmpty}>本期暂无接单数据</View>
              ) : (
                <View className={styles.rankList}>
                  <View className={styles.rankRowHeader}>
                    <Text className={styles.rankNo}>排名</Text>
                    <Text className={styles.rankPlayer}>陪玩</Text>
                    <Text className={styles.rankOrders}>接单</Text>
                    <Text className={styles.rankLevel}>等级</Text>
                  </View>
                  {ranking.order_rank.map(item => (
                    <View key={item.user_id} className={styles.rankRow}>
                      <Text className={`${styles.rankNo} ${item.rank <= 3 ? styles.rankNoTop : ''}`}>{item.rank}</Text>
                      <View className={styles.rankPlayer}>
                        <Image className={styles.rankAvatar} src={resolveImageUrl(item.avatar)} mode="aspectFill" />
                        <Text className={styles.rankName}>{item.nickname}</Text>
                      </View>
                      <Text className={styles.rankOrders}>{item.order_count}单</Text>
                      <Text className={styles.rankLevel}>{item.title || '—'}</Text>
                    </View>
                  ))}
                </View>
              )}
            </View>
          </View>

          {gifts.length > 0 && (
            <View className={styles.section}>
              <View className={styles.sectionHeader}>
                <View>
                  <Text className={styles.sectionTitle}>礼物专区</Text>
                  <Text className={styles.sectionSubtitle}>挑选礼物，为心仪陪玩应援</Text>
                </View>
                <View className={styles.giftBadge}>
                  <Icon name="gift" size={24} color="#FF2D55" />
                  <Text>{gifts.length} 款</Text>
                </View>
              </View>
              <ScrollView className={styles.giftScroll} scrollX>
                <View className={styles.giftRow}>
                  {gifts.map(gift => (
                    <View key={gift.id} className={styles.giftCard} onClick={() => handleGiftTap(gift.id)}>
                      <Image
                        className={styles.giftCover}
                        src={resolveImageUrl(gift.cover_url)}
                        mode="aspectFill"
                      />
                      <View className={styles.giftMain}>
                        <Text className={styles.giftName}>{gift.name}</Text>
                        <Text className={styles.giftPrice}>{formatXaCoin(gift.price)}兴安币</Text>
                      </View>
                    </View>
                  ))}
                </View>
              </ScrollView>
            </View>
          )}

          <View className={styles.section}>
            <View className={styles.sectionHeader}>
              <View>
                <Text className={styles.sectionTitle}>全部陪玩</Text>
                <Text className={styles.sectionSubtitle}>按游戏筛选当前可接单人员</Text>
              </View>
              <Text className={styles.playerCount}>{filteredEscorts.length} 位</Text>
            </View>

            <View className={styles.gameFilterWrap}>
              <View className={styles.gameFilterRow}>
                <View
                  className={`${styles.gameFilterChip} ${selectedCategoryId === null ? styles.gameFilterChipActive : ''}`}
                  onClick={() => handleGameChange(null)}
                >
                  <Text className={styles.gameFilterText}>全部</Text>
                </View>
                {gameCategories.map(category => (
                  <View
                    key={category.id}
                    className={`${styles.gameFilterChip} ${selectedCategoryId === category.id ? styles.gameFilterChipActive : ''}`}
                    onClick={() => handleGameChange(category.id)}
                  >
                    {category.icon_url && (
                      <Image className={styles.gameFilterIcon} src={resolveImageUrl(category.icon_url)} mode="aspectFit" />
                    )}
                    <Text className={styles.gameFilterText}>{category.name}</Text>
                  </View>
                ))}
              </View>
            </View>

            <View className={`${styles.playerList} ${escortLoading ? styles.playerListLoading : ''}`}>
              {filteredEscorts.map(escort => {
                const status = statusMeta(escort.status);
                return (
                  <View key={escort.id} className={styles.playerCard}>
                    <View className={styles.playerAvatarWrap}>
                      <Image className={styles.playerAvatar} src={resolveImageUrl(escort.avatar)} mode="aspectFill" />
                      <View className={`${styles.status} ${styles[status.key]}`}>
                        <View className={styles.statusDot} />
                        <Text>{status.text}</Text>
                      </View>
                    </View>
                    <View className={styles.playerMain}>
                      <View className={styles.playerNameRow}>
                        <Text className={styles.playerName}>{escort.nickname}</Text>
                      </View>
                      <Text className={styles.playerRank}>{escort.rank || '暂无段位'}</Text>
                      {escort.voice_card_url && (
                        <View
                          className={`${styles.voicePreview} ${playingId === escort.id ? styles.voicePreviewPlaying : ''}`}
                          onClick={() => toggleVoice(escort.id, escort.voice_card_url)}
                        >
                          <Text className={styles.voicePreviewIcon}>{playingId === escort.id ? '⏸' : '🔊'}</Text>
                          <Text>{playingId === escort.id ? '播放中' : '试听'}</Text>
                        </View>
                      )}
                      <View className={styles.playerMeta}>
                        <Text>好评 {escort.favorableRate || 0}%</Text>
                        <Text>已接 {escort.orderCount}</Text>
                      </View>
                      <Text className={styles.playerBio}>{escort.bio || '这位陪玩还没有填写个人介绍'}</Text>
                      <View className={styles.playerBottom}>
                        <View>
                          <Text className={styles.playerPrice}>{formatXaCoin(escort.pricePerHour)}兴安币</Text>
                          <Text className={styles.playerPriceUnit}>/小时</Text>
                        </View>
                        <View className={styles.orderButton} onClick={() => handleOrder(escort.id)}>
                          <Text>选TA下单</Text>
                        </View>
                      </View>
                    </View>
                  </View>
                );
              })}
              {filteredEscorts.length === 0 && (
                <Empty
                  icon="🔍"
                  title="没有找到相关陪玩"
                  desc={selectedCategoryId != null ? `暂无可接${gameCategories.find(c => c.id === selectedCategoryId)?.name || ''}的陪玩，试试其他游戏` : '换一个昵称、段位或城市关键词试试'}
                />
              )}
            </View>
          </View>
        </>
      )}
      {loginSheet}
    </ScrollView>
  );
};

export default CompanionsPage;
