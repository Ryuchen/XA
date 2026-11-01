<template>
  <div class="fee-page" v-loading="loading">
    <div class="fee-heading"><div><h2>微信支付费率</h2><p>记录当前商户的费率和报价计算方式。</p></div></div>
    <el-alert title="此处只保存配置，不会开启微信支付或修改现有订单。实际手续费以微信商户账单为准。" type="warning" :closable="false" show-icon />
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="fee-alert" />
    <template v-if="loaded">
      <p class="fee-status">{{ revision === 0 ? '尚未配置' : `当前配置版本 ${revision}` }}<span v-if="dirty" role="status"> · {{ needsRefresh ? '待刷新核对' : '有未保存修改' }}</span></p>
      <el-form label-position="top" :disabled="!editable || saving || needsRefresh" class="fee-form">
        <el-form-item label="微信支付商户号" :error="attempted && !validMerchant ? '请输入 8–32 位数字' : ''">
          <el-input v-model.trim="form.merchant_id" maxlength="32" inputmode="numeric" autocomplete="off" />
        </el-form-item>
        <el-form-item label="小程序 AppID" :error="attempted && !validAppid ? '请输入有效的 AppID' : ''">
          <el-input v-model.trim="form.appid" maxlength="32" autocomplete="off" />
        </el-form-item>
        <el-form-item label="商户费率（%）" :error="attempted && !validRate ? (form.fee_basis === 'PAYMENT_TOTAL' && Number(form.rate_percent) === 100 ? '以支付总额为基数时费率须小于 100%' : '请输入 0–100 的百分数，最多四位小数') : ''">
          <el-input v-model.trim="form.rate_percent" inputmode="decimal" maxlength="8" placeholder="例如 0.6" />
          <span class="hint">请按商户实际签约费率填写；0.6 表示 0.6%。</span>
        </el-form-item>
        <el-form-item label="向老板报价的手续费计算基数" :error="attempted && !validBasis ? '请选择计算基数' : ''">
          <el-select v-model="form.fee_basis" placeholder="请选择">
            <el-option label="服务成交金额" value="SERVICE_AMOUNT" />
            <el-option label="服务金额及手续费总额" value="PAYMENT_TOTAL" />
          </el-select>
        </el-form-item>
        <el-form-item label="手续费取整方式" :error="attempted && !validRounding ? '请选择取整方式' : ''">
          <el-select v-model="form.rounding" placeholder="请选择">
            <el-option label="不足一分舍去" value="DOWN" />
            <el-option label="不足一分进一分" value="UP" />
            <el-option label="四舍五入到分" value="HALF_UP" />
          </el-select>
        </el-form-item>
      </el-form>
      <p class="hint">请依据商户签约规则核对计算基数和取整方式；报价与微信实际扣费可能存在差异。</p>
      <div class="fee-actions"><el-button :disabled="saving" @click="refresh">刷新</el-button><el-button v-if="editable" type="primary" :loading="saving" :disabled="needsRefresh || !dirty" @click="save">保存配置</el-button></div>
      <section v-if="revision > 0" class="fee-section" aria-label="手续费试算">
        <h3>手续费试算</h3><p class="hint">使用已保存的版本试算，金额仅供核对，不代表实际渠道扣费。</p>
        <el-alert v-if="dirty" :title="needsRefresh ? '配置待核对，试算暂不可用。' : '当前修改尚未保存，试算仍使用已保存的配置。'" type="info" :closable="false" show-icon />
        <el-form label-position="top" @submit.prevent="preview">
          <el-form-item label="服务成交金额（元）" :error="previewAttempted && !previewFen ? '请输入大于 0、最多两位小数的金额' : ''">
            <el-input v-model.trim="previewYuan" inputmode="decimal" maxlength="12" />
          </el-form-item>
          <el-button native-type="submit" :loading="previewing" :disabled="saving || needsRefresh">试算已保存配置</el-button>
        </el-form>
        <p v-if="previewResult" role="status">版本 {{ previewResult.revision }}：服务 {{ money(previewResult.service_fen) }} 元，预计手续费 {{ money(previewResult.fee_fen) }} 元，合计 {{ money(previewResult.total_fen) }} 元。</p>
      </section>
      <section class="fee-section" aria-label="费率变更记录">
        <h3>变更记录</h3>
        <el-alert v-if="historyError" :title="historyError" type="error" :closable="false" show-icon><el-button link type="primary" @click="loadHistory(historyPage)">重新加载</el-button></el-alert>
        <el-table v-else class="fee-history-table" :data="history" v-loading="historyLoading" empty-text="暂无变更记录">
          <el-table-column prop="revision" label="版本" width="70" />
          <el-table-column prop="merchant_id" label="商户号" min-width="130" />
          <el-table-column prop="appid" label="AppID" min-width="180" />
          <el-table-column prop="rate_percent" label="费率（%）" min-width="100" />
          <el-table-column label="基数" min-width="130"><template #default="scope">{{ basisLabel(scope.row.fee_basis) }}</template></el-table-column>
          <el-table-column label="取整" min-width="120"><template #default="scope">{{ roundingLabel(scope.row.rounding) }}</template></el-table-column>
          <el-table-column prop="operator_name" label="操作人" min-width="100" />
          <el-table-column label="时间" min-width="170"><template #default="scope">{{ formatDateTime(scope.row.updated_at) }}</template></el-table-column>
        </el-table>
        <div v-if="!historyError" class="fee-history-mobile" v-loading="historyLoading">
          <ul v-if="history.length" aria-label="费率变更记录">
            <li v-for="row in history" :key="row.revision">
              <div class="fee-history-head"><strong>版本 {{ row.revision }}</strong><span>{{ formatDateTime(row.updated_at) }}</span></div>
              <dl><div><dt>商户号</dt><dd>{{ row.merchant_id }}</dd></div><div><dt>AppID</dt><dd>{{ row.appid }}</dd></div><div><dt>费率</dt><dd>{{ row.rate_percent }}%</dd></div><div><dt>基数</dt><dd>{{ basisLabel(row.fee_basis) }}</dd></div><div><dt>取整</dt><dd>{{ roundingLabel(row.rounding) }}</dd></div><div><dt>操作人</dt><dd>{{ row.operator_name || '—' }}</dd></div></dl>
            </li>
          </ul>
          <el-empty v-else-if="!historyLoading" description="暂无变更记录" :image-size="72" />
        </div>
        <el-pagination v-if="historyTotal > 10" class="fee-pages" layout="prev, pager, next" :page-size="10" :total="historyTotal" :current-page="historyPage" @current-change="loadHistory" />
      </section>
    </template>
    <el-button v-else-if="error && canView" @click="load">重新加载</el-button>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { onBeforeRouteLeave } from 'vue-router'
