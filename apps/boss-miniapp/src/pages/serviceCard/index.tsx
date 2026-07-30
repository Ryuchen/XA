import React, { useEffect, useState } from 'react';
import { View, Text, Image, Button } from '@tarojs/components';
import Taro from '@tarojs/taro';
import Icon from '@/components/Icon';
import { fetchContactCard, openWecomCustomerService, SupportContactCard } from '@/services/support';
import { resolveImageUrl } from '@/utils/media';
import styles from './index.module.scss';

const PRIMARY_COLOR = '#007AFF';

const FALLBACK_CARD: SupportContactCard = {
  id: 0,
  name: '游戏陪玩客服-小Q',
  company: '兴安电竞官方陪玩平台',
  wechat_id: 'xingandianjing',
  wecom_corp_id: '',
  wecom_service_url: '',
  avatar_url: 'https://picsum.photos/id/177/200/200',
  qrcode_url: 'https://picsum.photos/id/338/400/400',
  tips: '1. 请长按识别上方二维码，添加客服微信\n2. 发送您要点的陪玩游戏和时段给客服\n3. 客服会为您匹配合适的陪玩大神\n4. 转账后客服会第一时间安排陪玩服务',
};

const ServiceCardPage: React.FC = () => {
  const [card, setCard] = useState<SupportContactCard>(FALLBACK_CARD);
  const [opening, setOpening] = useState(false);

  useEffect(() => {
    fetchContactCard().then(res => {
      if (res.code === 0 && res.data) {
        setCard(res.data);
      }
    });
  }, []);

  const handleCopyWechatId = () => {
    Taro.setClipboardData({
      data: card.wechat_id,
      success: () => {
        Taro.showToast({
          title: '微信号已复制',
          icon: 'success'
        });
      }
    });
  };

  const handleOpenWecom = async () => {
    if (opening) return;
    setOpening(true);
    const result = await openWecomCustomerService({
      title: '我要咨询客服下单',
      path: '/pages/serviceCard/index',
    });
    setOpening(false);
    if (!result.opened) {
      Taro.showToast({
        title: result.reason === 'unsupported' ? '请扫码或复制微信号联系客服' : '企业微信客服暂时无法打开',
        icon: 'none',
        duration: 2500,
      });
    }
  };

  const handleClose = () => {
    Taro.switchTab({
      url: '/pages/home/index'
    });
  };

  const tipsLines = (card.tips || '').split(/\n+/).filter(Boolean);

  return (
    <View className={styles.container}>
      <View className={styles.header}>
        <View className={styles.headerIcon}>
          <Icon name="headphones" size={44} color={PRIMARY_COLOR} />
        </View>
        <Text className={styles.title}>联系客服下单</Text>
        <Text className={styles.subtitle}>一键进入企业微信客服，专属客服为你代下单</Text>
      </View>

      <View className={styles.card}>
        <View className={styles.serviceInfo}>
          <Image
            className={styles.avatar}
            src={resolveImageUrl(card.avatar_url)}
            mode="aspectFill"
          />
          <View className={styles.info}>
            <Text className={styles.name}>{card.name}</Text>
            <Text className={styles.company}>{card.company}</Text>
            <View className={styles.wechatId}>
              <Icon name="message-circle" size={26} color={PRIMARY_COLOR} />
              <Text className={styles.wechatIdText}>微信号: {card.wechat_id}</Text>
            </View>
          </View>
        </View>

        <View className={styles.qrcode}>
          <Text className={styles.qrcodeTitle}>长按识别二维码添加客服</Text>
          <Image
            className={styles.qrcodeImage}
            src={resolveImageUrl(card.qrcode_url)}
            mode="aspectFill"
            showMenuByLongpress
          />
        </View>

        {tipsLines.length > 0 && (
          <View className={styles.tips}>
            <View className={styles.tipsHeader}>
              <Icon name="info" size={28} color={PRIMARY_COLOR} />
              <Text className={styles.tipsTitle}>下单温馨提示</Text>
            </View>
            {tipsLines.map((line, idx) => (
              <View key={idx} className={styles.tipsItem}>
                <Icon
                  name="check-circle"
                  size={28}
                  color={PRIMARY_COLOR}
                  className={styles.tipsIcon}
                />
                <Text className={styles.tipsText}>{line}</Text>
              </View>
            ))}
          </View>
        )}
      </View>

      <View className={styles.actions}>
        <Button className={styles.copyBtn} loading={opening} onClick={handleOpenWecom}>
          <Icon name="message-circle" size={32} color="#FFFFFF" />
          <Text className={styles.btnText}>{opening ? '正在连接' : '打开企业微信客服'}</Text>
        </Button>
        <Button className={styles.addWechatBtn} onClick={handleCopyWechatId}>
          <Text className={styles.btnText}>复制客服微信号</Text>
          <Icon name="credit-card" size={32} color={PRIMARY_COLOR} />
        </Button>
        <Button className={styles.closeBtn} onClick={handleClose}>
          返回首页
        </Button>
      </View>
    </View>
  );
};

export default ServiceCardPage;
