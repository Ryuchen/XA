<template>
  <main class="login-page">
    <Card class="login-card">
      <header class="brand">
        <Badge variant="secondary">兴安电竞 · 运营后台</Badge>
        <h1>{{ recovery ? '保存备用恢复码' : otp?.requiresOTPSetup ? '绑定验证器' : otp ? '验证身份' : '后台登录' }}</h1>
        <p v-if="!otp && !recovery">使用已开通后台权限的账号登录。</p>
      </header>

      <div v-if="recovery" class="recovery-panel">
        <p>验证器已绑定。以下 {{ recovery.data.recoveryCodes?.length }} 个备用恢复码只显示一次，请安全保存。每个恢复码只能使用一次。</p>
        <ul class="recovery-codes" aria-label="备用恢复码">
          <li v-for="(code,index) in recovery.data.recoveryCodes" :key="index"><code>{{ code }}</code></li>
        </ul>
        <Button size="xs" class="login-submit" @click="finishRecovery">已保存，进入系统</Button>
      </div>
      <form v-else class="login-form" @submit.prevent="onSubmit">
        <div v-show="!otp" class="login-field">
          <label for="login-username">账号</label>
          <Input id="login-username" v-model="form.username" :disabled="loading || !!otp" placeholder="请输入账号" autocomplete="username" :aria-invalid="!!fieldErrors.username" :aria-describedby="fieldErrors.username ? 'username-error' : undefined" />
          <span v-if="fieldErrors.username" id="username-error" class="field-error">{{ fieldErrors.username }}</span>
        </div>
        <div v-show="!otp" class="login-field">
          <label for="login-password">密码</label>
          <Input id="login-password" v-model="form.password" :disabled="loading || !!otp" type="password" placeholder="请输入密码" autocomplete="current-password" :aria-invalid="!!fieldErrors.password" :aria-describedby="fieldErrors.password ? 'password-error' : undefined" />
          <span v-if="fieldErrors.password" id="password-error" class="field-error">{{ fieldErrors.password }}</span>
        </div>

        <div v-if="otp" class="otp-panel">
          <div class="account-summary"><span>当前账号</span><strong>{{ form.username }}</strong></div>
          <template v-if="otp.requiresOTPSetup">
            <p>使用支持 TOTP 的验证器扫描二维码，或手动输入绑定密钥。</p>
            <img :src="otp.otpQr" alt="验证器绑定二维码" class="otp-qr" />
            <div class="manual-secret"><span>手动绑定密钥</span><code class="otp-secret">{{ otp.otpSecret }}</code></div>
          </template>
          <p v-else>输入验证器中的 6 位动态码，也可使用一个备用恢复码。</p>
          <div class="login-field">
            <label for="login-otp">{{ otp.requiresOTPSetup ? '动态验证码' : '动态验证码 / 备用恢复码' }}</label>
            <Input id="login-otp" v-model="otpCode" :placeholder="otp.requiresOTPSetup ? '请输入6位动态码' : '动态验证码 / 备用恢复码'" :inputmode="otp.requiresOTPSetup ? 'numeric' : 'text'" :maxlength="20" :disabled="loading" autocomplete="one-time-code" />
          </div>
        </div>
        <div class="login-actions">
          <Button size="xs" type="submit" class="login-submit" :disabled="loading || (!!otp && !otpCode)">{{ loading ? '登录中…' : otp ? (otp.requiresOTPSetup ? '确认绑定并登录' : '验证并登录') : '下一步' }}</Button>
          <Button v-if="otp" size="xs" variant="outline" :disabled="loading" @click="otp = null; otpCode = ''">返回账号密码</Button>
        </div>
      </form>
    </Card>
  </main>
</template>

