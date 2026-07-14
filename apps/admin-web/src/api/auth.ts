import { request } from './request'

export interface LoginPayload {
  username: string
  password: string
}

export interface Profile {
  id: number
  username: string
  nickname: string
  avatar: string
  is_superuser: boolean
  role: { id: number; name: string; code: string } | null
  permissions: string[]
}

export interface LoginResult {
  token: string
  refresh: string
  profile: Profile
}

export const authApi = {
  login: (data: LoginPayload) =>
    request<LoginResult>({ url: '/auth/login', method: 'post', data }),
  profile: () => request<Profile>({ url: '/auth/profile', method: 'get' }),
}
