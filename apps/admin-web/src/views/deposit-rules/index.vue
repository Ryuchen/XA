<template>
  <div class="rules-page">
    <p class="ap-page-intro">设置单抵陪玩收益中的押金抵扣比例。</p>
    <el-alert v-if="marker" type="warning" :closable="false" show-icon title="保存结果未确认">
      <p>从 {{ marker.expected }}% 提交至 {{ marker.target }}%；原请求可能仍在处理中。</p>
      <el-button link type="primary" :disabled="busy" @click="reloadRules">重新读取</el-button>
      <el-button link type="primary" :disabled="busy || !markerChecked" @click="finishReview">已核对，解除限制</el-button>
    </el-alert>
    <el-alert v-if="notice && !marker" type="warning" :closable="false" show-icon :title="notice">
      <el-button v-if="needsReload" link type="primary" :disabled="busy" @click="reloadRules">重新读取</el-button>
    </el-alert>
    <el-card shadow="never" v-loading="loading">
      <el-alert v-if="loadError" :title="rules ? (percent !== rules.deposit_percent ? '读取失败，保留上次规则和未保存输入' : '读取失败，以下为上次读取的规则') : loadError" type="error" :closable="false"><el-button link type="primary" :disabled="busy" @click="reloadRules">重新读取</el-button></el-alert>
      <template v-if="rules">
        <div class="targets" aria-label="默认押金目标">
          <div><span>现金押金默认目标</span><strong>{{ formatRmb(rules.cash_target) }} 元</strong></div>
          <div><span>单抵押金默认目标</span><strong>{{ formatRmb(rules.earnings_target) }} 元</strong><small>现金目标的 {{ rules.earnings_multiplier }} 倍</small></div>
        </div>
        <el-alert v-if="rules.hold_hours === 0" type="warning" :closable="false" show-icon title="当前新订单不启用收益冻结">
          <p>当前新订单结算不会自动抵扣押金；历史订单按创建时记录的冻结时长处理。</p>
        </el-alert>
        <el-alert v-else type="info" :closable="false" show-icon :title="`当前新订单记录 ${rules.hold_hours} 小时冻结`">
          <p>订单使用创建时记录的冻结时长；手工报单使用结算时的设置。</p>
        </el-alert>
        <el-form label-position="top" class="rule-form">
          <el-form-item label="净收益抵押金比例" :error="percentError">
            <el-input-number v-model="percent" :min="0" :max="100" :precision="0" :step="10" controls-position="right" :disabled="busy || !auth.hasPerm('deposit_rule:edit')" />
            <span class="unit">%</span>
          </el-form-item>
          <div v-if="validPercent" class="split"><span>抵押金 {{ percent }}%</span><span>结款 {{ 100 - percent! }}%</span></div>
          <p v-if="validPercent" class="example">有收益冻结且押金未缴足时，每 100 元净收益按比例分为抵扣 {{ percent }} 元、结款 {{ 100 - percent! }} 元；抵扣不超过剩余应缴额。</p>
          <p class="example">比例在收益入账时读取；已有冻结记录不重算。默认目标不代表每位陪玩的实际应缴额。</p>
          <el-alert v-if="!rules.conditional_write_supported" type="warning" :closable="false" title="服务端暂不支持比例冲突校验，请更新服务端后再保存。" />
          <p class="example">配置为读取时快照；旧版客户端仍可能覆盖其他修改。</p>
          <el-button v-if="auth.hasPerm('deposit_rule:edit')" type="primary" :loading="saving" :disabled="busy || !!loadError || !validPercent || percent === rules.deposit_percent || !!marker || needsReload || !rules.conditional_write_supported" @click="save">核对并保存</el-button>
        </el-form>
      </template>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { depositRulesApi, type DepositRules } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'
import { formatRmb } from '@xa/money'

