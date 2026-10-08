import React, { useState } from 'react';
import { View, Text, Input, Button, Image } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { accountLogin, goHome, getSessionRevision, OtpLoginStep, OtpLoginData, completeOtpLogin } from '@/utils/auth';
import { wsService } from '@/services/websocket';
import XaLogo from '@/components/XaLogo';
import styles from './index.module.scss';

const LoginPage: React.FC = () => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [otp, setOtp] = useState<OtpLoginData | null>(null);
  const [otpCode, setOtpCode] = useState('');
  const [recovery, setRecovery] = useState<OtpLoginStep | null>(null);
  const enterHome = () => {
    const revision = getSessionRevision();
    wsService.connect();
    Taro.showToast({ title: '登录成功', icon: 'success' });
    const redirect = Taro.getCurrentInstance().router?.params?.redirect;
    setTimeout(() => {
      if (getSessionRevision() !== revision) return;
      if (redirect && redirect.startsWith('/pages/')) Taro.reLaunch({ url: decodeURIComponent(redirect) });
      else goHome();
    }, 500);
  };

  const handleLogin = async () => {
    if (isSubmitting) return;
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
      await accountLogin({ username, password, otpChallenge: otp?.otpChallenge, otpCode });
      Taro.hideLoading();
      enterHome();
    } catch (error) {
      // H5 loading and toast share a host: close loading before showing feedback.
      Taro.hideLoading();
      if (error instanceof OtpLoginStep) {
        if (error.data.requiresOTP) { setOtp(error.data); setOtpCode(''); }
        else { setRecovery(error); setOtp(null); setOtpCode(''); }
        return;
      }
      Taro.showToast({
        title: error instanceof Error ? error.message : '登录失败',
        icon: 'none'
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <View className={`xa-provider-page ${styles.container}`}>
      <View className={`${styles.header} ${otp ? styles.otpHeader : ''}`}>
        <XaLogo size={otp ? 80 : 120} color="#002fa7" style={{ marginBottom: otp ? '0' : '16px' }} />
        <Text className={styles.title}>兴安电竞 · 陪玩端</Text>
        <Text className={styles.subtitle}>报单与提款，尽在掌中</Text>
      </View>

      <View className={styles.formCard}>
        <Text className={styles.formTitle}>陪玩登录</Text>
        {!otp && <Text className={styles.formTip}>审核通过后，使用账号密码与验证器动态码登录。</Text>}

        {recovery ? <View>
          <Text className={styles.formTip}>验证器绑定成功。下面8个备用恢复码仅显示一次，请安全保存；每个恢复码只能使用一次。</Text>
          <Text className={styles.recoveryCodes}>{recovery.data.recoveryCodes?.join('\n')}</Text>
          <Button className={'xa-button-primary ' + styles.loginBtn} onClick={() => { completeOtpLogin(recovery); setRecovery(null); enterHome(); }}>已保存，进入系统</Button>
        </View> : <View>
          {!otp && <>
            <View className={styles.formItem}>
              <Text className={styles.formLabel}>账号</Text>
              <Input
                className={styles.formInput}
                value={username}
                disabled={isSubmitting || !!otp}
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
                disabled={isSubmitting || !!otp}
                password
                placeholder="请输入密码"
                maxlength={128}
                onInput={(event) => setPassword(event.detail.value)}
              />
            </View>

          </>}
          {otp && <View className={styles.otpPanel}>
            <View className={styles.accountSummary}><Text>当前账号</Text><Text className={styles.accountName}>{username}</Text></View>
            {otp.requiresOTPSetup ? <View>
              <Text className={styles.otpTitle}>绑定动态验证码 App</Text>
              <Text className={styles.otpTip}>使用支持 TOTP 的验证器扫码，或手动输入绑定密钥。随后输入 App 中的6位动态码。</Text>
              <Image className={styles.otpQr} src={otp.otpQr || ''} mode='aspectFit' />
              <Text className={styles.otpSecret}>{otp.otpSecret}</Text>
            </View> : <Text className={styles.otpTip}>输入验证器 App 的6位动态码，也可使用一个备用恢复码。</Text>}
            <View className={styles.otpField}>
              <Text className={styles.formLabel}>{otp.requiresOTPSetup ? '动态验证码' : '动态验证码 / 备用恢复码'}</Text>
              <Input className={styles.formInput} value={otpCode} placeholder={otp.requiresOTPSetup ? '请输入6位动态码' : '动态验证码 / 备用恢复码'} maxlength={20} disabled={isSubmitting} onInput={event => setOtpCode(event.detail.value.trim())} />
            </View>
          </View>}
          <View className={styles.loginActions}>
            <Button
              className={`xa-button-primary ${styles.loginBtn}`}
              loading={isSubmitting}
              disabled={isSubmitting || !username.trim() || !password || (!!otp && !otpCode)}
              onClick={handleLogin}
            >
              {otp ? (otp.requiresOTPSetup ? '确认绑定并登录' : '验证并登录') : '下一步'}
            </Button>
            {otp && <Button className={styles.backBtn} disabled={isSubmitting} onClick={() => { setOtp(null); setOtpCode(''); }}>返回账号密码</Button>}
          </View>
        </View>}
        {!recovery && !otp && <View className={styles.registerEntry}><Text>还没有陪玩账号？</Text><Button className={styles.registerLink} disabled={isSubmitting} onClick={() => Taro.navigateTo({ url: '/pages/register/index' })}>申请成为陪玩</Button></View>}
      </View>
    </View>
  );
};

export default LoginPage;
