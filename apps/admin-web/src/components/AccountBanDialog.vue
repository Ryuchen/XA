<template>
  <el-dialog :model-value="modelValue" :title="`封禁管理 · ${targetName || '账户'}`" width="800px"
    @update:model-value="emit('update:modelValue', $event)" class="ap-modal">
    <div v-loading="loading">
      <el-alert v-if="active" :title="`当前封禁：${active.reason}`" type="warning" :closable="false" />
      <el-alert v-else-if="loaded" :title="accountActive && canLogin ? '账户当前可登录' : '账户当前停用，未发现生效封禁记录'"
        type="info" :closable="false" />
      <el-form v-if="auth.hasPerm('user:ban')"  class="ban-form ap-modal-form ap-form-grid" label-position="top">
        <el-form-item label="操作原因" required class="ap-field--full">
          <el-input v-model="reason" type="textarea" :rows="2" maxlength="2000" show-word-limit placeholder="填写封禁或解封原因，记入操作记录" />
        </el-form-item>
        <el-form-item label="封禁到期">
          <el-date-picker v-model="expiresAt" type="datetime" clearable placeholder="留空为永久封禁" />
        </el-form-item>
        <el-form-item v-if="resource === 'escorts' && auth.hasPerm('user:ban_force')" label="强制处理">
          <el-checkbox v-model="force">即使有在途订单也封禁，相关订单需要客服跟进</el-checkbox>
        </el-form-item>
      </el-form>
      <el-alert v-if="blockingOrders.length" title="需先处理以下在途订单" type="warning" :closable="false">
        <div v-for="orderNo in blockingOrders" :key="orderNo">{{ orderNo }}</div>
      </el-alert>
      <h3 class="ban-history-title">封禁记录</h3>
      <el-table :data="records" max-height="280">
        <el-table-column prop="status_display" label="状态" width="90" />
        <el-table-column prop="reason" label="封禁原因" min-width="160" show-overflow-tooltip />
        <el-table-column prop="operator_name" label="操作人" width="100" />
        <el-table-column label="封禁时间" width="160"><template #default="{ row }">{{ formatDateTime(row.banned_at) }}</template></el-table-column>
        <el-table-column label="到期时间" width="160"><template #default="{ row }">{{ row.expires_at ? formatDateTime(row.expires_at) : '永久' }}</template></el-table-column>
        <el-table-column prop="lift_reason" label="解封原因" min-width="160" show-overflow-tooltip />
        <el-table-column prop="lifted_by_name" label="解封人" width="100" />
        <el-table-column label="解封时间" width="160"><template #default="{ row }">{{ row.lifted_at ? formatDateTime(row.lifted_at) : '—' }}</template></el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" :page-size="10" :total="total" layout="total, prev, pager, next" @current-change="load" />
    </div>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">关闭</el-button>
      <template v-if="auth.hasPerm('user:ban')">
        <el-button v-if="active" type="success" :loading="saving" :disabled="loading" @click="submit('unban')">解封账户</el-button>
        <el-button type="danger" :loading="saving" :disabled="loading || !loaded" @click="submit('ban')">{{ active ? '更新封禁' : '封禁账户' }}</el-button>
      </template>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { request } from '@/api/request'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/format'

const props = defineProps<{ modelValue: boolean; targetId?: number; resource: 'users' | 'escorts'; targetName?: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; changed: [] }>()
const auth = useAuthStore()
const records = ref<any[]>([])
const active = ref<any>(null)
const accountActive = ref(false)
const canLogin = ref(false)
const loading = ref(false)
const loaded = ref(false)
const saving = ref(false)
const reason = ref('')
const expiresAt = ref<Date | null>(null)
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
  if (!reason.value.trim()) { ElMessage.warning('请填写操作原因'); return }
  const targetId = props.targetId
  const resource = props.resource
  try {
    await ElMessageBox.confirm(action === 'ban' ? '封禁后该账户将无法登录，请确认原因及订单处理安排。' : '确认解封？系统将恢复封禁前的账户状态。',
      action === 'ban' ? '确认封禁' : '确认解封', { type: 'warning' })
  } catch { return }
  if (!props.modelValue || targetId !== props.targetId || resource !== props.resource) return
  saving.value = true
  blockingOrders.value = []
  try {
    const result = await request({ url: `/${resource}/${targetId}/${action}/`, method: 'post', data: {
      reason: reason.value.trim(), ...(action === 'ban' ? { expires_at: expiresAt.value?.toISOString() || null, force: force.value } : {}),
    } })
    emit('changed')
    if (!props.modelValue || targetId !== props.targetId) return
    const orphaned = result.data?.orphaned_order_count || 0
    ElMessage.success(action === 'ban' ? '封禁已生效' : '已解封')
    if (orphaned) {
      await ElMessageBox.alert(`还有 ${orphaned} 笔在途订单需要跟进：${(result.data.orphaned_order_nos || []).join('、')}`, '订单跟进提醒')
    }
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
  expiresAt.value = null
  force.value = false
  blockingOrders.value = []
  if (props.modelValue) void load().catch(() => {})
}, { immediate: true })
</script>

<style scoped>
.ban-form { margin-top: 20px; }
.ban-history-title { margin: 20px 0 12px; font-size: 15px; }
.el-pagination { margin-top: 12px; }
</style>
