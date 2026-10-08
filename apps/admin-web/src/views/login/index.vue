<template>
  <div class="login-page">
    <div class="login-card">
      <div class="brand">
        <h1>兴安电竞</h1>
        <p>后台管理系统</p>
      </div>
      <div v-if="recovery">
        <p>验证器绑定成功。以下8个备用恢复码仅显示一次，请安全保存；每个恢复码只能使用一次。</p>
        <pre class="recovery-codes">{{ recovery.data.recoveryCodes?.join('\n') }}</pre>
        <el-button type="primary" class="login-submit" @click="finishRecovery">已保存，进入系统</el-button>
      </div>
      <el-form v-else ref="formRef" class="login-form" :model="form" :rules="rules" label-position="top" @submit.prevent>
        <el-form-item v-show="!otp" label="账号" prop="username">
          <el-input
            v-model="form.username"
            :disabled="loading || !!otp"
            placeholder="请输入账号"
            :prefix-icon="User"
          />
        </el-form-item>
        <el-form-item v-show="!otp" label="密码" prop="password">
          <el-input
            v-model="form.password"
            :disabled="loading || !!otp"
            type="password"
            placeholder="请输入密码"
            show-password
            :prefix-icon="Lock"
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <div v-if="otp" class="otp-panel">
          <div class="account-summary"><span>当前账号</span><strong>{{ form.username }}</strong></div>
          <template v-if="otp.requiresOTPSetup">
            <h3>绑定动态验证码 App</h3>
            <p>使用支持 TOTP 的验证器扫码，或手动输入绑定密钥，再输入 App 的6位动态码。</p>
            <img :src="otp.otpQr" alt="验证器绑定二维码" class="otp-qr" />
            <code class="otp-secret">{{ otp.otpSecret }}</code>
          </template>
          <p v-else>输入验证器 App 的6位动态码，也可使用一个备用恢复码。</p>
          <div class="otp-field">
            <label for="login-otp">{{ otp.requiresOTPSetup ? '动态验证码' : '动态验证码 / 备用恢复码' }}</label>
            <el-input id="login-otp" v-model="otpCode" :placeholder="otp.requiresOTPSetup ? '请输入6位动态码' : '动态验证码 / 备用恢复码'" :maxlength="20" :disabled="loading" autocomplete="one-time-code" @keyup.enter="onSubmit" />
          </div>
        </div>
        <div class="login-actions">
          <el-button
            type="primary"
            class="login-submit"
            :loading="loading"
            :disabled="loading || (!!otp && !otpCode)"
            @click="onSubmit"
          >
            {{ otp ? (otp.requiresOTPSetup ? '确认绑定并登录' : '验证并登录') : '下一步' }}
          </el-button>
          <el-button v-if="otp" class="login-back" :disabled="loading" @click="otp = null; otpCode = ''">返回账号密码</el-button>
        </div>
      </el-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, shallowRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance } from 'element-plus'
import { User, Lock } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { OtpLoginStep, type LoginResult } from '@/api/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const formRef = ref<FormInstance>()
const loading = ref(false)
const otp = ref<LoginResult | null>(null)
const otpCode = ref('')
const recovery = shallowRef<OtpLoginStep | null>(null)
function enterHome() {
  ElMessage.success('登录成功')
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
const rules = {
  username: [{ required: true, message: '请输入账号', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function onSubmit() {
  await formRef.value?.validate(async (valid) => {
    if (!valid) return
    loading.value = true
    try {
      await auth.login(form.username, form.password, otp.value?.otpChallenge, otpCode.value.trim())
      enterHome()
    } catch (error) {
      if (error instanceof OtpLoginStep) {
        if (error.data.requiresOTP) { otp.value = error.data; otpCode.value = '' }
        else { recovery.value = error; otp.value = null; otpCode.value = '' }
      }
    } finally {
      loading.value = false
    }
  })
}
</script>

<style scoped lang="scss">
:global(body:has(.login-page)) { min-width: 0; }
.login-page {
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: max(20px, env(safe-area-inset-top)) 16px max(20px, env(safe-area-inset-bottom));
  background: linear-gradient(135deg, var(--brand-500) 0%, var(--brand-400) 100%);
}
.login-card {
  --ap-control-height: 44px;
  --ap-control-font-size: 14px;
  width: min(380px, 100%);
  padding: 40px 36px;
  background: var(--card);
  border-radius: var(--radius);
  box-shadow: var(--shadow-xl);
}

.brand {
  text-align: center;
  margin-bottom: 28px;
}
.brand h1 {
  margin: 0;
  font-size: 26px;
  color: var(--primary);
}
.brand p {
  margin: 6px 0 0;
  color: var(--muted-foreground);
  font-size: 14px;
}
.login-card :deep(.el-input), .login-card :deep(.el-button) { --ap-control-height: 44px; --ap-control-font-size: 14px; }
.login-form :deep(.el-form-item__label) { height: auto; min-height: 0; margin-bottom: 8px; padding: 0; line-height: 20px; }
.login-actions { display: grid; gap: 12px; margin-top: 20px; }
.login-submit, .login-actions .el-button { width: 100%; margin: 0; white-space: nowrap; }
.login-back { color: var(--brand-600); border-color: var(--border); background: var(--card); }
.login-submit { --el-button-bg-color: var(--brand-600); --el-button-border-color: var(--brand-600); --el-button-hover-bg-color: var(--brand-700); --el-button-hover-border-color: var(--brand-700); --el-button-active-bg-color: var(--brand-700); --el-button-active-border-color: var(--brand-700); }
.otp-panel { margin: 0; }
.account-summary { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 8px 12px; margin-bottom: 20px; padding-bottom: 12px; border-bottom: 1px solid var(--border); font-size: 13px; }
.account-summary span { color: var(--muted-foreground); }
.account-summary strong { min-width: 0; overflow-wrap: anywhere; font-weight: 600; }
.otp-panel h3 { margin: 0; font-size: 18px; line-height: 1.5; }
.otp-panel p { margin: 8px 0 16px; color: var(--muted-foreground); font-size: 13px; line-height: 1.6; }
.otp-qr { display: block; width: 176px; height: 176px; max-width: 100%; margin: 16px auto; background: white; }
.otp-secret, .recovery-codes { display: block; margin: 16px 0; padding: 12px; border: 1px solid var(--border); border-radius: var(--control-radius); background: var(--muted); font-family: monospace; font-size: 14px; line-height: 1.8; overflow-wrap: anywhere; word-break: break-all; white-space: pre-wrap; user-select: text; }
.otp-field { margin-top: 20px; }
.otp-field label { display: block; margin-bottom: 8px; font-size: 14px; line-height: 20px; }
@media (max-width: 480px) {
  .login-card { padding: 32px 22px; }
  .brand { margin-bottom: 22px; }
  .brand h1 { font-size: 23px; }
}
</style>
