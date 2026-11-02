<template>
  <div>
    <p v-if="loading" role="status" class="timeout-hint">正在读取配置…</p>
    <Alert v-if="error" variant="destructive"><AlertDescription>{{ error }}</AlertDescription></Alert>
    <p v-if="notice" role="status" class="timeout-hint">{{ notice }}</p>
    <template v-if="loaded">
      <p class="timeout-note">仅影响新订单；余额支付订单到期未接单将取消并退款。微信支付退款以订单状态为准，部分情况需人工核对。已有订单截止时间不变。</p>
      <Popover><PopoverTrigger as-child><Button variant="link">查看适用规则</Button></PopoverTrigger><PopoverContent class="timeout-popover">已指定陪玩和预约订单按原流程处理；拒单后按订单时限重新计时，缺少快照的历史订单按 30 分钟。短时限可能受通行证可见延迟影响。</PopoverContent></Popover>
      <div class="timeout-form">
        <label class="timeout-field">待接单时限（分钟）
          <Input :key="revision ?? -1" v-model.number="minutes" type="number" min="1" max="1440" step="1" :disabled="!editable || saving || requiresRefresh" />
          <span class="timeout-hint">整数 1–1440 分钟；默认 30 分钟。</span>
        </label>
      </div>
      <div class="timeout-actions">
        <Button variant="outline" :disabled="saving || loading" @click="refresh">刷新配置</Button>
        <Button v-if="editable" :disabled="!canSave" @click="save">{{ saving ? '保存中…' : '保存设置' }}</Button>
      </div>
      <TimeoutHistory v-if="auth.hasPerm('audit:view')" ref="historyRef" />
    </template>
    <Button v-else-if="error && canView" variant="outline" :disabled="loading" @click="load">重新加载</Button>
    <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { Popover, PopoverContent, PopoverTrigger } from '@xa/shadcn-vue/popover'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { onBeforeRouteLeave } from 'vue-router'
import { request } from '@/api/request'
import { useAuthStore } from '@/stores/auth'
import TimeoutHistory from './TimeoutHistory.vue'

const auth = useAuthStore()
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const emit = defineEmits<{ busy: [value: boolean] }>()
const historyRef = ref<InstanceType<typeof TimeoutHistory> | null>(null)
const canView = computed(() => Boolean(auth.token) && auth.hasPerm('order:view'))
const editable = computed(() => canView.value && auth.hasPerm('order:timeout'))
const minutes = ref<number | undefined>()
const savedMinutes = ref<number | undefined>()
const revision = ref<number | null>(null)
const loaded = ref(false), loading = ref(false), saving = ref(false), requiresRefresh = ref(false), saveUnknown = ref(false), error = ref(''), notice = ref('')
const canSave = computed(() => editable.value && loaded.value && !loading.value && !saving.value && !requiresRefresh.value
  && Number.isInteger(minutes.value) && Number(minutes.value) >= 1 && Number(minutes.value) <= 1440
  && minutes.value !== savedMinutes.value && revision.value !== null)
const dirty = computed(() => loaded.value && minutes.value !== savedMinutes.value)
let sequence = 0
let disposed = false
let closeCheck: Promise<boolean> | null = null

function validConfig(value: any): value is { minutes: number; revision: number } {
  return Number.isInteger(value?.minutes) && value.minutes >= 1 && value.minutes <= 1440
    && Number.isSafeInteger(value.revision) && value.revision >= 0
}

function current(id: number, session: number) {
  return !disposed && id === sequence && session === auth.sessionRevision && canView.value
}

async function load() {
  if (!canView.value || saving.value) return
  const id = ++sequence, session = auth.sessionRevision
  loading.value = true; error.value = ''; loaded.value = false
  try {
    const result = await request({ url: '/config/order-timeout/', method: 'get' })
    if (!current(id, session)) return
    if (!validConfig(result.data)) throw new Error('服务端返回的时限配置无效')
    minutes.value = savedMinutes.value = result.data.minutes; revision.value = result.data.revision
    loaded.value = true; requiresRefresh.value = false; saveUnknown.value = false
  } catch (e: any) {
    if (current(id, session)) error.value = e?.response?.data?.msg || e?.msg || e?.message || '配置加载失败，请重试'
  } finally { if (current(id, session)) loading.value = false }
}

