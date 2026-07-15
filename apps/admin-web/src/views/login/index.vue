<template>
  <div class="login-page">
    <div class="login-card">
      <div class="brand">
        <h1>兴安电竞</h1>
        <p>后台管理系统</p>
      </div>
      <el-form ref="formRef" :model="form" :rules="rules" @submit.prevent>
        <el-form-item prop="username">
          <el-input
            v-model="form.username"
            size="large"
            placeholder="请输入账号"
            :prefix-icon="User"
          />
        </el-form-item>
        <el-form-item prop="password">
          <el-input
            v-model="form.password"
            type="password"
            size="large"
            placeholder="请输入密码"
            show-password
            :prefix-icon="Lock"
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <el-button
          type="primary"
          size="large"
          style="width: 100%"
          :loading="loading"
          @click="onSubmit"
        >
          登 录
        </el-button>
      </el-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance } from 'element-plus'
import { User, Lock } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const formRef = ref<FormInstance>()
const loading = ref(false)

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
      await auth.login(form.username, form.password)
      ElMessage.success('登录成功')
      const redirect = (route.query.redirect as string) || '/dashboard'
      router.replace(redirect)
    } catch {
      /* 错误已由拦截器提示 */
    } finally {
      loading.value = false
    }
  })
}
</script>

<style scoped lang="scss">
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
  width: min(380px, 100%);
  padding: 40px 36px;
  background: var(--card);
  border-radius: var(--radius);
  box-shadow: var(--shadow-xl);
}

@media (max-width: 480px) {
  .login-card { padding: 32px 22px; }
  .brand { margin-bottom: 22px; }
  .brand h1 { font-size: 23px; }
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
</style>
