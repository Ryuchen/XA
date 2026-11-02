<template>
  <div>
    <p v-if="loading" class="traffic-hint" role="status">正在读取配置…</p>
    <Alert v-if="error"><AlertDescription>{{ error }}</AlertDescription></Alert>
    <p v-if="notice" class="traffic-hint" role="status">{{ notice }}</p>
    <template v-if="config">
      <div class="traffic-heading"><strong>当前时段：{{ modes[config.mode] || '待确认' }}</strong><span>北京时间 · 保存后保留当前窗口计数</span></div>
      <Alert v-if="config.runtime_enabled === false"><AlertDescription>当前服务实例未启用流量控制，不会校验限流或自主下单开关；客户端仍可能根据配置限制下单。</AlertDescription></Alert>
      <Alert v-else-if="typeof config.runtime_enabled !== 'boolean'"><AlertDescription>后端流量控制状态未提供，请核对服务配置。</AlertDescription></Alert>
      <div>
        <div class="traffic-switches">
          <label class="traffic-control">峰谷限流<Switch v-model="config.enabled" aria-label="峰谷限流" :disabled="!editable || saving || loading || requiresRefresh" /><span class="traffic-hint">{{ config.enabled ? '配置开启' : '配置关闭' }}</span></label>
          <label class="traffic-control">老板自主下单<Switch v-model="config.self_order_enabled" aria-label="老板自主下单" :disabled="!editable || saving || loading || requiresRefresh" /><span class="traffic-hint">{{ config.self_order_enabled ? '配置允许' : '配置关闭' }}</span></label>
        </div>
        <div class="traffic-times">
          <label>波峰时段<div class="traffic-range"><Input :key="`ps-${inputEpoch}`" v-model.number="config.peak_start" type="number" aria-label="波峰开始小时" min="0" max="23" step="1" :disabled="!editable || saving || loading || requiresRefresh" /><span>至</span><Input :key="`pe-${inputEpoch}`" v-model.number="config.peak_end" type="number" aria-label="波峰结束小时" min="0" max="24" step="1" :disabled="!editable || saving || loading || requiresRefresh" /></div></label>
          <label>波谷时段<div class="traffic-range"><Input :key="`vs-${inputEpoch}`" v-model.number="config.valley_start" type="number" aria-label="波谷开始小时" min="0" max="23" step="1" :disabled="!editable || saving || loading || requiresRefresh" /><span>至</span><Input :key="`ve-${inputEpoch}`" v-model.number="config.valley_end" type="number" aria-label="波谷结束小时" min="0" max="24" step="1" :disabled="!editable || saving || loading || requiresRefresh" /></div></label>
        </div>
        <p class="traffic-hint">按整点设置，支持跨午夜；波峰与波谷不能重叠。</p>
      </div>
      <Alert v-if="validationError && editable"><AlertDescription>{{ validationError }}</AlertDescription></Alert>
      <section class="traffic-section">
        <div class="traffic-section-head"><strong>请求配额</strong><span>按时段与操作设置</span></div>
        <nav class="traffic-tabs" aria-label="时段"><Button v-for="(label, mode) in modes" :key="mode" :variant="activeMode === mode ? 'default' : 'outline'" :aria-pressed="activeMode === mode" @click="activeMode = mode">{{ label }}</Button></nav>
        <div class="traffic-quota-table"><Table><TableHeader><TableRow><TableHead>操作</TableHead><TableHead v-for="field in fields" :key="field.key">{{ field.label }}</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in rows" :key="row.action"><TableCell>{{ actions[row.action] }}</TableCell><TableCell v-for="field in fields" :key="field.key"><Input :key="`${inputEpoch}-${activeMode}-${row.action}-${field.key}`" v-model.number="row.values[field.key]" type="number" min="1" :max="field.max" step="1" :aria-label="`${actions[row.action]} ${field.label}`" :disabled="!editable || saving || loading || requiresRefresh" /></TableCell></TableRow></TableBody></Table></div>
        <div class="traffic-quota-cards">
          <div v-for="row in rows" :key="row.action" class="traffic-quota-card">
            <strong>{{ actions[row.action] }}</strong>
            <div class="traffic-quota-fields"><label v-for="field in fields" :key="field.key"><span>{{ field.label }}</span><Input :key="`${inputEpoch}-${activeMode}-${row.action}-${field.key}`" v-model.number="row.values[field.key]" type="number" :aria-label="`${actions[row.action]} ${field.label}`" min="1" :max="field.max" step="1" :disabled="!editable || saving || loading || requiresRefresh" /></label></div>
          </div>
        </div>
      </section>
      <section class="traffic-section">
        <div class="traffic-section-head"><strong>最近 {{ metricWindow }} 分钟流量</strong><span>刷新后更新</span></div>
        <p v-if="config.runtime_enabled === false || persistedEnabled === false" class="traffic-hint">流量控制关闭时不记录限流请求，计数不能代表全部请求。</p>
        <div class="traffic-usage-cards" role="list" aria-label="最近流量统计"><article v-for="row in usage" :key="row.label" role="listitem"><strong>{{ row.label }}</strong><span>放行 {{ row.admitted }}</span><span>限流 {{ row.limited }}</span></article></div>
        <div class="traffic-usage-table"><Table><TableHeader><TableRow><TableHead>操作</TableHead><TableHead>放行请求</TableHead><TableHead>限流请求</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in usage" :key="row.label"><TableCell>{{ row.label }}</TableCell><TableCell>{{ row.admitted }}</TableCell><TableCell>{{ row.limited }}</TableCell></TableRow></TableBody></Table></div>
        <p class="traffic-hint">客户端上报 {{ metric('network_reports') }} 次 · 平均耗时 {{ networkMetric('average_latency_ms', 'network_reports') }} ms · 网络失败 {{ networkMetric('network_failure_percent', 'network_samples') }}%</p>
        <p class="traffic-hint">采样含服务端处理时间，仅用于诊断；多实例需共用 Redis。</p>
      </section>
      <div class="traffic-actions"><Button variant="outline" :disabled="saving || loading" @click="refresh">刷新</Button><Button v-if="editable" :disabled="!canSave" @click="save">{{ saving ? '保存中…' : '保存配置' }}</Button></div>
    </template>
    <Button v-else-if="error && canView" variant="outline" :disabled="loading" @click="load">重新加载</Button>
    <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Switch } from '@xa/shadcn-vue/switch'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { request } from '@/api/request'
