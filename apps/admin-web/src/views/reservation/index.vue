<template>
  <div class="reservation-page">
    <div class="ap-page-head"><div><p class="ap-page-intro">预约与转单</p><p class="hint">预约确认后，可核对实付金额并转为订单。</p></div><el-button v-if="canDispatch" type="primary" size="small" @click="openCreate">代老板预约</el-button></div>
    <div class="ap-card panel">
      <div class="filter"><el-select v-model="status" size="small" aria-label="预约状态" @change="changeStatus"><el-option label="全部状态" value=""/><el-option v-for="(label, key) in statuses" :key="key" :label="label" :value="key.toLowerCase()"/></el-select><el-button size="small" :loading="loading" @click="load">刷新</el-button></div>
      <el-alert v-if="error" type="error" :title="error" :closable="false" show-icon><el-button link type="primary" @click="load">重试</el-button></el-alert>
      <el-skeleton v-if="loading && !rows.length" :rows="4" animated />
      <el-empty v-else-if="!rows.length && !error" description="暂无预约" :image-size="70" />
      <ul v-else class="list"><li v-for="item in rows" :key="item.id" class="entry">
        <div class="entry-top"><strong>{{ item.service.name }}</strong><el-tag size="small" effect="light">{{ statuses[item.status] || item.status }}</el-tag></div>
        <div class="meta"><span>{{ reservationDate(item.start_time) }} — {{ reservationDate(item.end_time) }}（北京时间）</span><span>老板 {{ item.customer.name || '—' }}</span><span>陪玩 {{ item.provider.name || '—' }}</span><span>{{ item.reservation_no }}</span></div>
        <div class="actions"><el-button size="small" @click="view(item.id)">查看详情</el-button><el-button v-if="item.order" size="small" link type="primary" @click="router.push({ path: '/orders', query: { find_order: item.order_no } })">查看订单</el-button></div>
      </li></ul>
      <el-pagination v-if="total > pageSize" class="pager" small background layout="prev, pager, next" :current-page="page" :page-size="pageSize" :total="total" @current-change="changePage" />
    </div>
    <el-drawer v-model="detailOpen" title="预约详情" :size="drawerSize" destroy-on-close>
      <el-skeleton v-if="detailLoading" :rows="5" animated />
      <template v-else-if="detail"><el-alert v-if="detailRefreshError" type="warning" :title="detailRefreshError" :closable="false" show-icon /><div class="detail-head"><strong>{{ detail.service.name }}</strong><el-tag size="small">{{ statuses[detail.status] || detail.status }}</el-tag></div>
        <dl class="details"><div><dt>预约号</dt><dd>{{ detail.reservation_no }}</dd></div><div><dt>老板</dt><dd>{{ detail.customer.name || '—' }}</dd></div><div><dt>陪玩</dt><dd>{{ detail.provider.name || '—' }}</dd></div><div><dt>时间</dt><dd>{{ reservationDate(detail.start_time) }} 至 {{ reservationDate(detail.end_time) }}（北京时间）</dd></div><div><dt>数量</dt><dd>{{ detail.game_rounds }}</dd></div><div><dt>创建时估价</dt><dd>{{ amountToXaCoin(detail.estimated_amount) }} 币 <el-tooltip content="此为原价估算；转单前按当前规则重新报价。" :trigger="['hover','focus','click']"><button class="help" aria-label="估价说明">?</button></el-tooltip></dd></div><div v-if="detail.game_region"><dt>游戏区</dt><dd>{{ detail.game_region }}</dd></div><div v-if="detail.game_nickname"><dt>游戏昵称</dt><dd>{{ detail.game_nickname }}</dd></div><div v-if="detail.game_uid"><dt>游戏 ID</dt><dd>{{ detail.game_uid }}</dd></div><div v-if="detail.remark"><dt>备注</dt><dd>{{ detail.remark }}</dd></div><div v-if="detail.cancel_reason || detail.reject_reason"><dt>原因</dt><dd>{{ detail.cancel_reason || detail.reject_reason }}</dd></div></dl>
        <el-alert v-if="detail.status === 'CONFIRMED'" type="info" :closable="false" title="转单后立即成为已接单订单，请与双方确认服务安排。" />
        <el-alert v-if="detail.status === 'EXPIRED'" type="info" :closable="false" title="预约时段已结束，原预约不能转单；如需服务请重新预约。" />
        <div class="actions detail-actions"><el-button v-if="canDispatch && detail.allowed_actions.includes('confirm')" size="small" :disabled="busy" @click="action('confirm')">确认预约</el-button><el-button v-if="canDispatch && detail.allowed_actions.includes('reject')" size="small" :disabled="busy" @click="action('reject')">拒绝预约</el-button><el-button v-if="canCancel && detail.allowed_actions.includes('cancel')" size="small" :disabled="busy" @click="action('cancel')">取消预约</el-button><el-button v-if="canDispatch && detail.allowed_actions.includes('convert')" type="primary" size="small" :loading="busy" @click="convert">报价并转单</el-button><el-button v-if="detail.order" size="small" link type="primary" @click="router.push({ path: '/orders', query: { find_order: detail.order_no } })">查看订单</el-button></div>
      </template><el-alert v-else type="error" title="预约详情暂不可用" :closable="false" />
    </el-drawer>
    <el-dialog v-model="createOpen" title="代老板预约" width="520px" class="reservation-dialog" destroy-on-close>
      <el-alert v-if="createUncertain" type="warning" :closable="false" show-icon title="预约结果未确认，请先在预约列表核对，避免重复预约。" />
      <el-form label-position="top" size="small" @submit.prevent>
        <el-form-item label="老板" required><el-select v-model="form.customer_account_id" filterable remote reserve-keyword :remote-method="searchBoss" :loading="optionLoading.boss" placeholder="搜索编号、昵称或账号"><el-option v-for="row in bosses" :key="row.id" :value="row.id" :label="`${row.nickname || row.username} · ${row.boss_no}`"/></el-select></el-form-item>
        <el-form-item label="服务" required><el-select v-model="form.service_id" filterable remote reserve-keyword :remote-method="searchService" :loading="optionLoading.service" placeholder="搜索服务"><el-option v-for="row in services" :key="row.id" :value="row.id" :disabled="row.billing_provider_count !== 1" :label="`${row.name} · ${amountToXaCoin(row.price)}币${row.billing_provider_count !== 1 ? '（双陪暂不支持）' : ''}`"/></el-select></el-form-item>
        <el-form-item label="陪玩" required><el-select v-model="form.provider_account_id" filterable remote reserve-keyword :remote-method="searchProvider" :loading="optionLoading.provider" placeholder="搜索编号或昵称"><el-option v-for="row in providers" :key="row.user" :value="row.user" :label="`${row.display_name || row.username} · ${row.escort_no}`"/></el-select></el-form-item>
        <div class="form-grid"><el-form-item label="开始时间" required><el-date-picker v-model="form.start_time" type="datetime" value-format="YYYY-MM-DD HH:mm" placeholder="选择开始时间" :disabled-minutes="disabledMinutes" :disabled-seconds="disabledSeconds" /></el-form-item><el-form-item label="结束时间" required><el-date-picker v-model="form.end_time" type="datetime" value-format="YYYY-MM-DD HH:mm" placeholder="选择结束时间" :disabled-minutes="disabledMinutes" :disabled-seconds="disabledSeconds" /></el-form-item></div>
        <p class="hint">北京时间，按 15 分钟刻度选择；预约创建不扣款。</p>
        <el-form-item label="购买数量"><el-input-number v-model="form.game_rounds" :min="1" :max="999" controls-position="right" /></el-form-item>
        <div class="form-grid"><el-form-item label="游戏区"><el-input v-model="form.game_region" maxlength="50" /></el-form-item><el-form-item label="游戏昵称"><el-input v-model="form.game_nickname" maxlength="50" /></el-form-item></div>
        <el-form-item label="游戏 ID"><el-input v-model="form.game_uid" maxlength="50" /></el-form-item><el-form-item label="备注"><el-input v-model="form.remark" maxlength="255" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer><el-button size="small" @click="createOpen = false">取消</el-button><el-button v-if="createUncertain" size="small" type="primary" @click="reviewCreated">查看预约</el-button><el-button v-else size="small" type="primary" :loading="busy" @click="create">创建预约</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { request } from '@/api/request'
