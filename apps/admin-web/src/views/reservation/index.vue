<template>
  <div class="reservation-page">
    <Card class="panel">
      <header class="page-heading"><div><h2>预约与转单</h2><p class="hint">预约确认后，可核对实付金额并转为订单。</p></div>
        <Button size="xs" v-if="canDispatch" @click="openCreate">代老板预约</Button>
      </header>
      <p v-if="notice && !detailOpen && !createOpen" role="status" class="feedback">{{ notice }}</p>
      <div class="filter"><NativeSelect v-model="status" aria-label="预约状态" @update:model-value="changeStatus"><NativeSelectOption value="">全部状态</NativeSelectOption><NativeSelectOption v-for="(label, key) in statuses" :key="key" :value="key.toLowerCase()">{{ label }}</NativeSelectOption></NativeSelect><Button size="xs" variant="outline" :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新' }}</Button></div>
      <p v-if="status === 'pending' || status === 'confirmed'" class="hint">按预约开始时间从近到远排列</p>
      <Alert v-if="error" variant="destructive"><AlertDescription>{{ error }} <Button size="xs" variant="link" @click="load">重试</Button></AlertDescription></Alert>
      <p v-if="loading && !rows.length" role="status">正在读取预约…</p>
      <p v-else-if="!rows.length && !error" class="empty">暂无预约</p>
      <ul v-else class="list">
        <li v-for="item in rows" :key="item.id" class="entry">
          <div class="entry-top"><h3>{{ item.service.name }}</h3><Badge :variant="item.status === 'PENDING' ? 'secondary' : 'outline'">{{ statuses[item.status] || item.status }}</Badge></div>
          <p class="hint">预约号 {{ item.reservation_no }}</p>
          <dl class="slot"><div><dt>开始</dt><dd>{{ reservationDate(item.start_time) }}</dd></div><div><dt>结束</dt><dd>{{ reservationDate(item.end_time) }}</dd></div></dl>
          <p class="hint">北京时间</p>
          <dl class="people"><div><dt>老板</dt><dd>{{ item.customer.name || '—' }}</dd></div><div><dt>陪玩</dt><dd>{{ item.provider.name || '—' }}</dd></div></dl>
          <div class="actions"><Button size="xs" variant="outline" @click="view(item.id)">查看详情</Button><Button size="xs" v-if="item.order" variant="outline" @click="router.push({path:'/orders',query:{find_order:item.order_no}})">查看订单</Button></div>
        </li>
      </ul>
      <div v-if="total > pageSize" class="pager"><span>第 {{ page }} / {{ Math.ceil(total / pageSize) }} 页 · 共 {{ total }} 条</span><Button size="xs" variant="outline" :disabled="loading || page <= 1" @click="changePage(page - 1)">上一页</Button><Button size="xs" variant="outline" :disabled="loading || page * pageSize >= total" @click="changePage(page + 1)">下一页</Button></div>
    </Card>
    <Dialog v-model:open="detailOpen"><DialogContent class="reservation-detail"><DialogHeader><DialogTitle>预约详情</DialogTitle><DialogDescription>查看预约状态和服务安排。</DialogDescription></DialogHeader>
      <Alert v-if="notice"><AlertDescription>{{ notice }}</AlertDescription></Alert>
      <p v-if="detailLoading" role="status">正在读取预约详情…</p>
      <template v-else-if="detail"><Alert v-if="detailRefreshError" variant="destructive"><AlertDescription>{{ detailRefreshError }}</AlertDescription></Alert><div class="detail-head"><strong>{{ detail.service.name }}</strong><Badge variant="outline">{{ statuses[detail.status] || detail.status }}</Badge></div>
        <dl class="details"><div><dt>预约号</dt><dd>{{ detail.reservation_no }}</dd></div><div><dt>老板</dt><dd>{{ detail.customer.name || '—' }}</dd></div><div><dt>陪玩</dt><dd>{{ detail.provider.name || '—' }}</dd></div><div><dt>时间</dt><dd>{{ reservationDate(detail.start_time) }} 至 {{ reservationDate(detail.end_time) }}（北京时间）</dd></div><div><dt>数量</dt><dd>{{ detail.game_rounds }}</dd></div><div><dt>创建时估价</dt><dd>{{ amountToXaCoin(detail.estimated_amount) }} 币 <Popover><PopoverTrigger as-child><Button size="xs" variant="link" aria-label="估价说明">估价说明</Button></PopoverTrigger><PopoverContent>此为原价估算；转单前按当前规则重新报价。</PopoverContent></Popover></dd></div><div v-if="detail.game_region"><dt>游戏区</dt><dd>{{ detail.game_region }}</dd></div><div v-if="detail.game_nickname"><dt>游戏昵称</dt><dd>{{ detail.game_nickname }}</dd></div><div v-if="detail.game_uid"><dt>游戏 ID</dt><dd>{{ detail.game_uid }}</dd></div><div v-if="detail.remark"><dt>备注</dt><dd>{{ detail.remark }}</dd></div><div v-if="detail.cancel_reason || detail.reject_reason"><dt>原因</dt><dd>{{ detail.cancel_reason || detail.reject_reason }}</dd></div></dl>
        <Alert v-if="detail.status === 'CONFIRMED'"><AlertDescription>转单后立即成为已接单订单，请与双方确认服务安排。</AlertDescription></Alert>
        <Alert v-if="detail.status === 'EXPIRED'"><AlertDescription>预约时段已结束，原预约不能转单；如需服务请重新预约。</AlertDescription></Alert>
        <div class="actions detail-actions"><Button size="xs" v-if="canDispatch && detail.allowed_actions.includes('confirm')" variant="outline" :disabled="busy" @click="action('confirm')">确认预约</Button><Button size="xs" v-if="canDispatch && detail.allowed_actions.includes('reject')" variant="outline" :disabled="busy" @click="action('reject')">拒绝预约</Button><Button size="xs" v-if="canCancel && detail.allowed_actions.includes('cancel')" variant="outline" :disabled="busy" @click="action('cancel')">取消预约</Button><Button size="xs" v-if="canDispatch && detail.allowed_actions.includes('convert')" :disabled="busy" @click="convert">{{ busy ? '处理中…' : '报价并转单' }}</Button><Button size="xs" v-if="detail.order" variant="link" @click="router.push({ path: '/orders', query: { find_order: detail.order_no } })">查看订单</Button></div>
      </template><Alert v-else variant="destructive"><AlertDescription>{{ detailReadError || '预约详情暂不可用' }} <Button size="xs" v-if="requestedId" variant="outline" @click="view(requestedId)">重新读取详情</Button></AlertDescription></Alert>
    </DialogContent></Dialog>
    <Dialog :open="createOpen" @update:open="value => { if (!busy) createOpen = value }"><DialogContent class="reservation-dialog" :show-close-button="!busy" @interact-outside="event => { if (busy) event.preventDefault() }" @escape-key-down="event => { if (busy) event.preventDefault() }"><DialogHeader><DialogTitle>代老板预约</DialogTitle><DialogDescription>选择双方、服务与北京时间，创建时不扣款。</DialogDescription></DialogHeader>
      <Alert v-if="createUncertain"><AlertDescription>预约结果未确认，请先在预约列表核对，避免重复预约。</AlertDescription></Alert>
      <Alert v-if="notice" variant="destructive"><AlertDescription>{{ notice }}</AlertDescription></Alert>
      <p v-if="createUncertain && createPendingSummary" class="hint">{{ createPendingSummary }}</p>
      <div class="reservation-form">
        <div class="option-field"><span>老板</span><div class="search-row"><Input aria-label="搜索老板关键词" v-model="optionKeywords.boss" placeholder="搜索编号、昵称或账号" @keyup.enter="search('boss', optionKeywords.boss)" /><Button size="xs" variant="outline" aria-label="搜索老板" :disabled="optionLoading.boss" @click="search('boss', optionKeywords.boss)">搜索</Button></div><NativeSelect :model-value="String(form.customer_account_id || '')" aria-label="选择老板" @update:model-value="value => { form.customer_account_id = value ? Number(value) : null }"><NativeSelectOption value="">选择老板</NativeSelectOption><NativeSelectOption v-for="row in bosses" :key="row.id" :value="String(row.id)">{{ row.nickname || row.username }} · {{ row.boss_no }}</NativeSelectOption></NativeSelect></div>
        <div class="option-field"><span>服务</span><div class="search-row"><Input aria-label="搜索服务关键词" v-model="optionKeywords.service" placeholder="搜索服务" @keyup.enter="search('service', optionKeywords.service)" /><Button size="xs" variant="outline" aria-label="搜索服务" :disabled="optionLoading.service" @click="search('service', optionKeywords.service)">搜索</Button></div><NativeSelect :model-value="String(form.service_id || '')" aria-label="选择服务" @update:model-value="value => { form.service_id = value ? Number(value) : null }"><NativeSelectOption value="">选择服务</NativeSelectOption><NativeSelectOption v-for="row in services" :key="row.id" :value="String(row.id)" :disabled="row.billing_provider_count !== 1">{{ row.name }} · {{ amountToXaCoin(row.price) }}币{{ row.billing_provider_count !== 1 ? '（双陪暂不支持）' : '' }}</NativeSelectOption></NativeSelect></div>
        <div class="option-field"><span>陪玩</span><div class="search-row"><Input aria-label="搜索陪玩关键词" v-model="optionKeywords.provider" placeholder="搜索编号或昵称" @keyup.enter="search('provider', optionKeywords.provider)" /><Button size="xs" variant="outline" aria-label="搜索陪玩" :disabled="optionLoading.provider" @click="search('provider', optionKeywords.provider)">搜索</Button></div><NativeSelect :model-value="String(form.provider_account_id || '')" aria-label="选择陪玩" @update:model-value="value => { form.provider_account_id = value ? Number(value) : null }"><NativeSelectOption value="">选择陪玩</NativeSelectOption><NativeSelectOption v-for="row in providers" :key="row.user" :value="String(row.user)">{{ row.display_name || row.username }} · {{ row.escort_no }}</NativeSelectOption></NativeSelect></div>
        <div class="form-grid"><label>开始时间<Input :model-value="(form.start_time || '').replace(' ', 'T')" type="datetime-local" step="900" @update:model-value="value => { form.start_time = String(value).replace('T', ' ') || null }" /></label><label>结束时间<Input :model-value="(form.end_time || '').replace(' ', 'T')" type="datetime-local" step="900" @update:model-value="value => { form.end_time = String(value).replace('T', ' ') || null }" /></label></div>
        <p class="hint">北京时间，按 15 分钟刻度选择；预约创建不扣款。</p>
        <label>购买数量<Input v-model.number="form.game_rounds" type="number" min="1" max="999" step="1" /></label>
        <div class="form-grid"><label>游戏区<Input v-model="form.game_region" maxlength="50" /></label><label>游戏昵称<Input v-model="form.game_nickname" maxlength="50" /></label></div>
        <label>游戏 ID<Input v-model="form.game_uid" maxlength="50" /></label><label>备注<Textarea v-model="form.remark" maxlength="255" :rows="2" /></label>
      </div>
      <DialogFooter><Button size="xs" variant="outline" :disabled="busy" @click="createOpen = false">取消</Button><template v-if="createUncertain"><Button size="xs" variant="outline" @click="clearCreatePending">已核对，继续</Button><Button size="xs" @click="reviewCreated">查看预约</Button></template><Button size="xs" v-else :disabled="busy" @click="create">{{ busy ? '创建中…' : '创建预约' }}</Button></DialogFooter></DialogContent></Dialog>
    <Dialog :open="promptOpen" @update:open="value => { if (!value) cancelPrompt() }"><DialogContent><DialogHeader><DialogTitle>{{ promptOptions.title }}</DialogTitle><DialogDescription>{{ promptOptions.description }}</DialogDescription></DialogHeader><label v-if="promptOptions.reason">原因（选填）<Input v-model="promptReason" maxlength="255" placeholder="填写原因" /></label><DialogFooter><Button size="xs" variant="outline" @click="cancelPrompt">返回</Button><Button size="xs" @click="confirmPrompt">{{ promptOptions.confirmText }}</Button></DialogFooter></DialogContent></Dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useReservationPrompt } from '@/composables/useReservationPrompt'