const auth = useAuthStore()
const rules = ref<DepositRules>()
const percent = ref<number>()
const loading = ref(false), saving = ref(false), confirming = ref(false)
const busy = computed(() => loading.value || saving.value || confirming.value)
const loadError = ref(''), notice = ref(''), needsReload = ref(false)
const marker = ref<{ expected: number; target: number } | null>(null)
const markerChecked = ref(false)
const validPercent = computed(() => Number.isInteger(percent.value) && percent.value! >= 0 && percent.value! <= 100)
const percentError = computed(() => validPercent.value ? '' : '请输入 0 至 100 的整数')
let sequence = 0, confirmationSequence = 0, ownerKey = ''
let disposed = false, mounted = false
function markerKey() {
  const id = auth.profile?.id
  return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_deposit_rules_review_${id}` : ''
}
function restoreMarker() {
  const key = markerKey()
  if (key === ownerKey) return
  ownerKey = key; marker.value = null; markerChecked.value = false
  if (!key) return
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || 'null')
    if ([value?.expected, value?.target].every(n => Number.isInteger(n) && n >= 0 && n <= 100)) marker.value = value
  } catch { /* In-page guard remains. */ }
}
function beginReview(expected: number, target: number) {
  marker.value = { expected, target }; markerChecked.value = false
  const key = markerKey()
  if (key) try { sessionStorage.setItem(key, JSON.stringify(marker.value)) } catch { /* In-page guard remains. */ }
}
function clearReview() {
  const key = markerKey()
  if (key) try { sessionStorage.removeItem(key) } catch { /* Storage may be unavailable. */ }
  marker.value = null; markerChecked.value = false
}
async function finishReview() {
  const item = marker.value, revision = auth.sessionRevision, version = sequence
  if (!item || !markerChecked.value || busy.value || !auth.hasPerm('deposit_rule:edit')) return
  confirming.value = true
  const confirmation = ++confirmationSequence
  let accepted = false
  try {
    await ElMessageBox.confirm('请核对当前比例；原请求可能仍在处理中，重新读取只是当前状态。', '解除保存限制', { type: 'warning', confirmButtonText: '已核对，解除限制', cancelButtonText: '继续核对', closeOnClickModal: false })
    accepted = true
  } catch { /* Keep the marker. */ }
  finally { if (confirmation === confirmationSequence) confirming.value = false }
  if (accepted && !disposed && confirmation === confirmationSequence && revision === auth.sessionRevision && version === sequence && marker.value === item && markerChecked.value && auth.hasPerm('deposit_rule:edit')) clearReview()
}
async function load() {
  if (busy.value || !auth.hasPerm('deposit_rule:view')) return
  const current = ++sequence, revision = auth.sessionRevision
  loading.value = true; markerChecked.value = false
  try {
    const result = await depositRulesApi.get(true)
    if (disposed || current !== sequence || revision !== auth.sessionRevision || !auth.hasPerm('deposit_rule:view')) return
    rules.value = result.data; percent.value = result.data.deposit_percent
    loadError.value = ''; notice.value = ''; needsReload.value = false
    if (marker.value) markerChecked.value = true
  } catch {
    if (current === sequence && revision === auth.sessionRevision) loadError.value = '规则读取失败，请检查网络或权限后重试'
  } finally { if (current === sequence) loading.value = false }
}
async function reloadRules() {
  if (busy.value) return
  if (rules.value && percent.value !== rules.value.deposit_percent) {
    const revision = auth.sessionRevision, version = sequence, confirmation = ++confirmationSequence
    confirming.value = true
    let accepted = false
    try { await ElMessageBox.confirm('重新读取会替换未保存的比例。', '重新读取规则', { type: 'warning', confirmButtonText: '重新读取', cancelButtonText: '继续编辑' }); accepted = true }
    catch { /* Keep the draft. */ }
    finally { if (confirmation === confirmationSequence) confirming.value = false }
    if (!accepted || disposed || confirmation !== confirmationSequence || revision !== auth.sessionRevision || version !== sequence) return
  }
  await load()
}
async function save() {
  if (busy.value || loadError.value || !rules.value || !validPercent.value || marker.value || needsReload.value || !rules.value.conditional_write_supported || !auth.hasPerm('deposit_rule:edit') || !auth.hasPerm('deposit_rule:view')) return
  const expected = rules.value.deposit_percent, target = percent.value!, revision = auth.sessionRevision, version = sequence
  if (target === expected) return
  const lines = [h('p', `抵押金比例：${expected}% → ${target}%`), h('p', `结款比例：${100 - expected}% → ${100 - target}%`),
    h('p', '比例在收益入账时读取；已有冻结记录不重算。'),
    h('p', rules.value.hold_hours === 0 ? '当前新订单不启用冻结，结算不会自动抵扣押金。' : '订单按创建时记录的冻结时长处理。')]
  const confirmation = ++confirmationSequence
  confirming.value = true
  let accepted = false
  try { await ElMessageBox.confirm(h('div', { class: 'rules-confirm' }, lines), '核对押金比例', { type: 'warning', confirmButtonText: '确认保存', cancelButtonText: '返回修改', closeOnClickModal: false, customClass: 'rules-confirm-box' }); accepted = true }
  catch { /* Keep the draft. */ }
  finally { if (confirmation === confirmationSequence) confirming.value = false }
  if (!accepted || disposed || confirmation !== confirmationSequence || revision !== auth.sessionRevision || version !== sequence || !auth.hasPerm('deposit_rule:edit') || !auth.hasPerm('deposit_rule:view') || marker.value) return
  beginReview(expected, target); saving.value = true; notice.value = ''
  try {
    const result = await depositRulesApi.update(target, expected, true)
    if (disposed || revision !== auth.sessionRevision || version !== sequence || !auth.hasPerm('deposit_rule:edit') || !auth.hasPerm('deposit_rule:view')) return
    clearReview(); rules.value = result.data; percent.value = result.data.deposit_percent
    if (result.data.deposit_percent === target) ElMessage.success('比例已保存')
    else notice.value = '保存后比例已变化，当前显示重新读取的值，请核对。'
  } catch (failure: any) {
    if (disposed || revision !== auth.sessionRevision || version !== sequence || !auth.hasPerm('deposit_rule:edit') || !auth.hasPerm('deposit_rule:view')) return
    const body = failure?.response?.data || failure, status = failure?.response?.status
    const rejected = (!status || status < 500) && ((typeof body?.code === 'number' && body.code >= 400 && body.code < 500) || (status >= 400 && status < 500))
    if (rejected) {
      clearReview(); needsReload.value = status === 409 || body?.code === 409
      notice.value = `保存未完成：${typeof body?.msg === 'string' && body.msg ? body.msg : '请求被拒绝'}`
    } else notice.value = '保存结果未确认，请重新读取并核对当前比例。'
  } finally { if (revision === auth.sessionRevision && version === sequence) saving.value = false }
}
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreMarker, { immediate: true, flush: 'sync' })
watch([() => auth.sessionRevision, () => auth.hasPerm('deposit_rule:view'), () => auth.hasPerm('deposit_rule:edit')], () => {
  if (confirming.value) ElMessageBox.close()
  ++sequence; ++confirmationSequence
  rules.value = undefined; percent.value = undefined; loading.value = saving.value = confirming.value = false
  loadError.value = notice.value = ''; needsReload.value = false; markerChecked.value = false
  if (mounted && auth.hasPerm('deposit_rule:view')) void load()
}, { flush: 'sync' })
onMounted(() => { mounted = true; void load() })
onBeforeUnmount(() => { disposed = true; ++sequence; ++confirmationSequence; if (confirming.value) ElMessageBox.close() })
</script>

<style scoped lang="scss">
.rules-page { max-width:760px; display:flex; flex-direction:column; gap:16px; }
.rules-page .ap-page-intro { margin:0; }
.targets { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; padding-bottom:20px; margin-bottom:20px; border-bottom:1px solid var(--el-border-color-lighter); }
.targets div { display:flex; flex-direction:column; gap:8px; min-width:0; }.targets span,.targets small { color:var(--el-text-color-secondary); font-size:12px; }.targets strong { font-size:24px; font-weight:600; overflow-wrap:anywhere; }
.rule-form { max-width:520px; margin-top:20px; }.unit { margin-left:10px; }.split { display:flex; flex-wrap:wrap; gap:12px 24px; font-weight:600; color:var(--el-color-primary); }.example { font-size:12px; line-height:1.7; color:var(--el-text-color-secondary); margin:14px 0; }
.rules-confirm p { margin:8px 0; overflow-wrap:anywhere; }
:global(.rules-confirm-box) { width:min(480px, calc(100vw - 24px)); max-height:calc(100vh - 24px); overflow:auto; }
@media(max-width:480px) { .targets { gap:12px; }.targets strong { font-size:20px; } }
</style>