import { userApi, escortApi, serviceItemApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'
import { amountToXaCoin } from '@/utils/format'
import { adminWs } from '@/utils/ws'

const auth = useAuthStore(), router = useRouter(), route = useRoute()
const canDispatch = computed(() => auth.hasPerm('order:dispatch'))
const canCancel = computed(() => auth.hasPerm('order:cancel'))
const baseURL = (import.meta.env.VITE_API_BASE || '/api/admin').replace(/\/admin\/?$/, '')
const api = (url: string, method = 'get', data?: any, params?: any) => request<any>({ baseURL, url: `/orders/reservations/${url}`, method, data, params, silent: true })
const statuses: Record<string, string> = { PENDING: '待确认', CONFIRMED: '已确认', REJECTED: '已拒绝', CANCELLED: '已取消', CONVERTED: '已转单', EXPIRED: '已过期' }
// Reservation times use the same fixed Beijing time shown in the boss app,
// independent of the browser's local timezone.
function reservationDate(value?: string | null): string {
  if (!value) return '—'
  const instant = new Date(value)
  if (Number.isNaN(instant.getTime())) return '—'
  const local = new Date(instant.getTime() + 8 * 60 * 60 * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${local.getUTCFullYear()}-${pad(local.getUTCMonth() + 1)}-${pad(local.getUTCDate())} ${pad(local.getUTCHours())}:${pad(local.getUTCMinutes())}`
}
function reservationInstant(value: string): string {
  if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(value)) throw new Error('请选择有效预约时间')
  const instant = new Date(value.replace(' ', 'T') + ':00+08:00')
  if (Number.isNaN(instant.getTime()) || reservationDate(instant.toISOString()) !== value) throw new Error('请选择有效预约时间')
  return instant.toISOString()
}
function routeStatus(value: unknown): string {
  return typeof value === 'string' && Object.prototype.hasOwnProperty.call(statuses, value.toUpperCase()) ? value.toLowerCase() : ''
}
const rows = ref<any[]>([]), total = ref(0), page = ref(1), pageSize = 20, status = ref(routeStatus(route.query.status)), loading = ref(false), error = ref('')
const detailOpen = ref(false), detail = ref<any>(null), detailLoading = ref(false), createOpen = ref(false), createUncertain = ref(false), busy = ref(false)
const bosses = ref<any[]>([]), providers = ref<any[]>([]), services = ref<any[]>([])
type OptionType = 'boss' | 'provider' | 'service'
const optionLoading = reactive<Record<OptionType, boolean>>({ boss: false, provider: false, service: false })
const optionSeq: Record<OptionType, number> = { boss: 0, provider: 0, service: 0 }
const form = reactive<any>({ customer_account_id: null, provider_account_id: null, service_id: null, start_time: null, end_time: null, game_rounds: 1, game_region: '', game_nickname: '', game_uid: '', remark: '' })
const disabledMinutes = () => Array.from({ length: 60 }, (_, minute) => minute).filter(minute => minute % 15 !== 0)
const disabledSeconds = () => Array.from({ length: 59 }, (_, index) => index + 1)
const drawerSize = ref(window.innerWidth <= 600 ? '100%' : '520px')
const onResize = () => { drawerSize.value = window.innerWidth <= 600 ? '100%' : '520px' }
let sequence = 0, detailSeq = 0
let mounted = false, refreshing = false, pendingRefresh = false
let periodicTimer: ReturnType<typeof setInterval> | null = null
let unsubscribe: Array<() => void> = []
const detailRefreshError = ref('')
const message = (e: any) => e?.msg || e?.message || '操作失败，请重试'
async function load() { const seq = ++sequence; loading.value = true; error.value = ''; try { const res = await api('', 'get', undefined, { page: page.value, page_size: pageSize, status: status.value }); if (seq === sequence) { rows.value = res.data.items; total.value = res.data.total } } catch (e) { if (seq === sequence) error.value = message(e) } finally { if (seq === sequence) loading.value = false } }
function changeStatus() { page.value = 1; const query = { ...route.query }; if (status.value) query.status = status.value; else delete query.status; void router.replace({ query }); void load() }
function changePage(p: number) { page.value = p; void load() }
async function view(id: number) { detailOpen.value = true; detail.value = null; detailRefreshError.value = ''; detailLoading.value = true; const seq = ++detailSeq; try { const res = await api(`${id}/`); if (seq === detailSeq) detail.value = res.data } catch (e) { ElMessage.error(message(e)) } finally { if (seq === detailSeq) detailLoading.value = false } }
function invalidateOptions() { for (const type of ['boss', 'provider', 'service'] as const) { ++optionSeq[type]; optionLoading[type] = false } }
async function search(type: OptionType, keyword = '') { const seq = ++optionSeq[type]; optionLoading[type] = true; try { const fn = type === 'boss' ? userApi : type === 'provider' ? escortApi : serviceItemApi; const res = await fn.options({ keyword, page_size: 50, ...(type === 'service' ? { is_active: true } : {}) }, true); if (seq !== optionSeq[type]) return; const list = res.data.list; if (type === 'boss') bosses.value = list; else if (type === 'provider') providers.value = list; else services.value = list } catch (e) { if (seq === optionSeq[type]) ElMessage.error(message(e)) } finally { if (seq === optionSeq[type]) optionLoading[type] = false } }
const searchBoss = (keyword: string) => search('boss', keyword), searchProvider = (keyword: string) => search('provider', keyword), searchService = (keyword: string) => search('service', keyword)
async function openCreate() { if (!canDispatch.value) return; createUncertain.value = false; Object.assign(form, { customer_account_id: null, provider_account_id: null, service_id: null, start_time: null, end_time: null, game_rounds: 1, game_region: '', game_nickname: '', game_uid: '', remark: '' }); createOpen.value = true; await Promise.all([search('boss'), search('service'), search('provider')]) }
function reviewCreated() { createOpen.value = false; status.value = ''; changeStatus() }
async function create() {
  if (busy.value || createUncertain.value || !canDispatch.value) return
  if (!form.customer_account_id || !form.provider_account_id || !form.service_id || !form.start_time || !form.end_time) { ElMessage.warning('请选择老板、服务、陪玩和时间'); return }
  const revision = auth.sessionRevision
  let submitted = false
  busy.value = true
  try {
    const payload = { ...form, start_time: reservationInstant(form.start_time), end_time: reservationInstant(form.end_time) }
    submitted = true
    await api('', 'post', payload)
    if (revision !== auth.sessionRevision) return
    ElMessage.success('预约已创建，等待确认'); createOpen.value = false; page.value = 1; await load()
  } catch (e: any) {
    if (revision !== auth.sessionRevision) return
    const httpStatus = e?.response?.status
    if (submitted && ((!e?.response && typeof e?.code !== 'number') || httpStatus >= 500)) createUncertain.value = true
    else ElMessage.error(message(e))
  } finally { busy.value = false }
}
async function action(name: 'confirm' | 'reject' | 'cancel') { if (!detail.value || busy.value || !(name === 'cancel' ? canCancel.value : canDispatch.value) || !detail.value.allowed_actions.includes(name)) return; const id = detail.value.id, revision = auth.sessionRevision; let reason = ''; busy.value = true; try { if (name === 'confirm') await ElMessageBox.confirm('确认该预约？', '确认预约', { type: 'warning' }); else { const answer = await ElMessageBox.prompt(name === 'cancel' ? '可填写取消原因' : '可填写拒绝原因', name === 'cancel' ? '取消预约' : '拒绝预约', { inputPlaceholder: '原因（选填）', inputValidator: (value: string) => !value || value.length <= 255, confirmButtonText: '确认', cancelButtonText: '返回' }); reason = answer.value || '' } if (revision !== auth.sessionRevision || !detailOpen.value || detail.value?.id !== id || !(name === 'cancel' ? canCancel.value : canDispatch.value)) return; await api(`${id}/${name}/`, 'post', { reason }); ElMessage.success(name === 'confirm' ? '预约已确认' : name === 'cancel' ? '预约已取消' : '预约已拒绝'); await Promise.all([load(), view(id)]) } catch (e) { if (e !== 'cancel' && e !== 'close' && revision === auth.sessionRevision) { ElMessage.error(message(e)); await view(id) } } finally { busy.value = false } }
async function convert() { if (!detail.value || busy.value || !canDispatch.value || !detail.value.allowed_actions.includes('convert') || !auth.profile?.id) return; const id = detail.value.id, revision = auth.sessionRevision, accountId = auth.profile.id; busy.value = true; try { const quote = (await api(`${id}/quote/`)).data; if (revision !== auth.sessionRevision || !detailOpen.value || detail.value?.id !== id || !canDispatch.value) return; if (!quote.balance_sufficient) { ElMessage.warning('老板余额不足或钱包不可用，请先核对'); return }; await ElMessageBox.confirm(`将扣老板余额 ${amountToXaCoin(quote.amount)} 币，转单后立即进入已接单状态。请确认服务安排。`, '确认预约转单', { type: 'warning', confirmButtonText: '确认转单', cancelButtonText: '返回' }); if (revision !== auth.sessionRevision || !detailOpen.value || detail.value?.id !== id || !canDispatch.value) return; const key = `xa:reservation-convert:${accountId}:${id}`; let requestId = sessionStorage.getItem(key); if (!requestId) { requestId = crypto.randomUUID(); sessionStorage.setItem(key, requestId) }; await api(`${id}/convert/`, 'post', { client_request_id: requestId, expected_amount: quote.amount, expected_service: quote.service }); sessionStorage.removeItem(key); ElMessage.success('预约已转为订单'); await Promise.all([load(), view(id)]) } catch (e: any) { if (e === 'cancel' || e === 'close' || revision !== auth.sessionRevision) return; ElMessage.error(message(e)); await Promise.all([load(), view(id)]) } finally { busy.value = false } }
// HTTP remains the source of truth after realtime invalidation or reconnect.
async function requestRefresh() {
  if (!mounted || !auth.hasPerm('order:view')) return
  if (document.hidden || busy.value || createOpen.value || detailLoading.value || refreshing) { pendingRefresh = true; return }
  refreshing = true
  pendingRefresh = false
  const revision = auth.sessionRevision
  try {
    await load()
    if (!mounted || revision !== auth.sessionRevision) return
    if (document.hidden || busy.value || createOpen.value || detailLoading.value) { pendingRefresh = true; return }
    const id = detailOpen.value && detail.value?.id
    if (!id) return
    const seq = ++detailSeq
    try {
      const res = await api(`${id}/`)
      if (!mounted || revision !== auth.sessionRevision || seq !== detailSeq || !detailOpen.value || detail.value?.id !== id) return
      if (document.hidden || busy.value || createOpen.value) { pendingRefresh = true; return }
      detail.value = res.data
      detailRefreshError.value = ''
    } catch {
      if (mounted && revision === auth.sessionRevision && seq === detailSeq && detailOpen.value && detail.value?.id === id) {
        detailRefreshError.value = '详情更新失败，请检查网络后重新查看。'
      }
    }
  } finally {
    refreshing = false
    if (pendingRefresh && mounted && !document.hidden && !busy.value && !createOpen.value && !detailLoading.value) void requestRefresh()
  }
}
function onVisibilityChange() { if (!document.hidden) void requestRefresh() }
watch(() => route.query.status, value => { const next = routeStatus(value); if (next !== status.value) { status.value = next; page.value = 1; void load() } })
watch(() => route.query.find_reservation, value => {
  if (typeof value !== 'string' || !/^[1-9]\d*$/.test(value)) return
  const id = Number(value)
  if (!Number.isSafeInteger(id)) return
  void view(id)
  const query = { ...route.query }; delete query.find_reservation
  void router.replace({ query })
}, { immediate: true })
watch([busy, createOpen, detailLoading], () => { if (pendingRefresh && !busy.value && !createOpen.value && !detailLoading.value) void requestRefresh() }, { flush: 'sync' })
watch(() => auth.sessionRevision, () => { ++sequence; ++detailSeq; invalidateOptions(); pendingRefresh = false; rows.value = []; detail.value = null; detailRefreshError.value = ''; detailOpen.value = false; createOpen.value = false; if (auth.hasPerm('order:view')) void requestRefresh() })
onMounted(() => { mounted = true; unsubscribe = [adminWs.on('reservation_status_update', () => void requestRefresh()), adminWs.on('connected', () => void requestRefresh())]; window.addEventListener('resize', onResize); document.addEventListener('visibilitychange', onVisibilityChange); periodicTimer = setInterval(() => { if (!document.hidden) void requestRefresh() }, 60000); void requestRefresh(); adminWs.connect() })
onUnmounted(() => { mounted = false; ++sequence; ++detailSeq; invalidateOptions(); unsubscribe.forEach(off => off()); if (periodicTimer) clearInterval(periodicTimer); document.removeEventListener('visibilitychange', onVisibilityChange); window.removeEventListener('resize', onResize) })
</script>

<style scoped>
.reservation-page{display:flex;flex-direction:column;gap:16px}.hint{font-size:12px;color:var(--muted-foreground);margin:4px 0}.panel{padding:16px}.filter,.actions,.entry-top,.detail-head{display:flex;align-items:center;gap:8px}.filter .el-select{width:150px}.list{list-style:none;padding:0;margin:12px 0 0}.entry{padding:14px 0;border-bottom:1px solid var(--border)}.entry:last-child{border-bottom:0}.entry-top,.detail-head{justify-content:space-between}.meta{display:flex;flex-wrap:wrap;gap:6px 16px;color:var(--muted-foreground);font-size:12px;margin:8px 0}.pager{margin-top:12px}.details{margin:16px 0}.details div{display:grid;grid-template-columns:88px minmax(0,1fr);gap:8px;padding:8px 0;border-bottom:1px solid var(--border)}dt{color:var(--muted-foreground)}dd{margin:0;overflow-wrap:anywhere}.detail-actions{flex-wrap:wrap;margin-top:16px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.el-select,.el-date-editor{width:100%}.help{border:0;background:transparent;color:var(--primary);cursor:pointer}.help:focus-visible{outline:2px solid var(--primary)}@media(max-width:600px){.form-grid{grid-template-columns:1fr}.reservation-dialog{width:calc(100% - 24px)!important}.panel{padding:12px}.meta{flex-direction:column;gap:4px}}
</style>

<style>
/* Element Plus teleports dialogs outside the scoped page tree. */
@media (max-width: 600px) {
  .el-dialog.reservation-dialog {
    display: flex;
    flex-direction: column;
    width: calc(100% - 24px) !important;
    max-height: calc(100dvh - 24px);
    margin: 12px auto !important;
  }
  .el-dialog.reservation-dialog .el-dialog__body {
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
  }
}
</style>
