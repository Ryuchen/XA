<template>
  <div class="fee-page">
    <Card class="fee-heading"><div><h2>商户报价规则</h2><p>保存的费率用于手续费报价。微信支付开通需另行办理，实际扣费以商户账单为准。</p></div></Card>
    <Alert v-if="error" variant="destructive" class="fee-alert" role="alert"><AlertDescription>{{ error }}</AlertDescription></Alert>
    <p v-if="loading" role="status" class="hint">正在读取配置…</p>
    <template v-if="loaded">
      <div class="fee-workspace">
      <Card class="fee-config">
        <div class="section-head"><h3>商户与费率</h3><Badge variant="outline">{{ editable ? '可编辑' : '只读' }}</Badge></div>
        <p class="fee-status">{{ needsRefresh ? `上次读取版本 ${revision}` : revision === 0 ? '尚未配置' : `当前配置版本 ${revision}` }}<span v-if="dirty || needsRefresh" role="status"> · {{ needsRefresh ? '待刷新核对' : '有未保存修改' }}</span></p>
      <div class="fee-form">
        <label class="fee-field">微信支付商户号
          <Input v-model.trim="form.merchant_id" :aria-invalid="attempted && !validMerchant" maxlength="32" inputmode="numeric" autocomplete="off" :disabled="!editable || saving || needsRefresh" />
          <span v-if="attempted && !validMerchant" class="fee-error">请输入 8–32 位数字</span>
        </label>
        <label class="fee-field">小程序 AppID
          <Input v-model.trim="form.appid" :aria-invalid="attempted && !validAppid" maxlength="32" autocomplete="off" :disabled="!editable || saving || needsRefresh" />
          <span v-if="attempted && !validAppid" class="fee-error">请输入有效的 AppID</span>
        </label>
        <label class="fee-field">商户费率（%）
          <Input v-model.trim="form.rate_percent" :aria-invalid="attempted && !validRate" inputmode="decimal" maxlength="8" placeholder="例如 0.6" :disabled="!editable || saving || needsRefresh" />
          <span v-if="attempted && !validRate" class="fee-error">{{ form.fee_basis === 'PAYMENT_TOTAL' && Number(form.rate_percent) === 100 ? '以支付总额为基数时费率须小于 100%' : '请输入 0–100 的百分数，最多四位小数' }}</span>
          <span class="hint">填写签约费率的百分数，例如 0.6 表示 0.6%。</span>
        </label>
        <label class="fee-field">向老板报价的手续费计算基数
          <NativeSelect v-model="form.fee_basis" :aria-invalid="attempted && !validBasis" :disabled="!editable || saving || needsRefresh"><NativeSelectOption value="">请选择</NativeSelectOption><NativeSelectOption value="SERVICE_AMOUNT">服务成交金额</NativeSelectOption><NativeSelectOption value="PAYMENT_TOTAL">服务金额及手续费总额</NativeSelectOption></NativeSelect>
          <span v-if="attempted && !validBasis" class="fee-error">请选择计算基数</span>
        </label>
        <label class="fee-field">手续费取整方式
          <NativeSelect v-model="form.rounding" :aria-invalid="attempted && !validRounding" :disabled="!editable || saving || needsRefresh"><NativeSelectOption value="">请选择</NativeSelectOption><NativeSelectOption value="DOWN">不足一分舍去</NativeSelectOption><NativeSelectOption value="UP">不足一分进一分</NativeSelectOption><NativeSelectOption value="HALF_UP">四舍五入到分</NativeSelectOption></NativeSelect>
          <span v-if="attempted && !validRounding" class="fee-error">请选择取整方式</span>
        </label>
      </div>
      <p class="hint">请依据商户签约规则核对计算基数和取整方式。现有订单保留原报价。</p>
      <div class="fee-actions"><Button size="xs" variant="outline" :disabled="saving" @click="refresh">刷新</Button><Button size="xs" v-if="editable" :disabled="saving || needsRefresh || !dirty" @click="save">{{ saving ? '保存中…' : '保存配置' }}</Button></div>
      </Card>
      <section v-if="revision > 0" class="fee-section fee-calculator" aria-label="手续费试算">
        <h3>手续费试算</h3><p class="hint">使用已保存的版本 {{ revision }} 试算，以下金额以人民币元计。</p>
        <Alert v-if="dirty || needsRefresh"><AlertDescription>{{ needsRefresh ? '配置待核对，试算暂不可用。' : '当前修改尚未保存，试算仍使用已保存的配置。' }}</AlertDescription></Alert>
        <form class="fee-preview" @submit.prevent="preview">
          <label class="fee-field">服务成交金额（元）
            <Input v-model.trim="previewYuan" inputmode="decimal" maxlength="12" />
            <span v-if="previewAttempted && !previewFen" class="fee-error">请输入大于 0、最多两位小数的金额</span>
          </label>
          <Button size="xs" type="submit" variant="outline" :disabled="previewing || saving || needsRefresh">{{ previewing ? '试算中…' : '试算已保存配置' }}</Button>
        </form>
        <div v-if="previewResult" class="fee-result" role="status">
          <Badge variant="secondary">试算版本 {{ previewResult.revision }}</Badge>
          <dl><div><dt>服务金额</dt><dd>{{ money(previewResult.service_fen) }} 元</dd></div>
            <div><dt>预计手续费</dt><dd>{{ money(previewResult.fee_fen) }} 元</dd></div>
            <div class="fee-total"><dt>预计合计</dt><dd>{{ money(previewResult.total_fen) }} 元</dd></div>
          </dl>
        </div>
      </section>
      </div>
      <section class="fee-section fee-history" aria-label="费率变更记录">
        <h3>变更记录</h3>
        <Alert v-if="historyError" variant="destructive"><AlertDescription>{{ historyError }} <Button size="xs" variant="link" @click="loadHistory(historyPage)">重新加载</Button></AlertDescription></Alert>
        <p v-else-if="historyLoading" class="hint" role="status">正在加载变更记录…</p>
        <template v-else><p v-if="!history.length" class="hint">暂无变更记录</p>
        <div v-else class="fee-records">
          <Table><TableHeader><TableRow><TableHead>版本与时间</TableHead><TableHead>商户号 / AppID</TableHead><TableHead>费率 / 基数</TableHead><TableHead>取整方式</TableHead><TableHead>操作人</TableHead></TableRow></TableHeader>
            <TableBody><TableRow v-for="row in history" :key="row.revision">
              <TableCell class="fee-history-version"><strong>版本 {{ row.revision }}</strong><small>{{ formatDateTime(row.updated_at) }}</small></TableCell>
              <TableCell class="fee-history-account"><span>{{ row.merchant_id || '—' }}</span><small>AppID {{ row.appid || '—' }}</small></TableCell>
              <TableCell class="fee-history-rate"><strong>{{ row.rate_percent }}%</strong><small>{{ basisLabel(row.fee_basis) }}</small></TableCell>
              <TableCell data-label="取整方式">{{ roundingLabel(row.rounding) }}</TableCell>
              <TableCell data-label="操作人">{{ row.operator_name || '—' }}</TableCell>
            </TableRow></TableBody>
          </Table>
        </div>
        </template>
        <div v-if="!historyError && historyTotal > 10" class="fee-pages"><Button size="xs" variant="outline" :disabled="historyLoading || historyPage <= 1" @click="loadHistory(historyPage - 1)">上一页</Button><span>第 {{ historyPage }} / {{ Math.ceil(historyTotal / 10) }} 页</span><Button size="xs" variant="outline" :disabled="historyLoading || historyPage >= Math.ceil(historyTotal / 10)" @click="loadHistory(historyPage + 1)">下一页</Button></div>
      </section>
    </template>
    <Button size="xs" v-else-if="error && canView" variant="outline" @click="load">重新加载</Button>
    <p v-if="notice" role="status" class="fee-notice">{{ notice }}</p>
    <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Badge } from '@xa/shadcn-vue/badge'
