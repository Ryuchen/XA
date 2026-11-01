<template>
  <div v-loading="loading">
    <el-alert v-if="error" :title="error" type="warning" :closable="false" show-icon />
    <template v-if="config">
      <div class="traffic-heading"><strong>当前时段：{{ modes[config.mode] || '待确认' }}</strong><span>北京时间 · 保存后保留当前窗口计数</span></div>
      <el-alert v-if="config.runtime_enabled === false" title="当前服务实例未启用流量控制，不会校验限流或自主下单开关；客户端仍可能根据配置限制下单。" type="warning" :closable="false" show-icon />
      <el-alert v-else-if="typeof config.runtime_enabled !== 'boolean'" title="后端流量控制状态未提供，请核对服务配置。" type="info" :closable="false" show-icon />
      <el-form label-position="top" :disabled="!editable || saving || loading || requiresRefresh">
        <div class="traffic-switches">
          <el-form-item label="峰谷限流"><el-switch v-model="config.enabled" /><span class="traffic-hint">{{ config.enabled ? '配置开启' : '配置关闭' }}</span></el-form-item>
          <el-form-item label="老板自主下单"><el-switch v-model="config.self_order_enabled" /><span class="traffic-hint">{{ config.self_order_enabled ? '配置允许' : '配置关闭' }}</span></el-form-item>
        </div>
        <div class="traffic-times">
          <el-form-item label="波峰时段"><el-input-number :key="`ps-${inputEpoch}`" v-model="config.peak_start" aria-label="波峰开始小时" :min="0" :max="23" :precision="0" controls-position="right" /> <span>至</span> <el-input-number :key="`pe-${inputEpoch}`" v-model="config.peak_end" aria-label="波峰结束小时" :min="0" :max="24" :precision="0" controls-position="right" /></el-form-item>
          <el-form-item label="波谷时段"><el-input-number :key="`vs-${inputEpoch}`" v-model="config.valley_start" aria-label="波谷开始小时" :min="0" :max="23" :precision="0" controls-position="right" /> <span>至</span> <el-input-number :key="`ve-${inputEpoch}`" v-model="config.valley_end" aria-label="波谷结束小时" :min="0" :max="24" :precision="0" controls-position="right" /></el-form-item>
        </div>
        <p class="traffic-hint">按整点设置，支持跨午夜；波峰与波谷不能重叠。</p>
      </el-form>
      <el-alert v-if="validationError && editable" :title="validationError" type="warning" :closable="false" show-icon />
      <section class="traffic-section">
        <div class="traffic-section-head"><strong>请求配额</strong><span>按时段与操作设置</span></div>
        <el-tabs v-model="activeMode">
          <el-tab-pane v-for="(label, mode) in modes" :key="mode" :label="label" :name="mode" />
        </el-tabs>
        <el-table :key="`${inputEpoch}-${activeMode}`" class="traffic-quota-table" :data="rows" border>
          <el-table-column label="操作" min-width="110"><template #default="{ row }">{{ actions[row.action] }}</template></el-table-column>
          <el-table-column v-for="field in fields" :key="field.key" :label="field.label" min-width="150">
            <template #default="{ row }"><el-input-number v-model="row.values[field.key]" :min="1" :max="field.max" :precision="0" controls-position="right" :disabled="!editable || saving || loading || requiresRefresh" /></template>
          </el-table-column>
        </el-table>
        <div class="traffic-quota-cards">
          <div v-for="row in rows" :key="row.action" class="traffic-quota-card">
            <strong>{{ actions[row.action] }}</strong>
            <div class="traffic-quota-fields"><label v-for="field in fields" :key="field.key"><span>{{ field.label }}</span><el-input-number :key="`${inputEpoch}-${activeMode}-${row.action}-${field.key}`" v-model="row.values[field.key]" :aria-label="`${actions[row.action]} ${field.label}`" :min="1" :max="field.max" :precision="0" controls-position="right" :disabled="!editable || saving || loading || requiresRefresh" /></label></div>
          </div>
        </div>
      </section>
      <section class="traffic-section">
        <div class="traffic-section-head"><strong>最近 {{ metricWindow }} 分钟流量</strong><span>刷新后更新</span></div>
        <p v-if="config.runtime_enabled === false || persistedEnabled === false" class="traffic-hint">流量控制关闭时不记录限流请求，计数不能代表全部请求。</p>
        <div class="traffic-usage-cards" role="list" aria-label="最近流量统计"><article v-for="row in usage" :key="row.label" role="listitem"><strong>{{ row.label }}</strong><span>放行 {{ row.admitted }}</span><span>限流 {{ row.limited }}</span></article></div>
        <el-table class="traffic-usage-table" :data="usage" border>
          <el-table-column prop="label" label="操作" /><el-table-column prop="admitted" label="放行请求" /><el-table-column prop="limited" label="限流请求" />
        </el-table>
        <p class="traffic-hint">客户端上报 {{ metric('network_reports') }} 次 · 平均耗时 {{ networkMetric('average_latency_ms', 'network_reports') }} ms · 网络失败 {{ networkMetric('network_failure_percent', 'network_samples') }}%</p>
        <p class="traffic-hint">采样含服务端处理时间，仅用于诊断；多实例需共用 Redis。</p>
      </section>
      <div class="traffic-actions"><el-button :disabled="saving || loading" @click="refresh">刷新</el-button><el-button v-if="editable" type="primary" :loading="saving" :disabled="!canSave" @click="save">保存配置</el-button></div>
    </template>
    <el-button v-else-if="error && canView" :disabled="loading" @click="load">重新加载</el-button>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { request } from '@/api/request'