import { parseRmbFen, formatRmbFen as money } from '@xa/money'
import { request } from '@/api/request'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/format'

const auth = useAuthStore()
const canView = computed(() => Boolean(auth.token) && auth.hasPerm('payment_fee:view'))
const editable = computed(() => canView.value && auth.hasPerm('payment_fee:edit'))
const form = reactive({ merchant_id: '', appid: '', rate_percent: '', fee_basis: '', rounding: '' })
const savedForm = ref({ ...form })
const dirty = computed(() => Object.keys(savedForm.value).some((key) => {
  const name = key as keyof typeof form
  if (name === 'rate_percent' && form[name] && savedForm.value[name] && /^(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,4})?$/.test(form[name])) return Number(form[name]) !== Number(savedForm.value[name])
  return form[name] !== savedForm.value[name]
}))
const loading = ref(false), loaded = ref(false), saving = ref(false), needsRefresh = ref(false), attempted = ref(false), error = ref('')
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
    try { await ElMessageBox.confirm(needsRefresh.value ? '刷新会覆盖当前输入，请先核对保存结果。' : '刷新会覆盖当前未保存的输入。', '刷新配置', { confirmButtonText: '刷新', cancelButtonText: '继续编辑', type: 'warning' }) }
    catch { return }
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
    ElMessage.success('费率配置已保存'); void loadHistory(1)
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
  if (saving.value) { ElMessage.warning('正在保存，请稍候'); return false }
  if (!dirty.value || !canView.value) return true
  try { await ElMessageBox.confirm(needsRefresh.value ? '离开后当前输入将丢失，保存结果请刷新核对。' : '离开后未保存的修改将丢失。', '离开配置', { confirmButtonText: '离开', cancelButtonText: '继续编辑', type: 'warning' }); return true }
  catch { return false }
})
function beforeUnload(event: BeforeUnloadEvent) { if (canView.value && (dirty.value || saving.value)) { event.preventDefault(); event.returnValue = '' } }
onMounted(() => { window.addEventListener('beforeunload', beforeUnload); void load() })
onUnmounted(() => { window.removeEventListener('beforeunload', beforeUnload); disposed = true; ++sequence; ++historySequence; ++previewSequence })
</script>

<style scoped>
.fee-page { max-width: 1100px; }
.fee-heading h2 { margin: 0; }
.fee-heading p, .hint, .fee-status { color: var(--muted-foreground); line-height: 1.6; }
.fee-alert, .fee-form { margin-top: 18px; }
.fee-form { max-width: 520px; }
.fee-form :deep(.el-select) { width: 100%; }
.fee-actions { display: flex; gap: 12px; justify-content: flex-end; max-width: 520px; margin-top: 16px; }
.fee-section { margin-top: 32px; }
.fee-section :deep(.el-input) { max-width: 280px; }
.fee-pages { margin-top: 16px; }
.fee-history-mobile { display: none; }
.fee-history-mobile ul { margin: 0; padding: 0; list-style: none; }
.fee-history-mobile li { padding: 16px 0; border-top: 1px solid var(--border); }
.fee-history-head { display: flex; justify-content: space-between; gap: 12px; }
.fee-history-head span { color: var(--muted-foreground); font-size: 12px; }
.fee-history-mobile dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 12px; margin: 12px 0 0; font-size: 13px; }
.fee-history-mobile dl div { min-width: 0; }
.fee-history-mobile dt { color: var(--muted-foreground); }
.fee-history-mobile dd { margin: 4px 0 0; overflow-wrap: anywhere; }
@media (max-width: 720px) { .fee-section :deep(.fee-history-table) { display: none; } .fee-history-mobile { display: block; } }
</style>
