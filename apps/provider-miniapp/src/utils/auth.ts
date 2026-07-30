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

export const getStoredToken = (): string => {
  try {
    return Taro.getStorageSync(TOKEN_KEY) || '';
  } catch (error) {
    return '';
  }
};

export const setStoredToken = (token: string) => {
  Taro.setStorageSync(TOKEN_KEY, token);
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
  Taro.removeStorageSync(USER_KEY);
  Taro.removeStorageSync(TOKEN_KEY);
  Taro.removeStorageSync(REFRESH_TOKEN_KEY);
};

export const goHome = () => {
  Taro.switchTab({ url: HOME_URL });
};

export interface AccountLoginParams {
  username: string;
  password: string;
}

const buildAndStoreUser = (response: { statusCode: number; data?: LoginResponse }): LoginUser => {
  const result = response.data;
  if (response.statusCode >= 400 || !result || result.code !== 0 || !result.data?.token || !result.data.userInfo) {
    throw new Error(result?.msg || '登录失败');
  }
  return persistLoginData(result.data);
};

/** Persist an account-login or audition-token login response in one place. */
export const persistLoginData = (data: NonNullable<LoginResponse['data']>): LoginUser => {
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

  setStoredToken(data.token);
  if (data.refreshToken) {
    setStoredRefreshToken(data.refreshToken);
  }
  setStoredUser(user);
  return user;
};

export const accountLogin = async (params: AccountLoginParams): Promise<LoginUser> => {
  if (!params.username.trim()) {
    throw new Error('请输入账号');
  }
  if (!params.password) {
    throw new Error('请输入密码');
  }

  const response = await Taro.request<LoginResponse>({
    url: `${BASE_URL}/users/account-login/`,
    method: 'POST',
    data: {
      username: params.username.trim(),
      password: params.password
    },
    header: {
      'Content-Type': 'application/json'
    }
  });

  return buildAndStoreUser(response);
};
