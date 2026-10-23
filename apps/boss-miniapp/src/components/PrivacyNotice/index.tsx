import React from 'react';
import { Button, Text, View } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';

/** 展示微信平台配置的真实隐私指引，不把阅读入口当作授权记录。 */
const PrivacyNotice: React.FC = () => {
  const openGuide = () => {
    if (process.env.TARO_ENV !== 'weapp' || typeof Taro.openPrivacyContract !== 'function') {
      Taro.showToast({ title: '请在微信小程序中查看', icon: 'none' });
      return;
    }
    const showUnavailable = () => {
      Taro.showToast({ title: '暂时无法打开，请稍后重试', icon: 'none' });
    };
    try {
      Taro.openPrivacyContract({ fail: showUnavailable });
    } catch {
      showUnavailable();
    }
  };

  return (
    <View className={styles.notice}>
      <Text className={styles.text}>微信授权登录使用手机号识别账号，授权前可查看</Text>
      <Button className={styles.link} onClick={openGuide}>《隐私保护指引》</Button>
    </View>
  );
};

export default PrivacyNotice;