import { Input } from '@xa/shadcn-vue/input'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { onBeforeRouteLeave } from 'vue-router'
import { parseRmbFen, formatRmbFen as money } from '@xa/money'
import { request } from '@/api/request'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/format'

const auth = useAuthStore()
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const canView = computed(() => Boolean(auth.token) && auth.hasPerm('payment_fee:view'))
const editable = computed(() => canView.value && auth.hasPerm('payment_fee:edit'))
const form = reactive({ merchant_id: '', appid: '', rate_percent: '', fee_basis: '', rounding: '' })
const savedForm = ref({ ...form })
const dirty = computed(() => Object.keys(savedForm.value).some((key) => {
  const name = key as keyof typeof form
  if (name === 'rate_percent' && form[name] && savedForm.value[name] && /^(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,4})?$/.test(form[name])) return Number(form[name]) !== Number(savedForm.value[name])
  return form[name] !== savedForm.value[name]
}))
const loading = ref(false), loaded = ref(false), saving = ref(false), needsRefresh = ref(false), attempted = ref(false), error = ref(''), notice = ref('')
const revision = ref(0), history = ref<any[]>([]), historyTotal = ref(0), historyPage = ref(1), historyLoading = ref(false), historyError = ref('')
const previewYuan = ref(''), previewing = ref(false), previewAttempted = ref(false), previewResult = ref<any>(null)
const validMerchant = computed(() => /^[0-9]{8,32}$/.test(form.merchant_id))
const validAppid = computed(() => /^[A-Za-z0-9_-]{1,32}$/.test(form.appid))
const validRate = computed(() => /^(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,4})?$/.test(form.rate_percent) && Number(form.rate_percent) <= 100 && !(form.fee_basis === 'PAYMENT_TOTAL' && Number(form.rate_percent) === 100))
const validBasis = computed(() => ['SERVICE_AMOUNT', 'PAYMENT_TOTAL'].includes(form.fee_basis))
const validRounding = computed(() => ['DOWN', 'UP', 'HALF_UP'].includes(form.rounding))
const previewFen = computed(() => {
  if (!/^(?:0|[1-9][0-9]{0,7})(?:\.[0-9]{1,2})?$/.test(previewYuan.value)) return 0
  const amount = parseRmbFen(previewYuan.value)
  return amount > 0 && amount <= 2147483647 ? amount : 0
})
let sequence = 0, historySequence = 0, previewSequence = 0, disposed = false
function current(id: number, session: number) { return !disposed && id === sequence && session === auth.sessionRevision && canView.value }
function basisLabel(value: string) { return value === 'SERVICE_AMOUNT' ? '服务成交金额' : value === 'PAYMENT_TOTAL' ? '支付总额' : '待核对' }
function roundingLabel(value: string) { return ({ DOWN: '舍去', UP: '进一分', HALF_UP: '四舍五入' } as Record<string, string>)[value] || '待核对' }