<script setup lang="ts">
import { reactive, ref, shallowRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showNotice } from '@/utils/notice'
import { Button } from '@xa/shadcn-vue/button'
import { Badge } from '@xa/shadcn-vue/badge'
import { Card } from '@xa/shadcn-vue/card'
import { Input } from '@xa/shadcn-vue/input'
import { useAuthStore } from '@/stores/auth'
import { OtpLoginStep, type LoginResult } from '@/api/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const loading = ref(false)
const otp = ref<LoginResult | null>(null)
const otpCode = ref('')
const recovery = shallowRef<OtpLoginStep | null>(null)
function enterHome() {
  showNotice('登录成功', 'success')
  const redirect = (route.query.redirect as string) || '/dashboard'
  router.replace(redirect)
}
function finishRecovery() {
  if (!recovery.value) return
  auth.completeOtpLogin(recovery.value)
  recovery.value = null
  enterHome()
}

const form = reactive({ username: '', password: '' })
const fieldErrors = reactive({ username: '', password: '' })

async function onSubmit() {
  if (loading.value) return
  fieldErrors.username = form.username.trim() ? '' : '请输入账号'
  fieldErrors.password = form.password ? '' : '请输入密码'
  if (fieldErrors.username || fieldErrors.password || (otp.value && !otpCode.value.trim())) return
  loading.value = true
  try {
    await auth.login(form.username, form.password, otp.value?.otpChallenge, otpCode.value.trim())
    enterHome()
  } catch (error) {
    if (error instanceof OtpLoginStep) {
      if (error.data.requiresOTP) { otp.value = error.data; otpCode.value = '' }
      else { recovery.value = error; otp.value = null; otpCode.value = '' }
    }
  } finally { loading.value = false }
}
</script>

<style scoped>
:global(body:has(.login-page)) { min-width:0; }
.login-page {
  display:grid; grid-template-columns:minmax(0,1fr); place-items:center; min-height:100vh; min-height:100dvh;
  padding:max(16px,env(safe-area-inset-top)) 12px max(16px,env(safe-area-inset-bottom));
  background:linear-gradient(145deg,var(--background) 36%,var(--secondary));
}
.login-card { width:min(100%,400px); min-width:0; gap:16px; padding:24px; }
.brand,.login-form,.otp-panel,.recovery-panel { display:grid; gap:12px; min-width:0; }
.brand { gap:8px; justify-items:start; padding-bottom:12px; border-bottom:1px solid var(--border); }
.brand h1 { margin:0; color:var(--foreground); font-size:24px; font-weight:600; line-height:1.3; letter-spacing:-.4px; }
.brand p,.otp-panel>p,.recovery-panel>p { margin:0; font-size:13px; line-height:1.55; color:var(--muted-foreground); }
.login-field,.manual-secret { display:grid; gap:4px; min-width:0; }
.login-field label,.manual-secret>span,.account-summary>span { font-size:12px; color:var(--muted-foreground); }
.login-field :deep(input) { width:100%; min-height:28px; height:28px; padding-block:4px; font-size:13px; }
.field-error { color:var(--destructive); font-size:12px; }
.login-actions { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
.account-summary { display:flex; align-items:baseline; justify-content:space-between; flex-wrap:wrap; gap:8px; }
.account-summary strong { font-size:13px; overflow-wrap:anywhere; }
.otp-qr { display:block; width:176px; height:176px; max-width:100%; justify-self:center; background:white; }
.otp-secret,.recovery-codes { min-width:0; overflow-wrap:anywhere; background:var(--secondary); border:1px solid var(--border); border-radius:8px; padding:8px; }
.otp-secret,.recovery-codes code { font-family:var(--font-mono,monospace); font-size:13px; user-select:text; }
.recovery-codes { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; list-style:none; margin:0; padding:12px; }
.recovery-panel>.login-submit { justify-self:start; }
@media(max-width:480px) { .login-card { padding:16px; } }
@media(pointer:coarse) { .login-field :deep(input) { height:44px; min-height:44px; font-size:16px; } .login-card :deep([data-slot='button']) { min-width:44px; min-height:44px; } }
</style>
