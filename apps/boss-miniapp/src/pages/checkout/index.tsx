import React, { useEffect, useState } from 'react';
import { View, Text, ScrollView, Textarea, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { createOrder, fetchServices, quoteOrder, CreateOrderPayload, OrderQuote } from '@/services/order';
import { fetchMyCoupons, UserCoupon } from '@/services/coupon';
import { fetchMe, fetchEscorts, EscortProfile, MeProfile } from '@/services/user';
import { formatXaCoin } from '@/utils/format';
import { fetchContactCards, openWecomCustomerService, SupportContactCard } from '@/services/support';
import { useLoginGuard } from '@/hooks/useLoginGuard';
import { Skeleton } from '@/components';
import Icon from '@/components/Icon';
import { getStoredToken } from '@/utils/auth';
import styles from './index.module.scss';

interface Service {
  id: number;
  name: string;
  description: string;
  price: number;
  game_category?: number | null;
  game_category_name?: string;
}

type DispatchMode = 'assign' | 'platform';

const CheckoutPage: React.FC = () => {
  const [services, setServices] = useState<Service[]>([]);
  const [guidedFlow, setGuidedFlow] = useState(false);
  const [selectedService, setSelectedService] = useState<Service | null>(null);
  const [providerId, setProviderId] = useState<number | null>(null);
  const [selectedEscort, setSelectedEscort] = useState<EscortProfile | null>(null);
  const [dispatchMode, setDispatchMode] = useState<DispatchMode>('platform');
  const [supportContacts, setSupportContacts] = useState<SupportContactCard[]>([]);
  const [supportContactId, setSupportContactId] = useState<number | null>(null);
  const [gameRounds, setGameRounds] = useState(1);
  const [gameAccount, setGameAccount] = useState({
    region: '',
    nickname: '',
    uid: ''
  });
  const [meProfile, setMeProfile] = useState<MeProfile | null>(null);
  const [remark, setRemark] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [usableCoupons, setUsableCoupons] = useState<UserCoupon[]>([]);
  const [selectedCouponId, setSelectedCouponId] = useState<number | null>(null);
  const [quote, setQuote] = useState<OrderQuote | null>(null);
  const [quoteLoading, setQuoteLoading] = useState(false);
  const [quoteError, setQuoteError] = useState('');
  const { ensureLogin, loginSheet } = useLoginGuard();

  useEffect(() => {
    const params = Taro.getCurrentInstance().router?.params || {};
    const presetServiceId = params.serviceId || params.service_id;
    const presetPlayerId = params.playerId || params.provider_id;
    const presetSupportContactId = params.supportContactId || params.support_contact_id;
    const presetRounds = params.game_rounds ? parseInt(params.game_rounds, 10) : 1;
    const isGuidedFlow = params.flow === 'self_order';
    setGuidedFlow(isGuidedFlow);

    if (presetRounds > 0) setGameRounds(presetRounds);
    if (presetPlayerId) {
      const pid = Number(presetPlayerId);
      if (!Number.isNaN(pid)) {
        setProviderId(pid);
        setDispatchMode('assign');
        // 解析已指定陪玩的展示信息（昵称/头像），下单页需明确"指定的是谁"
        fetchEscorts()
          .then(res => {
            if (res.code === 0 && res.data) {
              const matched = res.data.find(e => e.id === pid);
              if (matched) setSelectedEscort(matched);
            }
          })
          .catch(e => console.warn('获取指定陪玩信息失败', e));
      }
    }

    fetchContactCards()
      .then(res => {
        if (res.code !== 0 || !res.data) return;
        setSupportContacts(res.data);
        const preset = Number(presetSupportContactId);
        const selected = res.data.find(item => item.id === preset) || res.data[0];
        setSupportContactId(selected?.id || null);
      })
      .catch(e => console.warn('获取负责客服失败', e));

    const loadServices = async () => {
      try {
        const res = await fetchServices();
        if (res?.code === 0 && res.data) {
          setServices(
            isGuidedFlow && presetServiceId
              ? res.data.filter(service => service.id === Number(presetServiceId))
              : res.data
          );
          if (presetServiceId) {
            const match = res.data.find(s => s.id === Number(presetServiceId));
            if (match) setSelectedService(match);
          } else if (res.data.length > 0) {
            setSelectedService(res.data[0]);
          }
        }
      } catch (error) {
        console.warn('获取服务列表失败', error);
      }
    };
    loadServices();
  }, []);

  useEffect(() => {
    fetchMe()
      .then(res => {
        if (res.code === 0 && res.data) setMeProfile(res.data);
      })
      .catch(e => console.warn('获取常用游戏资料失败', e));
  }, []);

  useEffect(() => {
    if (!meProfile || !selectedService) return;
    const matched = (meProfile.game_profiles || []).find(
      item => item.game_category === selectedService.game_category
    );
    if (matched) {
      setGameAccount({ region: matched.region, nickname: matched.nickname, uid: matched.uid });
      return;
    }
    if ((meProfile.game_profiles || []).length === 0) {
      setGameAccount({
        region: meProfile.game_region || '',
        nickname: meProfile.game_nickname || '',
        uid: meProfile.game_uid || '',
      });
      return;
    }
    setGameAccount({ region: '', nickname: '', uid: '' });
  }, [meProfile, selectedService]);

  const originalAmount = selectedService ? selectedService.price * gameRounds : 0;

  // 报价试算与正式下单共用的订单载荷，避免共享字段重复构造。
  // includeGameAccount 为 true 时附带游戏账号信息与备注（仅下单需要）。
  const buildOrderPayload = (opts?: { includeGameAccount?: boolean }): CreateOrderPayload => {
    const payload: CreateOrderPayload = {
      service_id: selectedService!.id,
      game_rounds: gameRounds,
    };
    if (supportContactId) payload.support_contact_id = supportContactId;
    // 仅"指定陪玩"模式携带 provider_id；平台派单不指定，交由后台待接单/客服指派
    if (dispatchMode === 'assign' && providerId) payload.provider_id = providerId;
    if (selectedCouponId != null) payload.user_coupon_id = selectedCouponId;
    if (opts?.includeGameAccount) {
      payload.game_region = gameAccount.region.trim();
      payload.game_nickname = gameAccount.nickname.trim();
      payload.game_uid = gameAccount.uid.trim();
      payload.remark = remark.trim();
    }
    return payload;
  };

  useEffect(() => {
    if (originalAmount <= 0) {
      setUsableCoupons([]);
      return;
    }
    fetchMyCoupons({ usable: true, amount: originalAmount })
      .then(res => {
        if (res.code === 0 && res.data) {
          setUsableCoupons(res.data);
          setSelectedCouponId(prev =>
            prev != null && res.data!.some(c => c.id === prev) ? prev : null
          );
        }
      })
      .catch(e => console.warn('获取可用优惠券失败', e));
  }, [originalAmount]);

  useEffect(() => {
    if (!selectedService || !getStoredToken()) {
      setQuote(null);
      return;
    }
    let active = true;
    const timer = setTimeout(async () => {
      setQuoteLoading(true);
      setQuoteError('');
      const payload = buildOrderPayload();
      try {
        const res = await quoteOrder(payload);
        if (!active) return;
        if (res.code === 0 && res.data) {
          setQuote(res.data);
        } else {
          setQuote(null);
          setQuoteError(res.msg || '价格试算失败');
        }
      } catch {
        if (active) {
          setQuote(null);
          setQuoteError('价格试算失败，请检查网络');
        }
      } finally {
        if (active) setQuoteLoading(false);
      }
    }, 250);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [selectedService, gameRounds, dispatchMode, providerId, selectedCouponId, supportContactId]);

  const totalAmount = quote?.amount ?? originalAmount;
  const totalDiscount = quote
    ? quote.boss_discount + quote.promo_discount + quote.coupon_discount
    : 0;

  // 切到平台派单：清除指定陪玩；切到指定陪玩但无人选：引导回选择页
  const handleSelectPlatform = () => {
    setDispatchMode('platform');
    setProviderId(null);
    setSelectedEscort(null);
  };

  const handleSelectAssign = () => {
    if (!providerId) {
      Taro.showModal({
        title: '指定陪玩',
        content: '尚未选择陪玩大神，是否前往选择？',
        confirmText: '去选择',
        success: (res) => {
          if (res.confirm) {
            const url = selectedService
              ? `/pages/players/index?serviceId=${selectedService.id}&game_rounds=${gameRounds}`
              : '/pages/players/index';
            Taro.navigateTo({ url });
          }
        }
      });
      return;
    }
    setDispatchMode('assign');
  };

  const handleReselectEscort = () => {
    const url = selectedService
      ? `/pages/players/index?serviceId=${selectedService.id}&game_rounds=${gameRounds}`
      : '/pages/players/index';
    Taro.navigateTo({ url });
  };

  const handleSubmit = async () => {
    if (submitting) return;

    // 下单前登录守卫：未登录弹出微信快捷登录窗，登录后由用户再次点击提交
    if (!ensureLogin()) return;

    if (!gameAccount.region.trim() || !gameAccount.nickname.trim() || !gameAccount.uid.trim()) {
      Taro.showToast({ title: '请填写游戏大区、昵称和UID', icon: 'none' });
      return;
    }

    if (!selectedService) {
      Taro.showToast({ title: '请选择服务', icon: 'none' });
      return;
    }

    if (!supportContactId) {
      Taro.showToast({ title: '请选择负责订单的客服', icon: 'none' });
      return;
    }

    if (quoteLoading) {
      Taro.showToast({ title: '正在核算订单价格', icon: 'none' });
      return;
    }
    if (!quote) {
      Taro.showToast({ title: quoteError || '请等待价格核算完成', icon: 'none' });
      return;
    }
    if (!quote.balance_sufficient) {
      const result = await Taro.showModal({
        title: '兴安币不足',
        content: `需支付 ${formatXaCoin(quote.amount)} 兴安币，当前余额 ${formatXaCoin(quote.wallet_balance)} 兴安币。`,
        confirmText: '联系企微客服',
      });
      if (result.confirm) {
        const serviceResult = await openWecomCustomerService({
          title: '订单余额不足，需要充值兴安币',
          path: '/pages/checkout/index',
        });
        if (!serviceResult.opened) Taro.navigateTo({ url: '/pages/serviceCard/index?scene=recharge' });
      }
      return;
    }

    const payload = buildOrderPayload({ includeGameAccount: true });

    const confirmation = await Taro.showModal({
      title: '确认提交订单',
      content: `本次将从钱包支付 ${formatXaCoin(quote.amount)} 兴安币，${dispatchMode === 'assign' ? '由指定大神接单' : '提交后进入平台抢单池'}，由${supportContacts.find(item => item.id === supportContactId)?.name || '所选客服'}负责跟进。`,
      confirmText: '确认支付',
      confirmColor: '#007AFF',
    });
    if (!confirmation.confirm) return;

    try {
      setSubmitting(true);
      const response = await createOrder(payload);

      if (response?.code !== 0) {
        Taro.showToast({ title: response?.msg || '下单失败，请稍后重试', icon: 'none' });
        return;
      }

      const createdOrderId = response.data?.id;
      const isAssign = dispatchMode === 'assign' && !!providerId;
      const assignName = selectedEscort?.nickname ? `「${selectedEscort.nickname}」` : '指定大神';
      Taro.showToast({ title: '下单成功', icon: 'success' });
      Taro.showModal({
        title: '订单已提交',
        content: isAssign
          ? `已为你指定陪玩${assignName}，等待对方接单，可在"我的订单"中查看进度。`
          : '订单已进入待接单，将由陪玩师主动接单或客服为你指派，请在"我的订单"中留意状态。',
        showCancel: false,
        success: () => {
          Taro.redirectTo({
            url: createdOrderId
              ? `/pages/orderList/index?orderId=${createdOrderId}`
              : '/pages/orderList/index',
          });
        }
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : '下单失败，请稍后重试';
      Taro.showToast({ title: message, icon: 'none' });
    } finally {
      setSubmitting(false);
    }
  };

  if (services.length === 0) {
    return (
      <View className={styles.container}>
        <Skeleton variant="card-list" rows={4} />
      </View>
    );
  }

  return (
    <View className={styles.container}>
      <ScrollView className={styles.content} scrollY>
        <View className={styles.section}>
          <Text className={styles.sectionTitle}>{guidedFlow ? '已选游玩项目' : '选择服务'}</Text>
          {services.map(service => {
            const isSelected = selectedService?.id === service.id;
            return (
              <View
                key={service.id}
                className={`${styles.serviceModeBox} ${isSelected ? styles.serviceModeBoxActive : ''}`}
                onClick={() => { if (!guidedFlow) setSelectedService(service); }}
              >
                <View className={styles.serviceModeMain}>
                  <Text className={styles.serviceModeName}>{service.name}</Text>
                  <Text className={styles.serviceModeDesc}>{service.description}</Text>
                  <Text className={styles.price}>{formatXaCoin(service.price)}兴安币</Text>
                </View>
                {isSelected && (guidedFlow
                  ? <Text className={styles.lockedSelection}>已确认</Text>
                  : <Icon name="check-circle" size={40} color="#007AFF" className={styles.serviceModeCheck} />)}
              </View>
            );
          })}
        </View>

        <View className={styles.section}>
          <Text className={styles.sectionTitle}>接单方式</Text>
          {(!guidedFlow || dispatchMode === 'assign') && <View
            className={`${styles.dispatchOption} ${dispatchMode === 'assign' ? styles.dispatchOptionActive : ''}`}
            onClick={handleSelectAssign}
          >
            <View className={styles.dispatchMain}>
              <View className={styles.dispatchHead}>
                <Text className={styles.dispatchName}>指定陪玩</Text>
                {dispatchMode === 'assign' && (
                  <View className={styles.dispatchBadge}>
                    <Icon name="check" size={20} color="#FFFFFF" strokeWidth={3} />
                    <Text className={styles.dispatchBadgeText}>已选</Text>
                  </View>
                )}
              </View>
              {selectedEscort ? (
                <Text className={styles.dispatchDesc}>
                  已指定「{selectedEscort.nickname}」·{selectedEscort.rank}，下单后等待其接单
                </Text>
              ) : (
                <Text className={styles.dispatchDesc}>自己挑选心仪大神，由其专属服务</Text>
              )}
            </View>
            {dispatchMode === 'assign' && (
              <View className={styles.dispatchReselect} onClick={handleReselectEscort}>
                <Text className={styles.dispatchReselectText}>重新选择</Text>
                <Icon name="chevron-right" size={28} color="#007AFF" />
              </View>
            )}
          </View>}

          {(!guidedFlow || dispatchMode === 'platform') && <View
            className={`${styles.dispatchOption} ${dispatchMode === 'platform' ? styles.dispatchOptionActive : ''}`}
            onClick={handleSelectPlatform}
          >
            <View className={styles.dispatchMain}>
              <View className={styles.dispatchHead}>
                <Text className={styles.dispatchName}>平台派单（不指定陪玩）</Text>
                {dispatchMode === 'platform' && (
                  <View className={styles.dispatchBadge}>
                    <Icon name="check" size={20} color="#FFFFFF" strokeWidth={3} />
                    <Text className={styles.dispatchBadgeText}>已选</Text>
                  </View>
                )}
              </View>
              <Text className={styles.dispatchDesc}>
                下单后进入待接单，陪玩师主动接单或由客服为你指派
              </Text>
            </View>
          </View>}
        </View>

        <View className={styles.section}>
          <Text className={styles.sectionTitle}>负责订单的客服</Text>
          {supportContacts.filter(contact => !guidedFlow || contact.id === supportContactId).map(contact => {
            const selected = supportContactId === contact.id;
            return (
              <View
                key={contact.id}
                className={`${styles.dispatchOption} ${selected ? styles.dispatchOptionActive : ''}`}
                onClick={() => setSupportContactId(contact.id)}
              >
                <View className={styles.dispatchMain}>
                  <Text className={styles.dispatchName}>{contact.name}</Text>
                  <Text className={styles.dispatchDesc}>{contact.tips || contact.company || '负责订单进度跟进与售后服务'}</Text>
                </View>
                {selected && <Icon name="check-circle" size={38} color="#007AFF" />}
              </View>
            );
          })}
          {supportContacts.length === 0 && (
            <Text className={styles.couponEmpty}>暂无可选客服，请联系平台处理</Text>
          )}
        </View>

        <View className={styles.section}>
          <Text className={styles.sectionTitle}>
            {selectedService?.game_category_name
              ? `${selectedService.game_category_name}账号信息`
              : '游戏账号信息'}
          </Text>
          <View className={styles.formItem}>
            <Text className={styles.formLabel}>游戏大区</Text>
            <Input
              className={styles.formInput}
              value={gameAccount.region}
              placeholder="例如：微信区 / QQ区 / 国服 / 国际服"
              onInput={(event) => setGameAccount(prev => ({ ...prev, region: event.detail.value }))}
            />
          </View>
          <View className={styles.formItem}>
            <Text className={styles.formLabel}>游戏昵称</Text>
            <Input
              className={styles.formInput}
              value={gameAccount.nickname}
              placeholder="请输入老板的游戏昵称"
              onInput={(event) => setGameAccount(prev => ({ ...prev, nickname: event.detail.value }))}
            />
          </View>
          <View className={styles.formItem}>
            <Text className={styles.formLabel}>游戏UID</Text>
            <Input
              className={styles.formInput}
              value={gameAccount.uid}
              placeholder="请输入游戏UID / 数字ID"
              type="text"
              onInput={(event) => setGameAccount(prev => ({ ...prev, uid: event.detail.value }))}
            />
          </View>
        </View>

        <View className={styles.section}>
          <Text className={styles.sectionTitle}>服务数量</Text>
          <View className={styles.stepperRow}>
            <Text className={styles.formLabel}>游戏局数</Text>
            <View className={styles.stepper}>
              <View
                className={`${styles.stepperBtn} ${gameRounds <= 1 ? styles.stepperBtnDisabled : ''}`}
                onClick={() => setGameRounds(gameRounds > 1 ? gameRounds - 1 : 1)}
              >
                <Icon name="minus" size={28} color={gameRounds <= 1 ? '#AEAEB2' : '#1D1D1F'} strokeWidth={2.5} />
              </View>
              <Input
                className={styles.stepperInput}
                value={String(gameRounds)}
                type="number"
                placeholder="1"
                onInput={(event) => {
                  const val = parseInt(event.detail.value, 10);
                  setGameRounds(val > 0 ? val : 1);
                }}
              />
              <View
                className={styles.stepperBtn}
                onClick={() => setGameRounds(gameRounds + 1)}
              >
                <Icon name="plus" size={28} color="#007AFF" strokeWidth={2.5} />
              </View>
            </View>
          </View>
        </View>

        <View className={styles.section}>
          <View className={styles.couponTitleRow}>
            <Icon name="ticket" size={36} color="#FF9500" />
            <Text className={styles.sectionTitle}>优惠券</Text>
          </View>
          {usableCoupons.length === 0 ? (
            <Text className={styles.couponEmpty}>暂无可用优惠券</Text>
          ) : (
            <>
              <View
                className={`${styles.couponOption} ${selectedCouponId === null ? styles.couponOptionActive : ''}`}
                onClick={() => setSelectedCouponId(null)}
              >
                <Text className={styles.couponOptionName}>不使用优惠券</Text>
                {selectedCouponId === null && (
                  <Icon name="check-circle" size={36} color="#007AFF" />
                )}
              </View>
              {usableCoupons.map(coupon => {
                const isSelected = selectedCouponId === coupon.id;
                return (
                  <View
                    key={coupon.id}
                    className={`${styles.couponOption} ${isSelected ? styles.couponOptionActive : ''}`}
                    onClick={() => setSelectedCouponId(coupon.id)}
                  >
                    <View className={styles.couponOptionMain}>
                      <Text className={styles.couponOptionName}>{coupon.name}</Text>
                      <Text className={styles.couponOptionDesc}>
                        {coupon.discount_type === 'DIRECT'
                          ? '无门槛'
                          : `满${formatXaCoin(coupon.threshold)}兴安币可用`}
                      </Text>
                    </View>
                    <Text className={styles.couponOptionAmount}>-{formatXaCoin(coupon.amount)}币</Text>
                    {isSelected && (
                      <Icon name="check-circle" size={36} color="#007AFF" className={styles.couponOptionCheck} />
                    )}
                  </View>
                );
              })}
            </>
          )}
        </View>

        <View className={styles.section}>
          <Text className={styles.sectionTitle}>订单备注</Text>
          <Textarea
            className={styles.remark}
            value={remark}
            maxlength={80}
            placeholder="例如：想冲分、需要教学、指定英雄/地图、是否介意多人车队"
            onInput={(event) => setRemark(event.detail.value)}
          />
        </View>

        <View className={styles.section}>
          <View className={styles.priceSummaryHead}>
            <Text className={styles.sectionTitle}>费用明细</Text>
            <Text className={styles.quoteState}>{quoteLoading ? '核算中…' : '以后端实时价格为准'}</Text>
          </View>
          <View className={styles.priceLine}>
            <Text>商品原价</Text>
            <Text>{formatXaCoin(quote?.original_amount ?? originalAmount)}兴安币</Text>
          </View>
          {!!quote?.boss_discount && (
            <View className={styles.priceLineDiscount}><Text>老板等级优惠</Text><Text>-{formatXaCoin(quote.boss_discount)}币</Text></View>
          )}
          {!!quote?.promo_discount && (
            <View className={styles.priceLineDiscount}><Text>{quote.promotion?.title || '限时活动'}</Text><Text>-{formatXaCoin(quote.promo_discount)}币</Text></View>
          )}
          {!!quote?.coupon_discount && (
            <View className={styles.priceLineDiscount}><Text>优惠券</Text><Text>-{formatXaCoin(quote.coupon_discount)}币</Text></View>
          )}
          {quoteError && <Text className={styles.quoteError}>{quoteError}</Text>}
          {quote && (
            <View className={styles.balanceLine}>
              <Text>钱包余额</Text>
              <Text className={quote.balance_sufficient ? styles.balanceEnough : styles.balanceInsufficient}>
                {formatXaCoin(quote.wallet_balance)}兴安币{quote.balance_sufficient ? '' : '（不足）'}
              </Text>
            </View>
          )}
        </View>
      </ScrollView>

      <View className={styles.footer}>
        <View className={styles.totalBlock}>
          <Text className={styles.totalLabel}>合计</Text>
          <Text className={styles.totalPrice}>{formatXaCoin(totalAmount)}兴安币</Text>
          {totalDiscount > 0 && (
            <Text className={styles.discountText}>已优惠 {formatXaCoin(totalDiscount)}兴安币</Text>
          )}
        </View>
        <View
          className={`${styles.submitBtn} ${submitting || quoteLoading || !!quoteError ? styles.submitBtnDisabled : ''}`}
          onClick={handleSubmit}
        >
          <Text>{submitting ? '提交中...' : quoteLoading ? '核算中...' : '确认支付'}</Text>
        </View>
      </View>
      {loginSheet}
    </View>
  );
};

export default CheckoutPage;