async function save() {
  if (!canSave.value) return
  const id = ++sequence, session = auth.sessionRevision, submitted = minutes.value!, oldRevision = revision.value!
  saving.value = true; error.value = ''
  try {
    const result = await request({ url: '/config/order-timeout/', method: 'put', data: { minutes: submitted, revision: oldRevision } })
    if (!current(id, session) || !editable.value) return
    if (!validConfig(result.data) || result.data.minutes !== submitted || result.data.revision <= oldRevision) throw new Error('保存结果无法确认')
    minutes.value = savedMinutes.value = result.data.minutes; revision.value = result.data.revision
    notice.value = '待接单时限已保存，后续新订单生效'
    historyRef.value?.reload()
  } catch (e: any) {
    if (current(id, session) && editable.value) {
      requiresRefresh.value = true
      saveUnknown.value = !(e?.response?.status === 409 || e?.code === 409)
      error.value = !saveUnknown.value
        ? '配置已被其他人修改，请刷新后核对再保存。'
        : '保存结果未确认，请先确认请求状态；刷新仅显示当前快照。'
    }
  } finally { if (current(id, session)) saving.value = false }
}

async function refresh() {
  const session = auth.sessionRevision
  if (minutes.value !== savedMinutes.value) {
    if (!await askConfirm({title:'刷新配置',description:`${saveUnknown.value ? '保存请求可能仍在处理，刷新只显示当前快照，请先确认请求状态。' : ''}刷新会丢弃未保存的修改。`,confirmText:'刷新',cancelText:'保留修改'})) return
  }
  if (!disposed && session === auth.sessionRevision && canView.value) await load()
}

function confirmClose(): Promise<boolean> {
  if (saving.value) { notice.value = '保存进行中，请稍后关闭'; return Promise.resolve(false) }
  if (!dirty.value) return Promise.resolve(true)
  if (closeCheck) return closeCheck
  const session = auth.sessionRevision
  closeCheck = askConfirm({title:'放弃修改',description:'关闭将放弃尚未保存的时限修改。',confirmText:'放弃修改',cancelText:'继续编辑'})
    .then(accepted => accepted && !disposed && session === auth.sessionRevision && !saving.value)
    .finally(() => { closeCheck = null })
  return closeCheck
}
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value || saving.value) { event.preventDefault(); event.returnValue = '' } }
onBeforeRouteLeave(confirmClose)
defineExpose({ confirmClose })

watch(saving, value => emit('busy', value), { flush: 'sync' })
watch([() => auth.sessionRevision, canView, editable], () => {
  ++sequence; loaded.value = false; loading.value = saving.value = false; requiresRefresh.value = saveUnknown.value = false
  minutes.value = savedMinutes.value = undefined; revision.value = null
  error.value = canView.value ? '' : '当前账号无订单查看权限'
  if (canView.value) void load()
}, { flush: 'sync' })
onMounted(() => { window.addEventListener('beforeunload', beforeUnload); void load() })
onUnmounted(() => { disposed = true; ++sequence; window.removeEventListener('beforeunload', beforeUnload); emit('busy', false) })
</script>

<style scoped>
.timeout-note { line-height: 1.6; }
.timeout-form { margin-top: 18px; }
.timeout-field { display:grid; gap:8px; max-width:260px; }
.timeout-hint { color: var(--muted-foreground); }
.timeout-popover { width:280px; background:var(--card); color:var(--foreground); font-size:13px; line-height:1.6; }
.timeout-actions { display: flex; justify-content: flex-end; gap: 12px; margin-top: 20px; }
</style>
