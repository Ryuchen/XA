import Taro from '@tarojs/taro';
import { BASE_URL } from './env';

// 陪玩端仅服务陪玩角色。
export type UserRole = 'provider';

export interface LoginUser {
  id: string;
  role: UserRole;
  nickname: string;
  username: string;
  avatar: string;
  phone?: string;
}

interface LoginResponse {
  code: number;
  msg?: string;
  data?: {
    requiresOTP?: boolean;
    requiresOTPSetup?: boolean;
    otpChallenge?: string;
    otpSecret?: string;
    otpQr?: string;
    recoveryCodes?: string[];
    token?: string;
    refreshToken?: string;
    userInfo?: {
      id?: string;
      username?: string;
      nickname?: string;
      role?: string;
      openid?: string | null;
      phone?: string | null;
    };
  };
}

const USER_KEY = 'xa_provider_user';
const TOKEN_KEY = 'token';
const REFRESH_TOKEN_KEY = 'xa_provider_refresh_token';
const HOME_URL = '/pages/orders/index';
const DEFAULT_AVATAR = 'https://picsum.photos/id/1005/200/200';
let sessionRevision = 0;
let loginAttempt = 0;

export const getSessionRevision = () => sessionRevision;
export const sessionChangedError = () => Object.assign(new Error('登录状态已变化，请重新操作'), { code: 'SESSION_CHANGED' });

export const getStoredToken = (): string => {
  try {
    return Taro.getStorageSync(TOKEN_KEY) || '';
  } catch (error) {
    return '';
  }
};

export const setStoredToken = (token: string) => {
  Taro.setStorageSync(TOKEN_KEY, token);
  Taro.eventCenter.trigger('xa:auth.changed');
};

export const getStoredRefreshToken = (): string => {
  try {
    return Taro.getStorageSync(REFRESH_TOKEN_KEY) || '';
  } catch (error) {
    return '';
  }
};

export const setStoredRefreshToken = (token: string) => {
  Taro.setStorageSync(REFRESH_TOKEN_KEY, token);
};

export const getStoredUser = (): LoginUser | null => {
  try {
    return Taro.getStorageSync(USER_KEY) || null;
  } catch (error) {
    return null;
  }
};

export const setStoredUser = (user: LoginUser) => {
  Taro.setStorageSync(USER_KEY, user);
};

export const clearStoredUser = () => {
  sessionRevision += 1;
  Taro.removeStorageSync(USER_KEY);
  Taro.removeStorageSync(TOKEN_KEY);
  Taro.removeStorageSync(REFRESH_TOKEN_KEY);
  Taro.eventCenter.trigger('xa:auth.changed');
};

export const goHome = () => {
  Taro.switchTab({ url: HOME_URL });
};

export interface AccountLoginParams {
  otpChallenge?: string;
  otpCode?: string;
  username: string;
  password: string;
}

const buildAndStoreUser = (response: { statusCode: number; data?: LoginResponse }, revision: number): LoginUser => {
  const result = response.data;
  if (response.statusCode >= 400 || !result || result.code !== 0 || !result.data?.token || !result.data.userInfo) {
    throw new Error(result?.msg || '登录失败');
  }
  return persistLoginData(result.data, revision);
};

/** Persist an account-login or audition-token login response in one place. */
export const persistLoginData = (data: NonNullable<LoginResponse['data']>, expectedRevision = sessionRevision): LoginUser => {
  if (expectedRevision !== sessionRevision) throw sessionChangedError();
  if (!data.token || !data.userInfo) {
    throw new Error('登录态数据不完整');
  }
  const userInfo = data.userInfo;
  const user: LoginUser = {
    id: String(userInfo.id || ''),
    role: 'provider',
    nickname: userInfo.nickname || userInfo.username || '陪玩用户',
    username: userInfo.username || '',
    avatar: DEFAULT_AVATAR,
    phone: userInfo.phone || ''
  };

  // Publish once, after all fields belong to the same account. A login without
  // a refresh token must never inherit the preceding account's credential.
  Taro.setStorageSync(TOKEN_KEY, data.token);
  if (data.refreshToken) setStoredRefreshToken(data.refreshToken);
  else Taro.removeStorageSync(REFRESH_TOKEN_KEY);
  setStoredUser(user);
  sessionRevision += 1;
  Taro.eventCenter.trigger('xa:auth.changed');
  return user;
};

export const accountLogin = async (params: AccountLoginParams): Promise<LoginUser> => {
  if (!params.username.trim()) {
    throw new Error('请输入账号');
  }
  if (!params.password) {
    throw new Error('请输入密码');
  }

  const revision = sessionRevision;
  const attempt = ++loginAttempt;
  const response = await Taro.request<LoginResponse>({
    url: `${BASE_URL}/users/account-login/`,
    method: 'POST',
    data: {
      username: params.username.trim(),
      password: params.password,
      otpChallenge: params.otpChallenge,
      otpCode: params.otpCode
    },
    header: {
      'Content-Type': 'application/json'
    }
  });

  if (revision !== sessionRevision || attempt !== loginAttempt) throw sessionChangedError();
  const data = response.data.data;
  if (response.data.code === 0 && data && (data.requiresOTP || data.recoveryCodes?.length)) {
    throw new OtpLoginStep(data, revision);
  }
  return buildAndStoreUser(response, revision);
};

export type OtpLoginData = NonNullable<LoginResponse['data']>;
export class OtpLoginStep extends Error {
  constructor(public data: OtpLoginData, public revision: number) { super('请完成动态验证码验证'); }
}
export const completeOtpLogin = (step: OtpLoginStep) => persistLoginData(step.data, step.revision);
