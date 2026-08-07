import React, { useState } from 'react';
import { View, Text, Button, Image, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { LoginUser, wechatQuickLogin } from '@/utils/auth';
import { useUserStore } from '@/store';
import styles from './index.module.scss';

const IS_DEV = process.env.NODE_ENV === 'development';
const DEFAULT_AVATAR = 'https://picsum.photos/id/64/200/200';

interface LoginSheetProps {
  visible: boolean;
  onClose: () => void;
  onSuccess?: (user: LoginUser) => void;
}

/** 微信快捷登录弹层：头像/昵称填写能力 + 手机号授权，供各页面的登录守卫复用。
 *
 * getPhoneNumber / chooseAvatar 只能由 Button open-type 用户点击触发，
 * 因此登录窗必须是内含这些按钮的弹层，而非调用 API 弹系统窗。
 */
const LoginSheet: React.FC<LoginSheetProps> = ({ visible, onClose, onSuccess }) => {
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
      // 走 store 写入（内部同步落盘），所有已挂载页面即时同步登录态
      useUserStore.getState().setUser(user);
      Taro.showToast({ title: '登录成功', icon: 'success' });
      onSuccess?.(user);
      onClose();
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

  if (!visible) return null;

  return (
    <View className={styles.mask} onClick={onClose}>
      <View className={styles.sheet} onClick={(e) => e.stopPropagation()}>
        <View className={styles.handle} />
        <Text className={styles.title}>微信快捷登录</Text>
        <Text className={styles.tip}>
          完善头像昵称后授权登录，即可挑选服务、选择陪玩并提交订单。
        </Text>

        <View className={styles.profileRow}>
          <Button
            className={styles.avatarBtn}
            openType="chooseAvatar"
            onChooseAvatar={handleChooseAvatar}
          >
            <Image
              className={styles.avatarImg}
              src={avatarPath || DEFAULT_AVATAR}
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
          微信快捷登录
        </Button>
        {IS_DEV && (
          <Button
            className={styles.devLoginBtn}
            disabled={isSubmitting}
            onClick={handleDevLogin}
          >
            跳过手机号登录（开发调试）
          </Button>
        )}
        <Text className={styles.cancelBtn} onClick={onClose}>暂不登录</Text>
      </View>
    </View>
  );
};

export default LoginSheet;
