<template>
  <main class="rules-page">
    <Card class="rules-header"><div><h2>单抵收益分配</h2><p>核对默认押金目标，设置陪玩净收益中的抵扣比例。个人实际应缴额以陪玩档案为准。</p></div>
      <Button v-if="rules" size="xs" variant="outline" :disabled="busy" @click="reloadRules">刷新规则</Button>
    </Card>
    <Alert v-if="marker && !saving"><AlertTitle>保存结果未确认</AlertTitle><AlertDescription>
      <p>已提交从 {{ marker.expected }}% 到 {{ marker.target }}% 的修改。原请求可能仍在处理中。</p>
      <div class="actions"><Button size="xs" variant="outline" :disabled="busy" @click="reloadRules">重新读取</Button>
        <Button v-if="auth.hasPerm('deposit_rule:edit')" size="xs" variant="outline" :disabled="busy || !markerChecked" @click="finishReview">已核对，解除限制</Button></div>
    </AlertDescription></Alert>
    <Alert v-if="notice && !marker" role="status"><AlertDescription>{{ notice }}
      <Button v-if="needsReload" size="xs" variant="outline" :disabled="busy" @click="reloadRules">重新读取</Button>
    </AlertDescription></Alert>
    <p v-if="loading" role="status" class="hint">正在读取规则…</p>
    <Alert v-if="loadError" role="alert" variant="destructive"><AlertDescription>{{ rules ? (percent !== rules.deposit_percent ? '读取失败，保留上次规则和未保存输入' : '读取失败，以下为上次读取的规则') : loadError }}<Button size="xs" variant="outline" :disabled="busy" @click="reloadRules">重新读取</Button></AlertDescription></Alert>
    <div v-if="rules" class="rules-grid">
      <Card class="rules-card">
        <header><h3>默认押金目标</h3><p class="hint">按押金方式区分，金额以人民币元计。</p></header>
        <dl class="targets">
          <div><dt>现金押金</dt><dd>{{ formatRmb(rules.cash_target) }} <small>元</small></dd></div>
          <div><dt>单抵押金</dt><dd>{{ formatRmb(rules.earnings_target) }} <small>元</small></dd><small>现金目标的 {{ rules.earnings_multiplier }} 倍</small></div>
        </dl>
        <div class="hold-policy">
          <h3>收益冻结</h3>
          <template v-if="rules.hold_hours === 0"><strong>当前新订单不启用收益冻结</strong><p class="hint">新订单结算时不会自动抵扣押金。历史订单按创建时记录的冻结时长处理。</p></template>
          <template v-else><strong>当前新订单记录 {{ rules.hold_hours }} 小时冻结</strong><p class="hint">订单按创建时记录的冻结时长执行，手工报单采用结算时的设置。</p></template>
        </div>
      </Card>
      <Card class="rules-card">
        <div class="section-head"><h3>净收益分配比例</h3><Badge variant="outline">{{ auth.hasPerm('deposit_rule:edit') ? '可编辑' : '只读' }}</Badge></div>
        <dl class="current"><div><dt>读取时抵押金</dt><dd>{{ rules.deposit_percent }}%</dd></div><div><dt>读取时结款</dt><dd>{{ rules.settlement_percent }}%</dd></div></dl>
        <div class="rule-form">
          <label for="deposit-percent">净收益抵押金比例</label>
          <div class="rule-input"><Input id="deposit-percent" v-model.number="percent" type="number" min="0" max="100" step="1" aria-describedby="percent-message" :aria-invalid="!validPercent" :disabled="busy || !auth.hasPerm('deposit_rule:edit')" /><span aria-hidden="true">%</span></div>
          <p id="percent-message" :class="percentError ? 'rule-error' : 'hint'">{{ percentError || '填写 0 至 100 的整数。' }}</p>
          <div v-if="validPercent" class="split">
            <div class="section-head"><strong>每 100 元净收益的分配示例</strong><Badge v-if="percent !== rules.deposit_percent" variant="outline">未保存输入</Badge></div>
            <dl><div><dt>抵扣押金</dt><dd>{{ percent }} 元</dd></div><div><dt>结款</dt><dd>{{ 100 - percent! }} 元</dd></div></dl>
            <p class="hint">有收益冻结且押金未缴足时按此比例分配。实际抵扣最多为剩余应缴额。</p>
          </div>
          <p class="hint">比例在收益入账时读取，已有冻结记录不重算。旧版客户端仍可能覆盖其他修改。</p>
          <Alert v-if="!rules.conditional_write_supported"><AlertDescription>服务端暂不支持比例冲突校验，请更新服务端后再保存。</AlertDescription></Alert>
          <div v-if="auth.hasPerm('deposit_rule:edit')" class="actions"><Button size="xs" :disabled="busy || !!loadError || !validPercent || percent === rules.deposit_percent || !!marker || needsReload || !rules.conditional_write_supported" @click="save">{{ saving ? '保存中…' : '核对并保存' }}</Button></div>
        </div>
      </Card>
    </div>
    <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
  </main>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Badge } from '@xa/shadcn-vue/badge'
import { Card } from '@xa/shadcn-vue/card'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { depositRulesApi, type DepositRules } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'
import { formatRmb } from '@xa/money'