import { useAuthStore } from '@/stores/auth'

const emit = defineEmits<{ busy: [value: boolean] }>()
const auth = useAuthStore()
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const canView = computed(() => Boolean(auth.token) && auth.hasPerm('order:view'))
const editable = computed(() => canView.value && auth.hasPerm('order:traffic'))
const config = ref<any>(null), loading = ref(false), saving = ref(false), error = ref(''), notice = ref(''), requiresRefresh = ref(false), saveUnknown = ref(false)
const inputEpoch = ref(0)
const activeMode = ref('peak')
const modes: Record<string, string> = { peak: '波峰', normal: '常规', valley: '波谷' }
const actions: Record<string, string> = { create: '自主下单', grab: '抢单 / 申请', pool: '抢单池刷新', quote: '报价' }
const fields = [{ key: 'account_per_minute', label: '每账号 / 分钟', max: 10000 }, { key: 'global_per_minute', label: '全站 / 分钟', max: 1000000 }, { key: 'global_per_second', label: '全站 / 秒', max: 100000 }]
const keys = ['revision', 'enabled', 'self_order_enabled', 'peak_start', 'peak_end', 'valley_start', 'valley_end', 'limits']
const payload = () => Object.fromEntries(keys.map(key => [key, config.value?.[key]]))
const saved = ref('')
const persistedEnabled = computed(() => saved.value ? JSON.parse(saved.value).enabled : null)
const dirty = computed(() => !!config.value && JSON.stringify(payload()) !== saved.value)
const rows = computed(() => config.value ? Object.keys(actions).map(action => ({ action, values: config.value.limits[activeMode.value][action] })) : [])
const metric = (key: string) => Number.isFinite(config.value?.metrics?.[key]) ? config.value.metrics[key] : '—'
const networkMetric = (key: string, sample: string) => config.value?.metrics?.[sample] > 0 ? metric(key) : '—'
const metricWindow = computed(() => Number.isFinite(config.value?.metrics?.window_minutes) ? config.value.metrics.window_minutes : '—')
const usage = computed(() => Object.keys(actions).map(action => ({ label: actions[action], admitted: metric(action + '_admitted'), limited: metric(action + '_limited') })))
const hourIn = (hour: number, start: number, end: number) => start < end ? start <= hour && hour < end : hour >= start || hour < end
const validationError = computed(() => {
  if (!config.value) return ''
  const c = config.value
  for (const prefix of ['peak', 'valley']) {
    const start = c[prefix + '_start'], end = c[prefix + '_end']
    if (!Number.isInteger(start) || start < 0 || start > 23 || !Number.isInteger(end) || end < 0 || end > 24) return '时段须使用 0 至 24 的整点，起点不能为 24。'
    if (start === end) return '时段起止不能相同。'
  }
  if (Array.from({ length: 24 }, (_, h) => h).some(h => hourIn(h, c.peak_start, c.peak_end) && hourIn(h, c.valley_start, c.valley_end))) return '波峰与波谷时段不能重叠。'
  for (const mode of Object.keys(modes)) for (const action of Object.keys(actions)) for (const field of fields) {
    const value = c.limits?.[mode]?.[action]?.[field.key]
    if (!Number.isInteger(value) || value < 1 || value > field.max) return `${modes[mode]} · ${actions[action]}：${field.label}须为 1 至 ${field.max} 的整数。`
  }
  return ''
})
const canSave = computed(() => editable.value && !!config.value && dirty.value && !loading.value && !saving.value && !requiresRefresh.value && !validationError.value)
let sequence = 0, disposed = false, closeCheck: Promise<boolean> | null = null
const current = (id: number, session: number) => !disposed && id === sequence && session === auth.sessionRevision && canView.value
function valid(value: any) {
  return value && Number.isSafeInteger(value.revision) && value.revision >= 0 && typeof value.enabled === 'boolean' && typeof value.self_order_enabled === 'boolean'
    && ['peak_start', 'peak_end', 'valley_start', 'valley_end'].every(key => Number.isInteger(value[key]))
    && Object.keys(modes).every(mode => Object.keys(actions).every(action => fields.every(field => Number.isInteger(value.limits?.[mode]?.[action]?.[field.key]))))
}
async function load() {
  if (!canView.value || saving.value) return
  const id = ++sequence, session = auth.sessionRevision
  loading.value = true; error.value = ''
  try {
    const result = await request({ url: '/order-traffic/', silent: true })
    if (!current(id, session)) return
    if (!valid(result.data)) throw new Error('配置格式无效')
    config.value = result.data; saved.value = JSON.stringify(payload()); requiresRefresh.value = saveUnknown.value = false; ++inputEpoch.value
    if (modes[result.data.mode]) activeMode.value = result.data.mode
  } catch {
    if (current(id, session)) { requiresRefresh.value = true; error.value = config.value ? '刷新失败，当前显示上次结果。请重新加载后操作。' : '配置加载失败，请重新加载。' }
  } finally { if (current(id, session)) loading.value = false }
}
async function refresh() {
  if (saving.value || loading.value) return
  const session = auth.sessionRevision
  if (dirty.value) {
    if (!await askConfirm({title:'刷新配置',description:`${saveUnknown.value ? '保存请求可能仍在处理，刷新只显示当前快照，请先确认请求状态。' : ''}刷新会丢弃未保存的修改。`,confirmText:'刷新',cancelText:'保留修改'})) return
  }
  if (session === auth.sessionRevision && canView.value && !disposed) await load()
}
async function save() {
  if (!canSave.value) return
  const id = ++sequence, session = auth.sessionRevision, previous = config.value.revision
  const submitted = JSON.parse(JSON.stringify(payload()))
  saving.value = true; error.value = ''
  let updated = false
  try {
    const result = await request({ url: '/order-traffic/', method: 'put', data: submitted, silent: true })
    if (!current(id, session) || !editable.value) return
    if (!valid(result.data) || result.data.revision <= previous) throw new Error('保存结果无法确认')
    config.value = { ...config.value, ...result.data }; saved.value = JSON.stringify(payload()); ++inputEpoch.value; updated = true
    notice.value = '限流配置已保存'
  } catch (e: any) {
    if (current(id, session) && editable.value) {
      ++inputEpoch.value
      const status = e?.response?.status
      if (status === 409 || e?.code === 409) { requiresRefresh.value = true; saveUnknown.value = false; error.value = '配置已被修改，请刷新后核对。' }
      else if (status === 400) { error.value = e?.response?.data?.detail || e?.response?.data?.non_field_errors?.[0] || '配置未保存，请检查时段和配额。' }
      else { requiresRefresh.value = saveUnknown.value = true; error.value = '保存结果未确认，请先确认请求状态；刷新仅显示当前快照。' }
    }
  } finally {
    if (current(id, session)) { saving.value = false; if (updated) void load() }
  }
}
function confirmClose(): Promise<boolean> {
  if (saving.value || loading.value) { notice.value = '操作进行中，请稍后关闭'; return Promise.resolve(false) }
  if (!dirty.value) return Promise.resolve(true)
  if (closeCheck) return closeCheck
  const session = auth.sessionRevision
  closeCheck = askConfirm({title:'放弃修改',description:'关闭将放弃尚未保存的限流配置。',confirmText:'放弃修改',cancelText:'继续编辑'})
    .then(accepted => accepted && !disposed && session === auth.sessionRevision && !saving.value && !loading.value)
    .finally(() => { closeCheck = null })
  return closeCheck
}
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value || saving.value) { event.preventDefault(); event.returnValue = '' } }
onBeforeRouteLeave(confirmClose)
defineExpose({ confirmClose })
watch(saving, value => emit('busy', value), { flush: 'sync' })
watch([() => auth.sessionRevision, canView, editable], () => {
  ++sequence; config.value = null; saved.value = ''; loading.value = saving.value = requiresRefresh.value = saveUnknown.value = false
  error.value = canView.value ? '' : '当前账号无订单查看权限'
  if (canView.value) void load()
}, { flush: 'sync' })
onMounted(() => { window.addEventListener('beforeunload', beforeUnload); void load() })
onUnmounted(() => { disposed = true; ++sequence; window.removeEventListener('beforeunload', beforeUnload); emit('busy', false) })
</script>
<style scoped>
.traffic-heading, .traffic-section-head { display:flex; align-items:baseline; justify-content:space-between; flex-wrap:wrap; gap:8px; }
.traffic-heading span, .traffic-section-head span, .traffic-hint { color:var(--muted-foreground); font-size:12px; line-height:1.5; }
.traffic-switches, .traffic-times { display:flex; flex-wrap:wrap; gap:12px 32px; margin-top:16px; }
.traffic-hint { margin:8px 0; }
.traffic-switches .traffic-hint { margin-left:10px; }
.traffic-control { display:flex; align-items:center; gap:8px; }
.traffic-times label { display:grid; gap:8px; }
.traffic-range { display:flex; align-items:center; gap:8px; }
.traffic-range input { width:96px; }
.traffic-tabs { display:flex; gap:8px; margin:12px 0; }
.traffic-section { margin-top:24px; padding-top:18px; border-top:1px solid var(--border); }
.traffic-quota-cards { display:none; }
.traffic-usage-cards { display:none; }
.traffic-actions { display:flex; justify-content:flex-end; gap:12px; margin-top:24px; }
@media(max-width:600px) {
  .traffic-times { display:block; }
  .traffic-actions > button { flex:1; }
  .traffic-quota-table { display:none; }
  .traffic-quota-cards { display:grid; gap:12px; }
  .traffic-usage-table { display:none; }
  .traffic-usage-cards { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .traffic-usage-cards article { display:grid; gap:4px; padding:10px; border:1px solid var(--border); border-radius:10px; background:var(--card); font-size:12px; }
  .traffic-quota-card { padding:12px; border:1px solid var(--border); border-radius:10px; background:var(--muted); }
  .traffic-quota-fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin-top:10px; }
  .traffic-quota-fields label { min-width:0; }
  .traffic-quota-fields label span { display:block; margin-bottom:4px; color:var(--muted-foreground); font-size:12px; }
}
</style>
