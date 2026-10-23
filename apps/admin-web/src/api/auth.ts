import { request } from './request'

export interface LoginPayload {
  username: string
  password: string
  otpChallenge?: string
  otpCode?: string
}

export interface Profile {
  id: number
  username: string
  nickname: string
  avatar: string
  is_superuser: boolean
  roles: { id: number; name: string; code: string }[]
  // 兼容旧展示：取首个角色
  role: { id: number; name: string; code: string } | null
  permissions: string[]
}

export interface LoginResult {
  requiresOTP?: boolean
  requiresOTPSetup?: boolean
  otpChallenge?: string
  otpSecret?: string
  otpQr?: string
  recoveryCodes?: string[]
  token?: string
  refresh?: string
  profile?: Profile
}

export interface RefreshResult {
  token: string
}

export const authApi = {
  login: (data: LoginPayload) =>
    request<LoginResult>({ url: '/auth/login', method: 'post', data }),
  profile: () => request<Profile>({ url: '/auth/profile', method: 'get' }),
  refresh: (refresh: string) =>
    request<RefreshResult>({
      url: '/auth/refresh',
      method: 'post',
      data: { refresh },
    }),
}

export class OtpLoginStep extends Error {
  constructor(public data: LoginResult, public revision: number) { super('请完成动态验证码验证') }
}
