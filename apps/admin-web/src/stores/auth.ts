import { defineStore } from 'pinia'
import { authApi, type Profile } from '@/api/auth'

const TOKEN_KEY = 'xa_admin_token'
const REFRESH_KEY = 'xa_admin_refresh'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem(TOKEN_KEY) || '',
    refresh: localStorage.getItem(REFRESH_KEY) || '',
    profile: null as Profile | null,
  }),
  getters: {
    permissions: (state): string[] => state.profile?.permissions || [],
    isSuperuser: (state): boolean => !!state.profile?.is_superuser,
  },
  actions: {
    setToken(token: string, refresh: string) {
      this.token = token
      this.refresh = refresh
      localStorage.setItem(TOKEN_KEY, token)
      localStorage.setItem(REFRESH_KEY, refresh)
    },
    async login(username: string, password: string) {
      const res = await authApi.login({ username, password })
      this.setToken(res.data.token, res.data.refresh)
      this.profile = res.data.profile
      return res.data.profile
    },
    async fetchProfile() {
      const res = await authApi.profile()
      this.profile = res.data
      return res.data
    },
    hasPerm(code?: string | string[]): boolean {
      if (!code) return true
      if (this.isSuperuser) return true
      const perms = this.permissions
      const codes = Array.isArray(code) ? code : [code]
      return codes.some((c) => perms.includes(c))
    },
    clear() {
      this.token = ''
      this.refresh = ''
      this.profile = null
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(REFRESH_KEY)
    },
  },
})