async function load() {
  if (!canView.value || saving.value) return
  const id = ++sequence, session = auth.sessionRevision
  loading.value = true; loaded.value = false; error.value = ''; previewResult.value = null; previewing.value = false; ++previewSequence; ++historySequence
  try {
    const result = await request({ url: '/config/payment-fee/', method: 'get', silent: true })
    if (!current(id, session)) return
    const data = result.data
    if (!Number.isSafeInteger(data?.revision) || data.revision < 0 || typeof data.configured !== 'boolean' || (data.configured && (!data.merchant_id || !data.appid || typeof data.rate_percent !== 'string' || !data.fee_basis || !data.rounding))) throw new Error('服务端返回的费率配置无效')
    revision.value = data.revision
    Object.assign(form, data.configured ? { merchant_id: data.merchant_id, appid: data.appid, rate_percent: data.rate_percent, fee_basis: data.fee_basis, rounding: data.rounding } : { merchant_id: '', appid: '', rate_percent: '', fee_basis: '', rounding: '' })
    savedForm.value = { ...form }
    attempted.value = false; needsRefresh.value = false; loaded.value = true
    void loadHistory(1)
  } catch (e: any) { if (current(id, session)) error.value = e?.response?.data?.msg || e?.message || '配置加载失败' }
  finally { if (current(id, session)) loading.value = false }
}
async function refresh() {
  if (dirty.value) {
    const confirmed = await askConfirm({ title: '刷新配置', description: needsRefresh.value ? '刷新会覆盖当前输入，请先核对保存结果。' : '刷新会覆盖当前未保存的输入。', confirmText: '刷新', cancelText: '继续编辑' })
    if (!confirmed) return
    if (!canView.value || saving.value) return
  }
  await load()
}
async function save() {
  attempted.value = true
  if (!editable.value || !loaded.value || saving.value || needsRefresh.value || !validMerchant.value || !validAppid.value || !validRate.value || !validBasis.value || !validRounding.value) return
  const id = ++sequence, session = auth.sessionRevision, oldRevision = revision.value
  const data = { revision: oldRevision, ...form }
  saving.value = true; error.value = ''; previewResult.value = null; previewing.value = false; ++previewSequence
  try {
    const result = await request({ url: '/config/payment-fee/', method: 'put', data, silent: true })
    if (!current(id, session) || !editable.value) return
    if (result.data?.revision !== oldRevision + 1 || result.data.merchant_id !== data.merchant_id || result.data.appid !== data.appid || Number(result.data.rate_percent) !== Number(data.rate_percent) || result.data.fee_basis !== data.fee_basis || result.data.rounding !== data.rounding) throw new Error('保存结果无法确认')
    revision.value = result.data.revision; form.rate_percent = result.data.rate_percent; savedForm.value = { ...form }
    notice.value = '费率配置已保存'; void loadHistory(1)
  } catch (e: any) {
    if (current(id, session)) {
      const status = e?.response?.status || e?.code
      if (status === 409) { needsRefresh.value = true; error.value = '配置已被修改，请刷新后核对。' }
      else if (status === 400 || status === 403) error.value = e?.response?.data?.msg || e?.msg || '保存被拒绝，请核对输入和权限。'
      else { needsRefresh.value = true; error.value = '保存结果未确认，请刷新后核对。' }
    }
  } finally { if (current(id, session)) saving.value = false }
}
async function loadHistory(page: number) {
  if (!canView.value) return
  const id = ++historySequence, session = auth.sessionRevision
  historyLoading.value = true; historyError.value = ''; history.value = []; historyTotal.value = 0; historyPage.value = page
  try {
    const result = await request({ url: `/config/payment-fee/history/?page=${page}&page_size=10`, method: 'get', silent: true })
    if (disposed || id !== historySequence || session !== auth.sessionRevision || !canView.value) return
    if (!Array.isArray(result.data?.list) || !Number.isInteger(result.data?.total)) throw new Error('记录格式无效')
    history.value = result.data.list; historyTotal.value = result.data.total; historyPage.value = page
  } catch { if (id === historySequence && session === auth.sessionRevision) historyError.value = '变更记录加载失败，请重试。' }
  finally { if (id === historySequence && session === auth.sessionRevision) historyLoading.value = false }
}
async function preview() {
  previewAttempted.value = true; previewResult.value = null
  if (!previewFen.value || !canView.value || !loaded.value || saving.value || needsRefresh.value || previewing.value) return
  const id = ++previewSequence, session = auth.sessionRevision, expected = revision.value, amount = previewFen.value
  previewing.value = true; error.value = ''
  try {
    const result = await request({ url: '/config/payment-fee/preview/', method: 'post', data: { service_fen: amount }, silent: true })
    if (disposed || id !== previewSequence || session !== auth.sessionRevision || !canView.value) return
    if (result.data?.revision !== expected) { needsRefresh.value = true; error.value = '配置已变化，请刷新后重新试算。'; return }
    if (![result.data.service_fen, result.data.fee_fen, result.data.total_fen].every(Number.isSafeInteger) || result.data.service_fen !== amount || result.data.fee_fen < 0 || result.data.total_fen !== amount + result.data.fee_fen) throw new Error('试算结果无效')
    previewResult.value = result.data
  } catch (e: any) {
    if (id === previewSequence && session === auth.sessionRevision) {
      if (e?.response?.status === 409 || e?.code === 409) { needsRefresh.value = true; error.value = '配置已变化，请刷新后重新试算。' }
      else error.value = e?.response?.data?.msg || e?.msg || '试算失败，请重试。'
    }
  }
  finally { if (id === previewSequence && session === auth.sessionRevision) previewing.value = false }
}
watch([() => auth.sessionRevision, canView, editable], () => {
  ++sequence; ++historySequence; ++previewSequence; loaded.value = loading.value = saving.value = historyLoading.value = previewing.value = false
  history.value = []; historyTotal.value = 0; previewResult.value = null; error.value = canView.value ? '' : '当前账号无查看费率的权限'
  if (canView.value) void load()
}, { flush: 'sync' })
watch(previewYuan, () => { ++previewSequence; previewResult.value = null; previewing.value = false })
onBeforeRouteLeave(async () => {
  if (saving.value) { notice.value = '正在保存，请稍候'; return false }
  if (!dirty.value || !canView.value) return true
  return askConfirm({ title: '离开配置', description: needsRefresh.value ? '离开后当前输入将丢失，保存结果请刷新核对。' : '离开后未保存的修改将丢失。', confirmText: '离开', cancelText: '继续编辑' })
})
function beforeUnload(event: BeforeUnloadEvent) { if (canView.value && (dirty.value || saving.value)) { event.preventDefault(); event.returnValue = '' } }
onMounted(() => { window.addEventListener('beforeunload', beforeUnload); void load() })
onUnmounted(() => { window.removeEventListener('beforeunload', beforeUnload); disposed = true; ++sequence; ++historySequence; ++previewSequence })
</script>

