import { useState, useEffect } from 'react';
import { View, Text, Image, Swiper, SwiperItem, Button, RichText } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { fetchServiceDetail, fetchServiceEvaluations, Evaluation } from '@/services/order';
import { toggleFavorite } from '@/services/favorite';
import { openWecomCustomerService } from '@/services/support';
import { ServiceInfo } from '@/types/order';
import { formatXaCoin } from '@xa/money';
import { useLoginGuard } from '@/hooks/useLoginGuard';
import { Skeleton, Empty } from '@/components';
import Icon from '@/components/Icon';
import RouletteWheel from '@/components/RouletteWheel';
import GameplayCover from '@/components/GameplayCover';
import { resolveImageUrl } from '@/utils/media';
import styles from './index.module.scss';

const renderStars = (score: number) => <View className={styles.stars}>
  {[1, 2, 3, 4, 5].map(n => <Icon key={n} name="star" size={24} color={n <= score ? '#002fa7' : '#f7f4ec'} fill={n <= score ? '#002fa7' : 'none'} />)}
</View>;

export default function ProductDetailPage() {
  const id = Taro.getCurrentInstance().router?.params.id;
  const [selectedId, setSelectedId] = useState(Number(id) || 0);
  const [service, setService] = useState<ServiceInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [variantError, setVariantError] = useState('');
  const [retry, setRetry] = useState(0);
  const [gameRounds, setGameRounds] = useState(1);
  const [currentImageIndex, setCurrentImageIndex] = useState(0);
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [evalTotal, setEvalTotal] = useState(0);
  const [evalAvg, setEvalAvg] = useState(0);
  const [evalError, setEvalError] = useState(false);
  const [favorited, setFavorited] = useState(false);
  const [favoriting, setFavoriting] = useState(false);
  const { ensureLogin, loginSheet } = useLoginGuard();

  useEffect(() => {
    let active = true;
    setLoading(true); setError(''); setVariantError(''); setEvalError(false);
    if (!selectedId) { setLoading(false); return; }
    fetchServiceDetail(selectedId).then(res => {
      if (!active) return;
      if (res.code !== 0 || !res.data) { (service ? setVariantError : setError)(res.msg || '该服务已下架'); return; }
      setService(res.data); setFavorited(!!res.data.is_favorited);
    }).catch(() => active && (service ? setVariantError : setError)('服务加载失败，请重试')).finally(() => active && setLoading(false));
    fetchServiceEvaluations(selectedId).then(res => {
      if (!active) return;
      if (res.code !== 0 || !res.data) { setEvalError(true); return; }
      setEvaluations(res.data.list); setEvalTotal(res.data.total); setEvalAvg(res.data.avg_score);
    }).catch(() => active && setEvalError(true));
    return () => { active = false; };
  }, [selectedId, retry]);

  const handleFavorite = () => ensureLogin(async () => {
    if (!service || favoriting || loading || variantError) return;
    setFavoriting(true);
    try {
      const res = await toggleFavorite(service.id);
      if (res.code === 0 && res.data) setFavorited(res.data.favorited);
      else Taro.showToast({ title: res.msg || '收藏失败', icon: 'none' });
    } catch { Taro.showToast({ title: '收藏失败，请重试', icon: 'none' }); }
    finally { setFavoriting(false); }
  });
  const handleCustomerService = () => ensureLogin(async () => {
    const result = await openWecomCustomerService({ title: `咨询：${service?.name || '玩法服务'}`, path: `/pages/productDetail/index?id=${selectedId}` });
    if (!result.opened) Taro.navigateTo({ url: '/pages/serviceCard/index' });
  });
  const handleBuyNow = () => {
    if (!service || loading || variantError) return;
    if (service.purchase_mode === 'CONSULT' || service.is_addon) return handleCustomerService();
    ensureLogin(() => Taro.navigateTo({ url: `/pages/checkout/index?service_id=${service.id}&game_rounds=${gameRounds}` }));
  };
  const chooseSpec = (field: 'platform' | 'security_level', value: string) => {
    if (!service) return;
    let choices = (service.variants || []).filter(item => !item.is_addon && item[field] === value);
    if (field === 'security_level') choices = choices.filter(item => item.platform === service.platform);
    else if (choices.some(item => item.security_level === service.security_level)) choices = choices.filter(item => item.security_level === service.security_level);
    const next = choices.find(item => item.catalog_variant === service.catalog_variant) || choices[0];
    if (next) setSelectedId(next.id);
  };

  if (loading && !service) return <View className={styles.container}><Skeleton variant="block" height="710rpx" /><Skeleton variant="card-list" rows={2} /></View>;
  if (!service || error) return <View className={styles.container}><Empty title={error || '服务不存在'} desc="可以返回玩法目录选择其他方案" /><Button onClick={() => setRetry(value => value + 1)}>重新加载</Button></View>;

  const images = service.images?.length ? service.images : service.cover_url ? [service.cover_url] : [];
  const variants = (service.variants?.length ? service.variants : [service]).filter(item => !item.is_addon && !item.is_gift);
  const platforms = [...new Map(variants.map(item => [item.platform || '', { id: item.platform || '', name: item.platform_name || '通用' }])).values()];
  const levels = [...new Map(variants.filter(item => item.platform === service.platform).map(item => [item.security_level || '', { id: item.security_level || '', name: item.security_level_name || '通用' }])).values()];
  const tiers = variants.filter(item => item.platform === service.platform && item.security_level === service.security_level);
  const isRoulette = service.gameplay_kind === 'ROULETTE';
  return <View className={styles.container}>
    {(platforms.length > 1 || levels.length > 1) && <View className={styles.projectFilters}>
      {platforms.length > 1 && <View className={styles.filterLine}><Text className={styles.label}>平台</Text>{platforms.map(item => <Button key={item.id} className={`${styles.specChip} ${service.platform === item.id ? styles.active : ''}`} aria-pressed={service.platform === item.id} onClick={() => chooseSpec('platform', item.id)}>{item.name}</Button>)}</View>}
      {levels.length > 1 && <View className={styles.filterLine}><Text className={styles.label}>难度</Text>{levels.map(item => <Button key={item.id} className={`${styles.specChip} ${service.security_level === item.id ? styles.active : ''}`} aria-pressed={service.security_level === item.id} onClick={() => chooseSpec('security_level', item.id)}>{item.name}</Button>)}</View>}
      {loading && <Text className={styles.muted}>正在更新对应玩法…</Text>}
    </View>}
    {!!service.introduction_images?.length ? <View className={styles.advertLanding}>{service.introduction_images.map(src => <Image key={src} className={styles.advertPoster} src={resolveImageUrl(src)} mode="widthFix" lazyLoad />)}</View> : !service.catalog_family && images.length ? <Swiper className={styles.swiper} current={currentImageIndex} onChange={e => setCurrentImageIndex(e.detail.current)} indicatorDots={images.length > 1} indicatorActiveColor="#002fa7" circular>
      {images.map((src, index) => <SwiperItem key={index}><Image className={styles.swiperImage} src={resolveImageUrl(src)} mode="aspectFill" /></SwiperItem>)}
    </Swiper> : !service.catalog_family && <GameplayCover size="hero" name={service.gameplay_name || service.name} cover={service.gameplay_cover_url} theme={service.gameplay_theme} />}

    <View className={styles.info}>
      <View className={styles.titleRow}><Text className={styles.productName}>{service.catalog_family || service.name}</Text><Button className={styles.favorite} onClick={handleFavorite} aria-label={favorited ? '取消收藏' : '收藏服务'}><Icon name="heart" size={32} color={favorited ? '#002fa7' : '#92909a'} fill={favorited ? '#002fa7' : 'none'} /></Button></View>
      <Text className={styles.description}>{service.description}</Text>
      <View className={styles.priceRow}><View><Text className={styles.price}>{formatXaCoin(service.price)}</Text><Text className={styles.priceUnit}> 兴安币 / 份</Text></View>{typeof service.sales_count === 'number' && <Text className={styles.sales}>已售 {service.sales_count}</Text>}</View>
      <View className={styles.assurances}>{['平台派单', '客服跟进', '进度可查', '售后支持'].map(text => <View className={styles.assurance} key={text}><Icon name="check-circle" size={24} color="#fffdf5" /><Text>{text}</Text></View>)}</View>
    </View>

    <View className={styles.section}>
      <View className={styles.specRow}><Text className={styles.label}>已选规格</Text><Text className={styles.specValue}>{service.platform_name} / {service.security_level_name}</Text></View>
      {tiers.length > 1 && <View className={styles.tierGrid}>{tiers.map(item => <Button key={item.id} className={`${styles.tierOption} ${service.id === item.id ? styles.active : ''}`} onClick={() => setSelectedId(item.id)}><Text>{item.catalog_variant || item.name}</Text><Text className={styles.tierPrice}>{formatXaCoin(item.price)} 兴安币</Text></Button>)}</View>}
      <View className={styles.quantityRow}><Text className={styles.label}>购买数量</Text>{isRoulette ? <Text className={styles.muted}>1 份 · 每单确定一个目标</Text> : <View className={styles.stepper}><Button className={styles.quantityBtn} aria-label="减少数量" disabled={gameRounds <= 1} onClick={() => setGameRounds(value => Math.max(1, value - 1))}>−</Button><Text>{gameRounds}</Text><Button className={styles.quantityBtn} aria-label="增加数量" onClick={() => setGameRounds(value => value + 1)}>+</Button></View>}</View>
    </View>

    {isRoulette && !!service.roulette_pool?.length && <View className={styles.section}><Text className={styles.sectionTitle}>转盘玩法</Text><Text className={styles.description}>先了解服务目标与概率，可免费模拟体验。下单支付后确定本单目标，结果可在订单中查看。</Text><RouletteWheel pool={service.roulette_pool} /></View>}
    {!!service.details_html && <View className={styles.section}><View className={styles.evalHeader}><Text className={styles.sectionTitle}>玩法详情与价目</Text></View><RichText className={styles.richDocument} nodes={service.details_html.replace(/\sdata-original="[^"]*"/g, "").replace(/src="([^"]+)"/g, (_, src) => `src="${resolveImageUrl(src)}"`)} /></View>}
    {!!variantError && <View className={styles.section} onClick={() => setRetry(value => value + 1)}><Text>{variantError} · 点击重试</Text></View>}
    <View className={styles.section}>
      <Text className={styles.sectionTitle}>商品详情</Text>
      {!!service.highlights?.length && <View className={styles.highlights}>{service.highlights.map(tip => <Text className={styles.highlight} key={tip}>{tip}</Text>)}</View>}
      {!service.details_html && <Text className={styles.rules}>{service.rules || service.description || '具体服务安排请联系客服确认。'}</Text>}
      <View className={styles.process}><Text className={styles.processTitle}>下单流程</Text><Text>确认方案 → 余额下单 → 派单接单 → 服务完成</Text></View>
      <Text className={styles.notice}>余额由人工客服收款后充值；服务中有疑问，可联系订单负责客服处理。</Text>
    </View>
    <View className={styles.section}>
      <View className={styles.evalHeader}><Text className={styles.sectionTitle}>用户评价{evalTotal > 0 ? `（${evalTotal}）` : ''}</Text>{evalTotal > 0 && <Text className={styles.evalScore}>{evalAvg.toFixed(1)} 分</Text>}</View>
      {evalError ? <Text className={styles.evalEmpty} onClick={() => setRetry(value => value + 1)}>评价加载失败，点击重试</Text> : !evaluations.length ? <Text className={styles.evalEmpty}>暂无评价</Text> : evaluations.map(item => <View className={styles.evalItem} key={item.id}>
        <View className={styles.evalItemHeader}>{item.customer_avatar ? <Image className={styles.evalAvatar} src={resolveImageUrl(item.customer_avatar)} mode="aspectFill" /> : <View className={styles.evalAvatar}>玩</View>}<Text className={styles.evalName}>{item.customer_name}</Text>{renderStars(item.score)}</View>
        {!!item.content && <Text className={styles.evalContent}>{item.content}</Text>}<Text className={styles.evalTime}>{new Date(item.created_at).toLocaleDateString('zh-CN')}</Text>
        {!!item.reply_content && <View className={styles.evalReply}><Text>陪玩回复：{item.reply_content}</Text></View>}
      </View>)}
    </View>
    <View className={styles.footer}><Text className={styles.footerHint}>客服跟进全程 · 接单进度可在订单中查看</Text><View className={styles.footerActions}><Button className={styles.customerBtn} onClick={handleCustomerService}>客服</Button><Button className={styles.buyBtn} disabled={loading || !!variantError} onClick={handleBuyNow}>{service.purchase_mode === 'CONSULT' ? '咨询方案' : '立即下单'}</Button></View></View>
    {loginSheet}
  </View>;
}
