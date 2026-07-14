import React, { useState } from 'react';
import { View, Text, Button, Image, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { goRoleHome, setStoredUser, wechatQuickLogin } from '@/utils/auth';
import Icon from '@/components/Icon';
import XaLogo from '@/components/XaLogo';
import styles from './index.module.scss';

// H5 仅作为本地联调入口；微信小程序仍使用真实微信登录。
const IS_DEV = process.env.NODE_ENV === 'development' || process.env.TARO_ENV === 'h5';

const LoginPage: React.FC = () => {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [avatarPath, setAvatarPath] = useState('');
  const [nickname, setNickname] = useState('');

  const runLogin = async (phoneCode?: string) => {
    setIsSubmitting(true);
    Taro.showLoading({ title: '微信登录中' });

    try {
      const user = await wechatQuickLogin({
        ...(phoneCode ? { phoneCode } : {}),
        ...(nickname.trim() ? { nickname: nickname.trim() } : {}),
        ...(avatarPath ? { avatarPath } : {})
      });
      setStoredUser(user);
      Taro.showToast({ title: '登录成功', icon: 'success' });
      setTimeout(() => goRoleHome(), 500);
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

  // 微信头像填写能力：用户选择头像后拿到本地临时路径，登录时随表单上传
  const handleChooseAvatar = (event) => {
    const path = event?.detail?.avatarUrl;
    if (path) {
      setAvatarPath(path);
    }
  };

  const handleNicknameInput = (event) => {
    setNickname(event?.detail?.value || '');
  };

  const handleGetPhoneNumber = (event) => {
    const phoneCode = event?.detail?.code;
    if (!phoneCode) {
      Taro.showToast({ title: '需授权手机号才能登录', icon: 'none' });
      return;
    }
    runLogin(phoneCode);
  };

  // 仅开发环境：跳过手机号授权，直接用 Taro.login 的 code 登录（后端 mock 模式下可用）
  const handleDevLogin = () => {
    runLogin();
  };

  return (
    <View className={styles.container}>
      <View className={styles.brand}>
        <View className={styles.logoBox}>
          <XaLogo size={96} color="#2E7BD6" />
        </View>
        <Text className={styles.title}>兴安电竞</Text>
        <Text className={styles.subtitle}>专业电竞陪玩平台</Text>
      </View>

      <View className={styles.formCard}>
        <Text className={styles.formTitle}>老板登录</Text>
        <Text className={styles.formTip}>
          完善头像昵称后使用微信快捷登录，登录后即可挑选服务、选择陪玩并提交订单。
        </Text>

        <View className={styles.profileRow}>
          <Button
            className={styles.avatarBtn}
            openType="chooseAvatar"
            onChooseAvatar={handleChooseAvatar}
          >
            <Image
              className={styles.avatarImg}
              src={avatarPath || 'https://picsum.photos/id/64/200/200'}
              mode="aspectFill"
            />
            <Text className={styles.avatarTip}>选择头像</Text>
          </Button>
          <Input
            className={styles.nicknameInput}
            type="nickname"
            placeholder="请输入昵称"
            value={nickname}
            onInput={handleNicknameInput}
          />
        </View>

        <Button
          className={styles.loginBtn}
          loading={isSubmitting}
          disabled={isSubmitting}
          openType="getPhoneNumber"
          onGetPhoneNumber={handleGetPhoneNumber}
        >
          <Icon name="message-circle" size={44} color="#FFFFFF" strokeWidth={2} />
          <Text className={styles.loginBtnText}>微信一键登录</Text>
        </Button>
        <View className={styles.loginHint}>
          <Icon name="shield-check" size={28} color="#34C759" strokeWidth={2} />
          <Text className={styles.loginHintText}>推荐使用微信快速登录</Text>
        </View>
        {IS_DEV && (
          <Button
            className={styles.devLoginBtn}
            disabled={isSubmitting}
            onClick={handleDevLogin}
          >
            跳过手机号登录（开发调试）
          </Button>
        )}
      </View>

      <View className={styles.agreement}>
        <View className={styles.agreementCheck}>
          <Icon name="check" size={24} color="#FFFFFF" strokeWidth={3} />
        </View>
        <Text className={styles.agreementText}>
          我已阅读并同意
          <Text className={styles.agreementLink}>《用户协议》</Text>
          和
          <Text className={styles.agreementLink}>《隐私政策》</Text>
        </Text>
      </View>
    </View>
  );
};

export default LoginPage;