const auth = useAuthStore()
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
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
  const accepted = await askConfirm({ title:'解除保存限制', description:'请核对当前比例；原请求可能仍在处理中，重新读取只是当前状态。', confirmText:'已核对，解除限制', cancelText:'继续核对' })
  if (confirmation === confirmationSequence) confirming.value = false
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
    const accepted = await askConfirm({ title:'重新读取规则', description:'重新读取会替换未保存的比例。', confirmText:'重新读取', cancelText:'继续编辑' })
    if (confirmation === confirmationSequence) confirming.value = false
    if (!accepted || disposed || confirmation !== confirmationSequence || revision !== auth.sessionRevision || version !== sequence) return
  }
  await load()
}
async function save() {
  if (busy.value || loadError.value || !rules.value || !validPercent.value || marker.value || needsReload.value || !rules.value.conditional_write_supported || !auth.hasPerm('deposit_rule:edit') || !auth.hasPerm('deposit_rule:view')) return
  const expected = rules.value.deposit_percent, target = percent.value!, revision = auth.sessionRevision, version = sequence
  if (target === expected) return
  const description = `抵押金比例：${expected}% → ${target}%；结款比例：${100 - expected}% → ${100 - target}%。比例在收益入账时读取；已有冻结记录不重算。${rules.value.hold_hours === 0 ? '当前新订单不启用冻结，结算不会自动抵扣押金。' : '订单按创建时记录的冻结时长处理。'}`
  const confirmation = ++confirmationSequence
  confirming.value = true
  const accepted = await askConfirm({ title:'核对押金比例', description, confirmText:'确认保存', cancelText:'返回修改' })
  if (confirmation === confirmationSequence) confirming.value = false
  if (!accepted || disposed || confirmation !== confirmationSequence || revision !== auth.sessionRevision || version !== sequence || !auth.hasPerm('deposit_rule:edit') || !auth.hasPerm('deposit_rule:view') || marker.value) return
  beginReview(expected, target); saving.value = true; notice.value = ''
  try {
    const result = await depositRulesApi.update(target, expected, true)
    if (disposed || revision !== auth.sessionRevision || version !== sequence || !auth.hasPerm('deposit_rule:edit') || !auth.hasPerm('deposit_rule:view')) return
    clearReview(); rules.value = result.data; percent.value = result.data.deposit_percent
    if (result.data.deposit_percent === target) notice.value = '比例已保存'
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
  if (confirming.value) confirmNo()
  ++sequence; ++confirmationSequence
  rules.value = undefined; percent.value = undefined; loading.value = saving.value = confirming.value = false
  loadError.value = notice.value = ''; needsReload.value = false; markerChecked.value = false
  if (mounted && auth.hasPerm('deposit_rule:view')) void load()
}, { flush: 'sync' })
onMounted(() => { mounted = true; void load() })
onBeforeUnmount(() => { disposed = true; ++sequence; ++confirmationSequence; if (confirming.value) confirmNo() })
</script>

<style scoped>
.rules-page { display:grid; gap:12px; min-width:0; container:deposit-rules / inline-size; }
.rules-header { display:flex; flex-flow:row wrap; align-items:center; justify-content:space-between; gap:8px; padding:12px 16px; background:linear-gradient(112deg,var(--secondary),var(--card) 60%); }
.rules-header h2,.rules-card h3 { margin:0; font-size:16px; font-weight:600; }
.rules-header p,.hint,.rule-error { margin:0; color:var(--muted-foreground); font-size:12px; line-height:1.5; }
.rules-header p,.rules-card header p,.hold-policy p { margin-top:4px; }
.rules-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); align-items:start; gap:12px; }
.rules-card { min-width:0; padding:16px; gap:12px; }
.targets,.current,.split dl { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
.targets dt,.current dt,.split dt { color:var(--muted-foreground); font-size:12px; }
.targets dd,.current dd,.split dd { margin-top:4px; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
.targets dd { font-size:24px; font-weight:600; line-height:1.3; letter-spacing:-.5px; }
.targets small { font-size:12px; color:var(--muted-foreground); font-weight:400; letter-spacing:0; }
.hold-policy { display:grid; gap:4px; padding-top:12px; border-top:1px solid var(--border); }
.hold-policy strong,.split strong { font-size:13px; font-weight:600; }
.section-head,.actions { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.section-head { justify-content:space-between; }
.current { padding-bottom:12px; border-bottom:1px solid var(--border); }
.current dd { font-size:16px; font-weight:600; }
.rule-form { display:grid; gap:8px; }
.rule-form label { font-size:13px; }
.rule-input { display:flex; align-items:center; gap:8px; }
.rule-input :deep(input) { max-width:144px; height:28px; min-height:28px; padding-block:4px; font-size:13px; }
.rule-input>span { color:var(--muted-foreground); font-size:12px; }
.rule-error { color:var(--destructive); }
.split { display:grid; gap:8px; padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--secondary); }
.split dd { font-size:16px; font-weight:600; }
.actions { justify-content:flex-start; }
@container deposit-rules (max-width:720px) { .rules-grid { grid-template-columns:minmax(0,1fr); } .rules-card { padding:12px; } .targets dd { font-size:20px; } }
@media(pointer:coarse) { .rules-page :deep([data-slot='button']),.rule-input :deep(input) { min-height:44px; } .rules-page :deep([data-slot='button']) { min-width:44px; } }
</style>