import { Card } from '@xa/shadcn-vue/card'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Badge } from '@xa/shadcn-vue/badge'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { Popover, PopoverContent, PopoverTrigger } from '@xa/shadcn-vue/popover'
import { request } from '@/api/request'
import { userApi, escortApi, serviceItemApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'
import { amountToXaCoin } from '@/utils/format'
import { adminWs } from '@/utils/ws'

const auth = useAuthStore(), router = useRouter(), route = useRoute()
const notice = ref('')
const feedback = { warning: (text: string) => { notice.value = text }, error: (text: string) => { notice.value = text }, success: (text: string) => { notice.value = text } }
const { open: promptOpen, options: promptOptions, reason: promptReason, ask: askPrompt, confirm: confirmPrompt, cancel: cancelPrompt } = useReservationPrompt()
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
const createPendingSummary = ref('')
const bosses = ref<any[]>([]), providers = ref<any[]>([]), services = ref<any[]>([])
type OptionType = 'boss' | 'provider' | 'service'
const optionLoading = reactive<Record<OptionType, boolean>>({ boss: false, provider: false, service: false })
const optionKeywords = reactive<Record<OptionType, string>>({ boss: '', provider: '', service: '' })
const optionSeq: Record<OptionType, number> = { boss: 0, provider: 0, service: 0 }
const form = reactive<any>({ customer_account_id: null, provider_account_id: null, service_id: null, start_time: null, end_time: null, game_rounds: 1, game_region: '', game_nickname: '', game_uid: '', remark: '' })
let sequence = 0, detailSeq = 0
let mounted = false, refreshing = false, pendingRefresh = false
let reservationReviewId: number | null = null, reservationReviewInvalid = false, reservationPromptOpen = false
let cancelReservationReview: (() => void) | null = null
let periodicTimer: ReturnType<typeof setInterval> | null = null
let unsubscribe: Array<() => void> = []
const detailRefreshError = ref('')
const requestedId = ref(0), detailReadError = ref('')
let uncertainConversionId: number | null = null
const message = (e: any) => e?.msg || e?.message || '操作失败，请重试'
function reconcileConversion(id: number, status: string) {
  if (uncertainConversionId === id && status === 'CONVERTED') {
    uncertainConversionId = null; feedback.success('预约已转为订单，请查看关联订单。')
  }
}
async function load() { const seq = ++sequence; loading.value = true; error.value = ''; try { const res = await api('', 'get', undefined, { page: page.value, page_size: pageSize, status: status.value, ...(['pending', 'confirmed'].includes(status.value) ? { sort_start: 'asc' } : {}) }); if (seq === sequence) { rows.value = res.data.items; total.value = res.data.total } } catch (e) { if (seq === sequence) error.value = message(e) } finally { if (seq === sequence) loading.value = false } }
function changeStatus() { page.value = 1; const query = { ...route.query }; if (status.value) query.status = status.value; else delete query.status; void router.replace({ query }); void load() }
function changePage(p: number) { page.value = p; void load() }
async function view(id: number) {
  requestedId.value = id; detailReadError.value = ''; detailOpen.value = true
  detail.value = null; detailRefreshError.value = ''; detailLoading.value = true
  const seq = ++detailSeq
  try {
    const result = await api(id + '/')
    if (seq === detailSeq) {
      detail.value = result.data
      reconcileConversion(id, result.data.status)
    }
  } catch (cause) {
    const failure = cause as {response?: {data?: {msg?: string}}; msg?: string}
    if (seq === detailSeq) detailReadError.value = failure.response?.data?.msg || failure.msg || '预约详情暂时无法读取，请重试。'
  } finally {
    if (seq === detailSeq) detailLoading.value = false
  }
}
function invalidateOptions() { for (const type of ['boss', 'provider', 'service'] as const) { ++optionSeq[type]; optionLoading[type] = false } }
async function search(type: OptionType, keyword = '') { const seq = ++optionSeq[type]; optionLoading[type] = true; try { const fn = type === 'boss' ? userApi : type === 'provider' ? escortApi : serviceItemApi; const res = await fn.options({ keyword, page_size: 50, ...(type === 'service' ? { is_active: true } : {}) }, true); if (seq !== optionSeq[type]) return; const list = res.data.list; const current = type === 'boss' ? bosses.value.find(row => row.id === form.customer_account_id) : type === 'provider' ? providers.value.find(row => row.user === form.provider_account_id) : services.value.find(row => row.id === form.service_id); const key = type === 'provider' ? 'user' : 'id'; const merged = current && !list.some((row: any) => row[key] === current[key]) ? [current, ...list] : list; if (type === 'boss') bosses.value = merged; else if (type === 'provider') providers.value = merged; else services.value = merged } catch (e) { if (seq === optionSeq[type]) feedback.error(message(e)) } finally { if (seq === optionSeq[type]) optionLoading[type] = false } }
function createPendingKey() { return auth.profile?.id ? `xa:reservation-create-pending:${auth.profile.id}` : '' }
function readCreatePending(): string | null { try { const key = createPendingKey(); return key ? localStorage.getItem(key) || '' : '' } catch { return null } }
function clearCreatePending() { try { const key = createPendingKey(); if (key) localStorage.removeItem(key) } catch { feedback.warning('无法保存核对结果，请检查浏览器存储'); return }; createUncertain.value = false; createPendingSummary.value = '' }
async function openCreate() { if (!canDispatch.value) return; notice.value = ''; const pending = readCreatePending(); if (pending !== null) { createPendingSummary.value = pending; createUncertain.value = !!pending }; Object.assign(form, { customer_account_id: null, provider_account_id: null, service_id: null, start_time: null, end_time: null, game_rounds: 1, game_region: '', game_nickname: '', game_uid: '', remark: '' }); Object.assign(optionKeywords, { boss: '', provider: '', service: '' }); createOpen.value = true; await Promise.all([search('boss'), search('service'), search('provider')]) }
function reviewCreated() { createOpen.value = false; status.value = ''; changeStatus() }
async function create() {
  if (busy.value || createUncertain.value || !canDispatch.value) return
  if (!form.customer_account_id || !form.provider_account_id || !form.service_id || !form.start_time || !form.end_time) { feedback.warning('请选择老板、服务、陪玩和时间'); return }
  if (![form.start_time, form.end_time].every((value: string) => /^\d{4}-\d{2}-\d{2} \d{2}:(00|15|30|45)$/.test(value))) { feedback.warning('预约时间须按 15 分钟选择'); return }
  if (!Number.isInteger(form.game_rounds) || form.game_rounds < 1 || form.game_rounds > 999) { feedback.warning('购买数量须为 1 至 999 的整数'); return }
  const revision = auth.sessionRevision, pendingKey = createPendingKey()
  let submitted = false
  busy.value = true
  try {
    const payload = { ...form, start_time: reservationInstant(form.start_time), end_time: reservationInstant(form.end_time) }
    const summary = `老板 #${form.customer_account_id} · 陪玩 #${form.provider_account_id} · 服务 #${form.service_id} · ${form.start_time}（北京时间）`
    try { if (pendingKey) localStorage.setItem(pendingKey, summary) } catch { /* The current dialog still retains the warning. */ }
    createPendingSummary.value = summary
    submitted = true
    await api('', 'post', payload)
    try { if (pendingKey) localStorage.removeItem(pendingKey) } catch {}
    if (revision !== auth.sessionRevision) return
    feedback.success('预约已创建，等待确认'); createOpen.value = false; page.value = 1; await load()
  } catch (e: any) {
    if (revision !== auth.sessionRevision) return
    const httpStatus = e?.response?.status
    if (submitted && ((!e?.response && typeof e?.code !== 'number') || httpStatus >= 500)) createUncertain.value = true
    else { try { if (pendingKey) localStorage.removeItem(pendingKey) } catch {}; createPendingSummary.value = ''; feedback.error(message(e)) }
  } finally { busy.value = false }
}
async function action(name: 'confirm' | 'reject' | 'cancel') {
  if (!detail.value || busy.value || !(name === 'cancel' ? canCancel.value : canDispatch.value) || !detail.value.allowed_actions.includes(name)) return
  const id = detail.value.id, revision = auth.sessionRevision
  reservationReviewId = id; reservationReviewInvalid = false; reservationPromptOpen = true; busy.value = true
  try {
    const invalidated = new Promise<null>(resolve => { cancelReservationReview = () => resolve(null) })
    const answer = await Promise.race([askPrompt({ title: name === 'confirm' ? '确认预约' : name === 'cancel' ? '取消预约' : '拒绝预约', description: name === 'confirm' ? '确认该预约？' : name === 'cancel' ? '可填写取消原因。' : '可填写拒绝原因。', confirmText: '确认', reason: name !== 'confirm' }), invalidated])
    reservationPromptOpen = false
    if (answer === null || reservationReviewInvalid || revision !== auth.sessionRevision || !detailOpen.value || detail.value?.id !== id || !(name === 'cancel' ? canCancel.value : canDispatch.value)) return
    const reason = answer
    reservationReviewId = null
    await api(`${id}/${name}/`, 'post', { reason })
    feedback.success(name === 'confirm' ? '预约已确认' : name === 'cancel' ? '预约已取消' : '预约已拒绝')
    await Promise.all([load(), view(id)])
  } catch (e) {
    if (e !== 'cancel' && e !== 'close' && revision === auth.sessionRevision) { feedback.error(message(e)); await view(id) }
  } finally {
    const invalidated = reservationReviewInvalid
    reservationReviewId = null; reservationPromptOpen = false; cancelReservationReview = null; busy.value = false
    if (invalidated && mounted && revision === auth.sessionRevision && detailOpen.value && detail.value?.id === id) await view(id)
  }
}
async function convert() {
  if (!detail.value || busy.value || !canDispatch.value || !detail.value.allowed_actions.includes('convert') || !auth.profile?.id) return
  const id = detail.value.id, reservationNo = detail.value.reservation_no, revision = auth.sessionRevision, accountId = auth.profile.id
  let submitted = false
  reservationReviewId = id; reservationReviewInvalid = false; busy.value = true
  try {
    const quote = (await api(`${id}/quote/`)).data
    if (reservationReviewInvalid || revision !== auth.sessionRevision || !detailOpen.value || detail.value?.id !== id || !canDispatch.value) return
    if (!quote.balance_sufficient) { feedback.warning('老板余额不足或钱包不可用，请先核对'); return }
    const platform = ({ PC: '端游', MOBILE: '手游' } as Record<string, string>)[quote.service?.platform] || ''
    const difficulty = ({ CONFIDENTIAL: '机密', TOP_SECRET: '绝密', ANY: '不限难度' } as Record<string, string>)[quote.service?.security_level] || ''
    const service = [quote.service?.name || '服务待核对', platform, difficulty].filter(Boolean).join(' · ')
    const payment = quote.amount === 0 ? '本单应付 0 币，无需扣款' : `将扣老板余额 ${amountToXaCoin(quote.amount)} 币`
    reservationPromptOpen = true
    const invalidated = new Promise<null>(resolve => { cancelReservationReview = () => resolve(null) })
    const answer = await Promise.race([askPrompt({ title: '确认预约转单', description: `服务：${service}，数量 ${detail.value.game_rounds}。老板：${detail.value.customer.name || '待核对'}；陪玩：${detail.value.provider.name || '待核对'}。时间：${reservationDate(detail.value.start_time)} 至 ${reservationDate(detail.value.end_time)}（北京时间）。${payment}；转单后立即进入已接单状态。`, confirmText: '确认转单' }), invalidated])
    reservationPromptOpen = false
    if (answer === null || reservationReviewInvalid || revision !== auth.sessionRevision || !detailOpen.value || detail.value?.id !== id || !canDispatch.value) return
    reservationReviewId = null // A committed request is reconciled by its own response.
    const key = `xa:reservation-convert:${accountId}:${id}`
    let requestId = sessionStorage.getItem(key)
    if (!requestId) { requestId = crypto.randomUUID(); sessionStorage.setItem(key, requestId) }
    submitted = true
    await api(`${id}/convert/`, 'post', { client_request_id: requestId, expected_amount: quote.amount, expected_service: quote.service })
    uncertainConversionId = null
    sessionStorage.removeItem(key); feedback.success('预约已转为订单'); await Promise.all([load(), view(id)])
  } catch (e: any) {
    if (e === 'cancel' || e === 'close' || revision !== auth.sessionRevision) return
    const status = e?.response?.status
    if (submitted && ((!e?.response && typeof e?.code !== 'number') || status >= 500 || (typeof e?.code === 'number' && e.code >= 500))) {
      uncertainConversionId = id
      feedback.error(`预约 ${reservationNo} 转单结果未确认，请重新读取预约详情并核对关联订单。`)
    } else feedback.error(message(e))
    await Promise.all([load(), view(id)])
  } finally {
    const invalidated = reservationReviewInvalid
    reservationReviewId = null; reservationPromptOpen = false; cancelReservationReview = null; busy.value = false
    if (invalidated && mounted && revision === auth.sessionRevision && detailOpen.value && detail.value?.id === id) await view(id)
  }
}
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
      reconcileConversion(id, res.data.status)
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
watch(() => auth.sessionRevision, () => { invalidateReservationReview(false); ++sequence; ++detailSeq; invalidateOptions(); pendingRefresh = false; uncertainConversionId = null; notice.value = ''; rows.value = []; detail.value = null; detailRefreshError.value = ''; detailOpen.value = false; createOpen.value = false; createUncertain.value = false; createPendingSummary.value = ''; if (auth.hasPerm('order:view')) void requestRefresh() })
function invalidateReservationReview(notify = true) {
  if (reservationReviewId !== null && !reservationReviewInvalid) {
    reservationReviewInvalid = true
    if (reservationPromptOpen) { cancelReservationReview?.(); cancelPrompt() }
    if (notify) feedback.warning('预约状态可能已变化，请重新核对后操作。')
  }
}
function onReservationUpdate(data: any) {
  if (reservationReviewId !== null && Number(data?.reservation_id) === reservationReviewId) invalidateReservationReview()
  void requestRefresh()
}
onMounted(() => { mounted = true; unsubscribe = [adminWs.on('reservation_status_update', onReservationUpdate), adminWs.on('connected', () => { invalidateReservationReview(); void requestRefresh() })]; document.addEventListener('visibilitychange', onVisibilityChange); periodicTimer = setInterval(() => { if (!document.hidden) void requestRefresh() }, 60000); void requestRefresh(); adminWs.connect() })
onUnmounted(() => { mounted = false; invalidateReservationReview(false); ++sequence; ++detailSeq; invalidateOptions(); unsubscribe.forEach(off => off()); if (periodicTimer) clearInterval(periodicTimer); document.removeEventListener('visibilitychange', onVisibilityChange) })
</script>

<style scoped>
.reservation-page { min-width:0; container:reservations / inline-size; }
.panel { display:grid; grid-template-columns:minmax(0,1fr); gap:12px; padding:16px; border:1px solid var(--border); border-radius:12px; box-shadow:none; }
.page-heading,.entry-top,.detail-head,.filter,.actions,.pager,.search-row { display:flex; align-items:center; gap:8px; }
.page-heading,.entry-top,.detail-head { justify-content:space-between; }
.page-heading,.filter,.actions,.pager { flex-wrap:wrap; }
h2,h3,p,dl,dd,ul { margin:0; }
h2 { font-size:16px; line-height:24px; font-weight:600; }
h3,.detail-head strong { font-size:14px; line-height:20px; font-weight:600; }
.hint,dt,.feedback,.empty,.reservation-form label,.pager { font-size:12px; line-height:16px; }
.hint,dt,.pager { color:var(--muted-foreground); }
.page-heading .hint,.entry-top+.hint { margin-top:4px; }
.feedback,.empty { padding:8px 12px; border-radius:8px; background:var(--muted); }
.list { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); align-items:start; gap:8px; padding:0; list-style:none; }
.entry { display:grid; min-width:0; gap:4px; padding:12px; border:1px solid var(--border); border-radius:8px; overflow-wrap:anywhere; }
.entry-top { align-items:start; }
.entry-top :deep([data-slot='badge']),.detail-head :deep([data-slot='badge']) { flex-shrink:0; }
.slot,.people,.details { display:grid; min-width:0; gap:4px; }
.slot { margin-top:4px; padding-top:8px; border-top:1px solid var(--border); }
.people { margin:4px 0; }
.slot div,.people div,.details div { display:grid; align-items:baseline; grid-template-columns:36px minmax(0,1fr); gap:4px; }
.slot dd,.people dd,.details dd { font-size:13px; line-height:20px; overflow-wrap:anywhere; }
.slot dd { font-variant-numeric:tabular-nums; }
.pager { justify-content:flex-end; }
:global(.reservation-detail),:global(.reservation-dialog) { max-height:calc(100dvh - 32px); overflow:auto; width:min(640px,calc(100vw - 24px)); }
.details { gap:0; }
.details div { grid-template-columns:80px minmax(0,1fr); padding:8px 0; border-top:1px solid var(--border); }
.details dd { white-space:pre-wrap; }
.reservation-form { display:grid; min-width:0; gap:12px; }
.reservation-form label,.option-field { display:grid; min-width:0; gap:4px; } .option-field>span { font-size:12px; line-height:16px; }
.search-row :deep(input) { min-width:0; }
.form-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
.reservation-form :deep([data-slot='native-select-wrapper']) { width:100%; }
.reservation-form :deep(input),.reservation-form :deep(select),.filter :deep(select) { height:28px; min-height:28px; font-size:12px; padding-block:4px; }
@container reservations (max-width:900px) { .panel { padding:12px; } .list { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@container reservations (max-width:520px) { .list { grid-template-columns:minmax(0,1fr); } .pager { justify-content:flex-start; } }
@media(max-width:600px) { .form-grid { grid-template-columns:minmax(0,1fr); } }
@media(pointer:coarse) { .panel :deep(button),.filter :deep(select),.reservation-form :deep(input),.reservation-form :deep(select) { min-height:44px; } .panel :deep(button) { min-width:44px; } }
</style>
