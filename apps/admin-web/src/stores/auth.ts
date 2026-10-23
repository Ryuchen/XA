import { defineStore } from 'pinia'
import { authApi, OtpLoginStep, type LoginResult, type Profile } from '@/api/auth'

const TOKEN_KEY = 'xa_admin_token'
const REFRESH_KEY = 'xa_admin_refresh'
export const sessionChangedError = () => Object.assign(new Error('登录状态已变化，请重新操作'), { code: 'SESSION_CHANGED' })

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem(TOKEN_KEY) || '',
    refresh: localStorage.getItem(REFRESH_KEY) || '',
    profile: null as Profile | null,
    sessionRevision: 0,
    loginAttempt: 0,
  }),
  getters: {
    permissions: (state): string[] => state.profile?.permissions || [],
    isSuperuser: (state): boolean => !!state.profile?.is_superuser,
  },
  actions: {
    setToken(token: string, refresh: string) {
      this.sessionRevision += 1
      this.profile = null
      this.token = token
      this.refresh = refresh
      localStorage.setItem(TOKEN_KEY, token)
      localStorage.setItem(REFRESH_KEY, refresh)
    },
    setAccessToken(token: string) {
      this.token = token
      localStorage.setItem(TOKEN_KEY, token)
    },
    async login(username: string, password: string, otpChallenge?: string, otpCode?: string) {
      const revision = this.sessionRevision
      const attempt = ++this.loginAttempt
      const res = await authApi.login({ username, password, otpChallenge, otpCode })
      if (this.sessionRevision !== revision || this.loginAttempt !== attempt) throw sessionChangedError()
      if (res.data.requiresOTP || res.data.recoveryCodes?.length) throw new OtpLoginStep(res.data, revision)
      if (!res.data.token || !res.data.refresh || !res.data.profile) throw new Error('登录验证未完成')
      this.setToken(res.data.token, res.data.refresh)
      this.profile = res.data.profile
      return res.data.profile
    },
    completeOtpLogin(step: OtpLoginStep) {
      if (this.sessionRevision !== step.revision) throw sessionChangedError()
      const data: LoginResult = step.data
      if (!data.token || !data.refresh || !data.profile) throw new Error('登录验证未完成')
      this.setToken(data.token, data.refresh)
      this.profile = data.profile
    },
    async fetchProfile() {
      const revision = this.sessionRevision
      const res = await authApi.profile()
      if (this.sessionRevision !== revision) throw sessionChangedError()
      this.profile = res.data
      return res.data
    },
    async refreshAccessToken() {
      const revision = this.sessionRevision
      if (!this.refresh) {
        throw new Error('Missing refresh token')
      }
      const res = await authApi.refresh(this.refresh)
      if (this.sessionRevision !== revision) throw sessionChangedError()
      if (!res.data?.token) throw new Error('登录凭证刷新失败，请稍后重试')
      this.setAccessToken(res.data.token)
      return res.data.token
    },
    hasPerm(code?: string | string[]): boolean {
      if (!code) return true
      const perms = this.permissions
      const codes = Array.isArray(code) ? code : [code]
      return codes.some((c) => perms.includes(c))
    },
    clear() {
      this.sessionRevision += 1
      this.token = ''
      this.refresh = ''
      this.profile = null
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(REFRESH_KEY)
    },
  },
})
