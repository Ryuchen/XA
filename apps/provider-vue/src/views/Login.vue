<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Input } from '@xa/shadcn-vue/input'
import { login, persistLogin, type LoginResult } from '../lib/session'

const router = useRouter(), route = useRoute()
const destination = () => typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/audition?') ? route.query.redirect : '/orders'
const username = ref(''), password = ref(''), code = ref(''), error = ref('')
const step = ref<LoginResult | null>(null), recovery = ref<LoginResult | null>(null), busy = ref(false)

async function submit() {
  if (busy.value || !username.value.trim() || !password.value || (step.value && !code.value.trim())) return
  busy.value = true; error.value = ''
  try {
    const result = await login(username.value, password.value, step.value?.otpChallenge, code.value.trim())
    if (result.requiresOTP) { step.value = result; code.value = ''; return }
    if (result.recoveryCodes?.length) { recovery.value = result; step.value = null; return }
    persistLogin(result); await router.replace(destination())
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '登录失败' }
  finally { busy.value = false }
}
async function finish() {
  if (!recovery.value) return
  persistLogin(recovery.value); recovery.value = null; await router.replace(destination())
}
</script>

<template>
  <main class="page login-page">
    <header class="login-header">
      <div class="brand"><span class="brand-mark" aria-hidden="true">XA</span><span>兴安电竞</span></div>
      <h1>陪玩工作台</h1><p>接单、服务与收益管理</p>
    </header>
    <Card class="login-card">
      <header class="card-heading"><h2>{{ recovery ? '保存备用恢复码' : step ? step.requiresOTPSetup ? '绑定验证器' : '验证身份' : '陪玩登录' }}</h2>
        <p v-if="!step && !recovery">使用账号密码登录，按提示完成身份验证。</p>
      </header>
      <div v-if="recovery" class="form-stack">
        <p class="hint">验证器绑定成功。备用恢复码仅显示一次，请妥善保存。</p>
        <pre class="recovery" aria-label="备用恢复码">{{ recovery.recoveryCodes?.join('\n') }}</pre>
        <Button size="xs" type="button" @click="finish">已保存，进入系统</Button>
      </div>
      <form v-else class="form-stack" @submit.prevent="submit">
        <template v-if="!step">
          <label class="field">账号<Input v-model="username" autocomplete="username" maxlength="32" placeholder="请输入账号" :disabled="busy" /></label>
          <label class="field">密码<Input v-model="password" type="password" autocomplete="current-password" maxlength="128" placeholder="请输入密码" :disabled="busy" /></label>
        </template>
        <template v-else>
          <p class="account-line"><span>当前账号</span><strong>{{ username }}</strong></p>
          <section v-if="step.requiresOTPSetup" class="enrollment" aria-label="验证器绑定信息"><p class="hint">用验证器扫描二维码，或手动输入下方密钥。</p>
            <img v-if="step.otpQr" class="otp-qr" :src="step.otpQr" alt="验证器绑定二维码"><code class="secret">{{ step.otpSecret }}</code>
          </section>
          <label class="field">动态验证码<Input v-model="code" autocomplete="one-time-code" maxlength="20" placeholder="动态码或备用恢复码" :disabled="busy" /></label>
        </template>
        <p v-if="error" role="alert" class="error">{{ error }}</p>
        <div class="form-actions"><Button size="xs" type="submit" :disabled="busy || !username.trim() || !password || (!!step && !code.trim())">{{ busy ? '登录中…' : step ? '验证并登录' : '下一步' }}</Button>
          <Button v-if="step" size="xs" type="button" variant="outline" :disabled="busy" @click="step = null; code = ''">返回账号密码</Button>
          <Button v-else size="xs" variant="outline" type="button" @click="router.push('/register')">申请成为陪玩 / 查询审核</Button>
        </div>
      </form>
    </Card>
  </main>
</template>
<style scoped>
.login-page { display:grid; grid-template-columns:minmax(0,1fr); max-width:448px; gap:16px; padding-top:32px; }
.login-header,.login-card { min-width:0; }
.brand { display:inline-flex; align-items:center; gap:8px; color:var(--muted-foreground); font-size:12px; line-height:16px; font-weight:600; }
.brand-mark { display:grid; width:28px; height:28px; place-items:center; background:linear-gradient(135deg,#1e3a8a,#2563eb); border-radius:8px; color:#fff; font-size:12px; line-height:16px; letter-spacing:-.04em; }
h1,h2,p,pre { margin:0; }
h1 { margin:12px 0 4px; font-size:28px; line-height:36px; font-weight:650; color:var(--foreground); }
h2 { font-size:20px; line-height:28px; font-weight:650; }
.login-header p,.card-heading p,.hint,.account-line { color:var(--muted-foreground); font-size:13px; line-height:20px; }
.login-card { gap:16px; border:1px solid var(--border); border-radius:12px; background:var(--card); padding:16px; box-shadow:none; }
.card-heading { display:grid; gap:4px; }
.form-stack,.form-actions,.enrollment { display:grid; min-width:0; gap:12px; }
.field { min-width:0; gap:4px; color:var(--foreground); font-weight:600; }
.field :deep(input) { min-width:0; min-height:28px; height:28px; box-sizing:border-box; padding:4px 8px; font-weight:400; }
.form-actions { gap:8px; }
.login-page :deep([data-slot='button']) { border:1px solid transparent; font-family:inherit; font-size:12px; font-weight:600; line-height:16px; box-shadow:none; }
.login-page :deep([data-slot='button'][data-variant='outline']) { border-color:var(--border); color:var(--foreground); background:var(--card); }
.login-page :deep([data-slot='button']:focus-visible) { outline:2px solid var(--primary); outline-offset:2px; }
@media (hover:hover) { .login-page :deep([data-slot='button'][data-variant='outline']:hover) { background:var(--accent); } }
.account-line { display:flex; flex-wrap:wrap; gap:4px 8px; }
.account-line strong { min-width:0; overflow-wrap:anywhere; color:var(--foreground); }
.enrollment { gap:8px; }
.otp-qr { display:block; width:160px; height:auto; max-width:100%; background:white; }
.secret,.recovery { display:block; max-width:100%; padding:8px 12px; overflow-wrap:anywhere; white-space:pre-wrap; background:var(--muted); border-radius:8px; font:14px/20px ui-monospace,monospace; color:var(--foreground); }
.error { margin:0; color:var(--destructive); overflow-wrap:anywhere; }
@media (min-width:768px) { .login-page { padding-top:64px; gap:24px; } .login-card { padding:24px; } }
@media (max-width:600px),(pointer:coarse) { .login-page :deep([data-slot='button']),.field :deep(input) { min-height:44px; } .field :deep(input) { font-size:16px; } }
</style>
