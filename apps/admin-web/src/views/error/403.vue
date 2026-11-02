<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { Badge } from '@xa/shadcn-vue/badge'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { buildNavigation, flattenNavigation } from '@/router/navigation'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const destination = computed(() => {
  const root = router.getRoutes().find(route => route.path === '/')
  return flattenNavigation(buildNavigation(root?.children || [], code => auth.hasPerm(code)))[0]
})
const loading = ref(false)
const feedback = ref('')
const failed = ref(false)

async function refreshPermissions() {
  if (loading.value) return
  loading.value = true
  feedback.value = ''
  failed.value = false
  const revision = auth.sessionRevision
  try {
    await auth.fetchProfile()
    feedback.value = destination.value ? '权限已刷新。' : '权限已刷新，当前仍未分配后台菜单。'
  } catch {
    if (revision === auth.sessionRevision) {
      failed.value = true
      feedback.value = '暂时无法读取权限，请重试。'
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="denied">
    <Card class="denied-card">
      <div class="denied-heading">
        <Badge variant="secondary">403 · 访问受限</Badge>
        <h1>无法访问此页面</h1>
        <p>{{ destination ? '当前账号没有此页面的查看权限。你可以前往已开放的功能，或联系管理员开通所需权限。' : '当前账号尚未分配可用的后台菜单。请联系管理员分配权限，再点击下方刷新。' }}</p>
      </div>
      <div class="denied-actions">
        <Button v-if="destination" size="xs" as-child><RouterLink :to="'/' + destination.path" replace>前往{{ destination.title }}</RouterLink></Button>
        <Button size="xs" :variant="destination ? 'outline' : 'default'" :disabled="loading" @click="refreshPermissions">{{ loading ? '正在刷新…' : '刷新权限' }}</Button>
      </div>
      <Alert v-if="feedback" :variant="failed ? 'destructive' : 'default'" :role="failed ? 'alert' : 'status'"><AlertDescription>{{ feedback }}</AlertDescription></Alert>
    </Card>
  </main>
</template>

<style scoped>
.denied { display:grid; justify-items:center; align-content:start; padding:32px 0; }
.denied-card { width:min(100%,480px); min-width:0; padding:20px; gap:16px; }
.denied-heading { display:grid; justify-items:start; gap:8px; }
.denied h1 { margin:0; font-size:24px; line-height:1.3; letter-spacing:-.4px; font-weight:600; }
.denied-heading p { margin:0; color:var(--muted-foreground); font-size:13px; line-height:1.55; overflow-wrap:anywhere; }
.denied-actions :deep(a[data-slot='button']) { color:var(--primary-foreground); text-decoration:none; }
.denied-actions { display:flex; flex-wrap:wrap; gap:8px; border-top:1px solid var(--border); padding-top:12px; }
@media(max-width:480px) { .denied { padding-top:8px; } .denied-card { padding:16px; } }
@media(pointer:coarse) { .denied :deep([data-slot='button']) { min-height:44px; min-width:44px; } }
</style>