<style scoped>
.fee-page { display:grid; gap:12px; min-width:0; container:payment-fee / inline-size; }
.fee-heading { padding:12px 16px; gap:4px; background:linear-gradient(112deg,var(--secondary),var(--card) 60%); }
.fee-heading h2 { margin:0; font-size:16px; font-weight:600; }
.fee-heading p,.hint,.fee-status { color:var(--muted-foreground); font-size:12px; line-height:1.5; }
.fee-heading p { margin-top:4px; }
.fee-workspace { display:grid; gap:12px; grid-template-columns:minmax(0,3fr) minmax(0,2fr); align-items:start; }
.fee-config:only-child { grid-column:1/-1; max-width:760px; width:100%; }
.fee-section,.fee-config { display:grid; align-content:start; min-width:0; gap:12px; padding:16px; background:var(--card); border:1px solid var(--border); border-radius:12px; }
.section-head,.fee-actions,.fee-pages { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.section-head { justify-content:space-between; } h3 { margin:0; font-size:16px; font-weight:600; }
.fee-form { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); align-items:start; gap:12px; }
.fee-field { display:grid; align-content:start; gap:4px; font-size:12px; min-width:0; }
.fee-field :deep([data-slot='native-select-wrapper']) { width:100%; }
.fee-field :deep(input),.fee-field :deep(select) { min-width:0; width:100%; height:28px; min-height:28px; padding-block:4px; font-size:12px; }
.fee-field :deep(select) { padding-right:32px; }
.fee-error { font-size:12px; color:var(--destructive); }
.fee-actions { justify-content:flex-end; padding-top:8px; border-top:1px solid var(--border); }
.fee-preview { display:grid; gap:8px; }
.fee-preview button { justify-self:start; }
.fee-result { display:grid; gap:8px; justify-items:start; padding:12px; border-radius:8px; background:var(--secondary); }
.fee-result dl { display:grid; width:100%; gap:4px; }
.fee-result dl>div { display:flex; justify-content:space-between; flex-wrap:wrap; gap:8px; font-size:13px; }
.fee-result dt { color:var(--muted-foreground); } .fee-result dd { font-variant-numeric:tabular-nums; }
.fee-result .fee-total { padding-top:8px; border-top:1px solid var(--border); font-weight:600; }
.fee-records { min-width:0; overflow:hidden; border:1px solid var(--border); border-radius:8px; }
.fee-records :deep(td),.fee-records :deep(th) { white-space:normal; padding:8px; vertical-align:top; overflow-wrap:anywhere; }
.fee-records :deep(th) { font-size:12px; background:var(--muted); } .fee-records :deep(td) { font-size:13px; }
.fee-records small { display:block; font-size:12px; margin-top:4px; color:var(--muted-foreground); }
.fee-pages { font-size:12px; color:var(--muted-foreground); justify-content:flex-end; }
.fee-notice { color:var(--primary); font-size:13px; }
@container payment-fee (max-width:820px) {
  .fee-workspace { grid-template-columns:minmax(0,1fr); }
  .fee-config,.fee-section { padding:12px; }
  .fee-records,.fee-records :deep([data-slot='table-container']) { overflow:visible; border:0; }
  .fee-records :deep(table),.fee-records :deep(tbody) { display:grid; }
  .fee-records :deep(thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
  .fee-records :deep(tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .fee-records :deep(tbody tr) { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; padding:12px; border:1px solid var(--border); border-radius:8px; }
  .fee-records :deep(td) { padding:0; }
  .fee-history-version { grid-column:1; grid-row:1; } .fee-history-rate { grid-column:2; grid-row:1; text-align:right; }
  .fee-history-account { grid-column:1/-1; }
  .fee-history-account::before,.fee-records :deep([data-label])::before { display:block; color:var(--muted-foreground); font-size:12px; }
  .fee-history-account::before { content:'商户号'; } .fee-records :deep([data-label])::before { content:attr(data-label); }
  .fee-pages { justify-content:flex-start; }
}
@container payment-fee (max-width:520px) { .fee-form,.fee-records :deep(tbody) { grid-template-columns:minmax(0,1fr); } }
@media(pointer:coarse) { .fee-field :deep(input),.fee-field :deep(select),.fee-page :deep([data-slot='button']) { min-height:44px; } .fee-page :deep([data-slot='button']) { min-width:44px; } }
</style>
