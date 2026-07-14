import Taro from '@tarojs/taro';
import { BASE_URL } from './env';

// 老板端仅服务老板角色，保留类型名以兼容既有引用。
export type UserRole = 'customer';

export interface LoginUser {
  id: string;
  role: UserRole;
  nickname: string;
  avatar: string;
  phone?: string;
  openid?: string;
}

interface LoginResponse {
  code: number;
  msg?: string;
  data?: {
    token?: string;
    userInfo?: {
      id?: string;
      username?: string;
      nickname?: string;
      avatar?: string | null;
      role?: string;
      openid?: string | null;
      phone?: string | null;
    };
  };
}

export const roleTextMap: Record<UserRole, string> = {
  customer: '老板'
};

const USER_KEY = 'xa_login_user';
const TOKEN_KEY = 'token';
const HOME_URL = '/pages/home/index';
const DEFAULT_AVATAR = 'https://picsum.photos/id/64/200/200';

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
};

export const goRoleHome = () => {
  Taro.switchTab({ url: HOME_URL });
};

const buildAndStoreUser = (response: { statusCode: number; data?: LoginResponse }): LoginUser => {
  const result = response.data;
  if (response.statusCode >= 400 || !result || result.code !== 0 || !result.data?.token || !result.data.userInfo) {
    throw new Error(result?.msg || '登录失败');
  }
  return persistLoginData(result.data);
};

/** 将登录态数据（token + userInfo）写入本地存储并返回标准化用户。
 *
 * 供微信登录与免登录链接换发 JWT 复用，避免重复构造逻辑。
 */
export const persistLoginData = (data: NonNullable<LoginResponse['data']>): LoginUser => {
  if (!data.token || !data.userInfo) {
    throw new Error('登录态数据不完整');
  }
  const userInfo = data.userInfo;
  const user: LoginUser = {
    id: String(userInfo.id || ''),
    role: 'customer',
    nickname: userInfo.nickname || userInfo.username || '老板用户',
    avatar: userInfo.avatar || DEFAULT_AVATAR,
    phone: userInfo.phone || '',
    openid: userInfo.openid || undefined
  };

  setStoredToken(data.token);
  setStoredUser(user);
  return user;
};

export interface WechatLoginParams {
  phoneCode?: string;
  nickname?: string;
  avatarPath?: string;
}

export const wechatQuickLogin = async (params: WechatLoginParams = {}): Promise<LoginUser> => {
  // 浏览器 H5 没有微信小程序登录上下文，本地联调固定映射测试老板。
  const loginResult = process.env.TARO_ENV === 'h5'
    ? { code: 'manualboss' }
    : await Taro.login();
  if (!loginResult.code) {
    throw new Error('微信登录失败，请稍后重试');
  }

  const formData: Record<string, string> = { code: loginResult.code };
  if (params.phoneCode?.trim()) {
    formData.phoneCode = params.phoneCode.trim();
  }
  if (params.nickname?.trim()) {
    formData.nickname = params.nickname.trim();
  }

  // 选择了头像：走 uploadFile（multipart），头像文件字段名为 avatar，其余字段随 formData 提交
  if (params.avatarPath) {
    const uploadResult = await Taro.uploadFile({
      url: `${BASE_URL}/users/wechat-login/`,
      filePath: params.avatarPath,
      name: 'avatar',
      formData
    });
    let parsed: LoginResponse | undefined;
    try {
      parsed = JSON.parse(uploadResult.data) as LoginResponse;
    } catch (error) {
      throw new Error('登录失败，请稍后重试');
    }
    return buildAndStoreUser({ statusCode: uploadResult.statusCode, data: parsed });
  }

  const response = await Taro.request<LoginResponse>({
    url: `${BASE_URL}/users/wechat-login/`,
    method: 'POST',
    data: formData,
    header: {
      'Content-Type': 'application/json'
    }
  });

  return buildAndStoreUser(response);
};
