import React, { useState } from 'react';
import { View, Text, Input, Button } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { accountLogin, goHome, setStoredUser } from '@/utils/auth';
import { wsService } from '@/services/websocket';
import XaLogo from '@/components/XaLogo';
import styles from './index.module.scss';

const LoginPage: React.FC = () => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleLogin = async () => {
    if (!username.trim()) {
      Taro.showToast({ title: '请输入账号', icon: 'none' });
      return;
    }
    if (!password) {
      Taro.showToast({ title: '请输入密码', icon: 'none' });
      return;
    }

    setIsSubmitting(true);
    Taro.showLoading({ title: '登录中' });

    try {
      const user = await accountLogin({ username, password });
      setStoredUser(user);
      wsService.connect();
      Taro.showToast({ title: '登录成功', icon: 'success' });
      const redirect = Taro.getCurrentInstance().router?.params?.redirect;
      setTimeout(() => {
        if (redirect && redirect.startsWith('/pages/')) {
          Taro.reLaunch({ url: decodeURIComponent(redirect) });
        } else {
          goHome();
        }
      }, 500);
    } catch (error) {
      Taro.showToast({
        title: error instanceof Error ? error.message : '登录失败',
        icon: 'none'
      });
    } finally {
      Taro.hideLoading();
      setIsSubmitting(false);
    }
  };

  return (
    <View className={styles.container}>
      <View className={styles.header}>
        <XaLogo size={120} color="#FFFFFF" style={{ marginBottom: '16rpx' }} />
        <Text className={styles.title}>兴安电竞 · 陪玩端</Text>
        <Text className={styles.subtitle}>报单与提款，尽在掌中</Text>
      </View>

      <View className={styles.formCard}>
        <Text className={styles.formTitle}>陪玩登录</Text>
        <Text className={styles.formTip}>请使用客服为你开通的账号密码登录。</Text>

        <View className={styles.formItem}>
          <Text className={styles.formLabel}>账号</Text>
          <Input
            className={styles.formInput}
            value={username}
            placeholder="请输入账号"
            maxlength={32}
            onInput={(event) => setUsername(event.detail.value)}
          />
        </View>
        <View className={styles.formItem}>
          <Text className={styles.formLabel}>密码</Text>
          <Input
            className={styles.formInput}
            value={password}
            password
            placeholder="请输入密码"
            maxlength={32}
            onInput={(event) => setPassword(event.detail.value)}
          />
        </View>

        <Button
          className={styles.loginBtn}
          loading={isSubmitting}
          disabled={isSubmitting || !username.trim() || !password}
          onClick={handleLogin}
        >
          登录
        </Button>
      </View>
    </View>
  );
};

export default LoginPage;
