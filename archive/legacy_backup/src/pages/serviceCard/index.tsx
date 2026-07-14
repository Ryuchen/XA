import React from 'react';
import { View, Text, Image, Button } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';

const ServiceCardPage: React.FC = () => {
  const serviceInfo = {
    name: '游戏陪玩客服-小Q',
    company: '兴安电竞官方陪玩平台',
    wechatId: 'xingandianjing',
    avatar: 'https://picsum.photos/id/177/200/200',
    qrcode: 'https://picsum.photos/id/338/400/400'
  };

  const handleCopyWechatId = () => {
    Taro.setClipboardData({
      data: serviceInfo.wechatId,
      success: () => {
        Taro.showToast({
          title: '微信号已复制',
          icon: 'success'
        });
      }
    });
  };

  const handleAddWechat = () => {
    Taro.showToast({
      title: '请截图保存微信号添加好友',
      icon: 'none',
      duration: 3000
    });
  };

  const handleClose = () => {
    Taro.switchTab({
      url: '/pages/home/index'
    });
  };

  return (
    <View className={styles.container}>
      <View className={styles.header}>
        <Text className={styles.title}>联系客服下单</Text>
        <Text className={styles.subtitle}>扫码添加客服微信，享受专属服务</Text>
      </View>

      <View className={styles.card}>
        <View className={styles.serviceInfo}>
          <Image
            className={styles.avatar}
            src={serviceInfo.avatar}
            mode="aspectFill"
          />
          <View className={styles.info}>
            <Text className={styles.name}>{serviceInfo.name}</Text>
            <Text className={styles.company}>{serviceInfo.company}</Text>
            <Text className={styles.wechatId}>微信号: {serviceInfo.wechatId}</Text>
          </View>
        </View>

        <View className={styles.qrcode}>
          <Text className={styles.qrcodeTitle}>长按识别二维码添加客服</Text>
          <Image
            className={styles.qrcodeImage}
            src={serviceInfo.qrcode}
            mode="aspectFill"
            showMenuByLongpress
          />
        </View>

        <View className={styles.tips}>
          <Text className={styles.tipsTitle}>下单温馨提示</Text>
          <Text className={styles.tipsText}>
            1. 请长按识别上方二维码，添加客服微信{'\n'}
            2. 发送您要点的陪玩游戏和时段给客服{'\n'}
            3. 客服会为您匹配合适的陪玩大神{'\n'}
            4. 转账后客服会第一时间安排陪玩服务
          </Text>
        </View>
      </View>

      <View className={styles.actions}>
        <Button className={styles.copyBtn} onClick={handleCopyWechatId}>
          复制微信号
        </Button>
        <Button className={styles.addWechatBtn} onClick={handleAddWechat}>
          已添加好友，去下单
        </Button>
        <Button className={styles.closeBtn} onClick={handleClose}>
          返回首页
        </Button>
      </View>
    </View>
  );
};

export default ServiceCardPage;
