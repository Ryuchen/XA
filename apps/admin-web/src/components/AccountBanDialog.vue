<template>
  <Dialog :open="modelValue" @update:open="value => emit('update:modelValue', value)"><DialogContent class="ban-dialog">
    <DialogHeader><DialogTitle>封禁管理 · {{ targetName || '账户' }}</DialogTitle><DialogDescription>查看账户状态及封禁记录。</DialogDescription></DialogHeader>
    <div class="ban-content">
      <p v-if="loading" role="status">正在加载…</p>
      <Alert v-if="active"><AlertDescription>当前封禁：{{ active.reason }}</AlertDescription></Alert>
      <Alert v-else-if="loaded"><AlertDescription>{{ accountActive && canLogin ? '账户当前可登录' : '账户当前停用，未发现生效封禁记录' }}</AlertDescription></Alert>
      <p v-if="notice" role="status" class="ban-notice">{{ notice }}</p>
      <div v-if="auth.hasPerm('user:ban')" class="ban-form">
        <label class="ban-field">操作原因 <Textarea v-model="reason" :rows="2" maxlength="2000" placeholder="填写封禁或解封原因，记入操作记录" /></label>
        <label class="ban-field">封禁到期（留空为永久）<Input v-model="expiresAt" type="datetime-local" /></label>
        <label v-if="resource === 'escorts' && auth.hasPerm('user:ban_force')" class="ban-force"><Checkbox v-model="force" />即使有在途订单也封禁，相关订单需要客服跟进</label>
      </div>
      <Alert v-if="blockingOrders.length"><AlertTitle>需先处理以下在途订单</AlertTitle><AlertDescription>
        <div v-for="orderNo in blockingOrders" :key="orderNo">{{ orderNo }}</div>
      </AlertDescription></Alert>
      <h3 class="ban-history-title">封禁记录</h3>
      <div class="ban-table"><Table><TableHeader><TableRow><TableHead>状态</TableHead><TableHead>封禁原因</TableHead><TableHead>操作人</TableHead><TableHead>封禁时间</TableHead><TableHead>到期时间</TableHead><TableHead>解封原因</TableHead><TableHead>解封人</TableHead><TableHead>解封时间</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in records" :key="row.id"><TableCell>{{ row.status_display }}</TableCell><TableCell>{{ row.reason }}</TableCell><TableCell>{{ row.operator_name }}</TableCell><TableCell>{{ formatDateTime(row.banned_at) }}</TableCell><TableCell>{{ row.expires_at ? formatDateTime(row.expires_at) : '永久' }}</TableCell><TableCell>{{ row.lift_reason || '—' }}</TableCell><TableCell>{{ row.lifted_by_name || '—' }}</TableCell><TableCell>{{ row.lifted_at ? formatDateTime(row.lifted_at) : '—' }}</TableCell></TableRow></TableBody></Table><p v-if="!loading && !records.length">暂无封禁记录</p></div>
      <div class="ban-pages"><span>共 {{ total }} 条 · 第 {{ page }} / {{ Math.max(1,Math.ceil(total / 10)) }} 页</span><Button variant="outline" :disabled="loading || page <= 1" @click="page--; load()">上一页</Button><Button variant="outline" :disabled="loading || page >= Math.ceil(total / 10)" @click="page++; load()">下一页</Button></div>
    </div>
    <DialogFooter>
      <Button variant="outline" @click="emit('update:modelValue', false)">关闭</Button>
      <template v-if="auth.hasPerm('user:ban')">
        <Button v-if="active" variant="outline" :disabled="loading || saving" @click="submit('unban')">解封账户</Button>
        <Button variant="destructive" :disabled="loading || saving || !loaded" @click="submit('ban')">{{ saving ? '处理中…' : active ? '更新封禁' : '封禁账户' }}</Button>
      </template>
    </DialogFooter>
  </DialogContent></Dialog>
  <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Checkbox } from '@xa/shadcn-vue/checkbox'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { request } from '@/api/request'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/format'

const props = defineProps<{ modelValue: boolean; targetId?: number; resource: 'users' | 'escorts'; targetName?: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; changed: [] }>()
const auth = useAuthStore()
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const records = ref<any[]>([])
const active = ref<any>(null)
const accountActive = ref(false)
const canLogin = ref(false)
const loading = ref(false)
const loaded = ref(false)
const saving = ref(false)
const reason = ref('')
const expiresAt = ref('')
const notice = ref('')
const force = ref(false)
const blockingOrders = ref<string[]>([])
const page = ref(1)
const total = ref(0)
let sequence = 0

async function load() {
  if (!props.modelValue || !props.targetId) return
  const current = ++sequence
  loading.value = true
  try {
    const result = await request({ url: `/${props.resource}/${props.targetId}/ban-history/`, params: { page: page.value } })
    if (current !== sequence || !props.modelValue) return
    records.value = result.data.list
    total.value = result.data.total
    active.value = result.data.active
    accountActive.value = result.data.account_active
    canLogin.value = result.data.can_login
    loaded.value = true
  } finally {
    if (current === sequence) loading.value = false
  }
}

async function submit(action: 'ban' | 'unban') {
  if (saving.value || !props.targetId) return
  if (!reason.value.trim()) { notice.value = '请填写操作原因'; return }
  const targetId = props.targetId
  const resource = props.resource
  if (!await askConfirm({title:action === 'ban' ? '确认封禁' : '确认解封',description:action === 'ban' ? '封禁后该账户将无法登录，请确认原因及订单处理安排。' : '确认解封？系统将恢复封禁前的账户状态。'})) return
  if (!props.modelValue || targetId !== props.targetId || resource !== props.resource) return
  saving.value = true
  blockingOrders.value = []
  try {
    const result = await request({ url: `/${resource}/${targetId}/${action}/`, method: 'post', data: {
      reason: reason.value.trim(), ...(action === 'ban' ? { expires_at: expiresAt.value ? new Date(expiresAt.value).toISOString() : null, force: force.value } : {}),
    } })
    emit('changed')
    if (!props.modelValue || targetId !== props.targetId) return
    const orphaned = result.data?.orphaned_order_count || 0
    notice.value = action === 'ban' ? '封禁已生效' : '已解封'
    if (orphaned) notice.value += `；还有 ${orphaned} 笔在途订单需要跟进：${(result.data.orphaned_order_nos || []).join('、')}`
    reason.value = ''
    force.value = false
    page.value = 1
    await load()
  } catch (error: any) {
    if (targetId === props.targetId) blockingOrders.value = error?.response?.data?.data?.active_order_nos || []
  } finally { saving.value = false }
}

watch(() => [props.modelValue, props.targetId, props.resource], () => {
  ++sequence
  records.value = []
  active.value = null
  loaded.value = false
  loading.value = false
  page.value = 1
  total.value = 0
  reason.value = ''
  expiresAt.value = ''
  force.value = false
  blockingOrders.value = []
  notice.value = ''
  confirmNo()
  if (props.modelValue) void load().catch(() => {})
}, { immediate: true })
</script>

<style scoped>
.ban-dialog { max-width:min(800px, calc(100vw - 24px)); }
.ban-content { max-height:65vh; overflow:auto; }
.ban-form { margin-top: 20px; display:grid; gap:12px; }
.ban-field { display:grid; gap:8px; }
.ban-force { display:flex; gap:8px; align-items:center; }
.ban-history-title { margin: 20px 0 12px; font-size: 15px; }
.ban-table { max-height:280px; overflow:auto; }
.ban-table :deep(table) { min-width:900px; }
.ban-pages { margin-top:12px; display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.ban-notice { color:var(--primary); }
</style>