import { useAuthStore } from '@/stores/auth'

const emit = defineEmits<{ busy: [value: boolean] }>()
const auth = useAuthStore()
const canView = computed(() => Boolean(auth.token) && auth.hasPerm('order:view'))
const editable = computed(() => canView.value && auth.hasPerm('order:traffic'))
const config = ref<any>(null), loading = ref(false), saving = ref(false), error = ref(''), requiresRefresh = ref(false), saveUnknown = ref(false)
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
    try { await ElMessageBox.confirm(`${saveUnknown.value ? '保存请求可能仍在处理，刷新只显示当前快照，请先确认请求状态。' : ''}刷新会丢弃未保存的修改。`, '刷新配置', { confirmButtonText: '刷新', cancelButtonText: '保留修改', type: 'warning' }) }
    catch { return }
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
    ElMessage.success('限流配置已保存')
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
  if (saving.value || loading.value) { ElMessage.warning('操作进行中，请稍后关闭'); return Promise.resolve(false) }
  if (!dirty.value) return Promise.resolve(true)
  if (closeCheck) return closeCheck
  const session = auth.sessionRevision
  closeCheck = ElMessageBox.confirm('关闭将放弃尚未保存的限流配置。', '放弃修改', { confirmButtonText: '放弃修改', cancelButtonText: '继续编辑', type: 'warning' })
    .then(() => !disposed && session === auth.sessionRevision && !saving.value && !loading.value)
    .catch(() => false).finally(() => { closeCheck = null })
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
.traffic-times :deep(.el-form-item__content) { gap:8px; }
.traffic-times :deep(.el-input-number) { width:112px; }
.traffic-section { margin-top:24px; padding-top:18px; border-top:1px solid var(--border); }
.traffic-section :deep(.el-input-number) { width:100%; }
.traffic-quota-cards { display:none; }
.traffic-usage-cards { display:none; }
.traffic-actions { display:flex; justify-content:flex-end; gap:12px; margin-top:24px; }
@media(max-width:600px) {
  .traffic-times { display:block; }
  .traffic-actions > .el-button { flex:1; }
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
