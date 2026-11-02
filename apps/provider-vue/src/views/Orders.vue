<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { formatXaCoin } from '@xa/money'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Input } from '@xa/shadcn-vue/input'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { api, clearSession, currentUser } from '../lib/session'
import { coalesced, onRealtime } from '../lib/realtime'
import { loadPending, loadPendingAfterCurrent, onPoolUpdate, setSoundEnabled, soundEnabled } from '../lib/orderAlerts'

type Application = { id: number; team_size: number; status: string; ready: boolean; is_partner: boolean; partner_confirmed: boolean; members: { id: number; nickname: string; level: string }[] }
type TeamProgress = { stage: 'ENTRY' | 'COMPLETION'; submitted_count: number; total_count: number; members: { id: number; nickname: string; submitted: boolean }[] }
type RefundSummary = { amount: number; remaining_amount?: number | null; recovered_amount?: number | null; net_income?: number | null; referral_recovered_amount?: number | null; referral_net_income?: number | null; remaining_frozen_amount?: number | null; income_breakdown_unavailable?: boolean }
type Order = { id: number; order_no: string; status: string; amount: number; payment_status?: string; cancel_reason?: string; aftersale_status?: string; refund_summary?: RefundSummary | null; settlement_status?: string; settled_at?: string | null; fulfillment?: string; gameplay_snapshot?: { rules?: string } | null; reservation_origin?: { id: number; start_time: string; end_time: string; can_open: boolean } | null; my_assignment_released?: boolean | null; my_assignment_settled?: boolean | null; team_progress?: TeamProgress | null; participants?: { id: number; nickname: string }[]; service_name_snapshot?: string; service?: { name?: string }; expected_income: number | null; remark?: string; customer?: { nickname?: string }; game_rounds: number; game_region?: string; game_nickname?: string; game_uid?: string; quantity_unit?: string; allowed_actions?: string[]; can_operate?: boolean; can_reject?: boolean; requires_boss_selection?: boolean; required_team_size?: number; can_team_apply?: boolean; dispatch_route?: string; my_applications?: Application[]; request_replayed?: boolean }
type Teammate = { id: number; nickname: string; level: string }
type Stats = { today_orders: number; serving: number; completion_rate: number; total_income: number; active_orders: number; max_concurrent_orders: number }
type Action = 'grab' | 'start' | 'complete' | 'reject' | 'confirm_team' | 'withdraw'
const router = useRouter(), route = useRoute()
const tabs = [{ id: 'pending', label: '抢单池' }, { id: 'grabbed', label: '已接单' }, { id: 'in_progress', label: '服务中' }, { id: 'completed', label: '已完成' }, { id: 'cancelled', label: '已取消' }]
const labels: Record<string,string> = { PENDING:'待接单', GRABBED:'已接单', IN_SERVICE:'服务中', COMPLETED:'已完成', CANCELLED:'已取消' }
const selected = ref('pending'), orders = ref<Order[]>([]), stats = ref<Stats | null>(null)
const loading = ref(false), error = ref(''), feedback = ref(''), busy = ref(false)
const sound = ref(soundEnabled())
const action = ref<Action | null>(null), target = ref<Order | null>(null), application = ref<Application | null>(null)
const teammates = ref<Teammate[]>([]), teammateLoading = ref(false), partner = ref<string | number>('')
const reason = ref(''), image = ref<File | null>(null)
let sequence = 0, teamSequence = 0
const dialogOpen = computed({ get: () => !!action.value, set: value => { if (!value && !busy.value) closeDialog() } })
const title = computed(() => ({ grab: target.value?.requires_boss_selection ? '报名抢单' : '确认接单', start: '开始服务', complete: '完成订单', reject: '拒单原因', confirm_team: '确认组队', withdraw: '撤回报名' }[action.value || 'grab']))
const needsTeam = computed(() => target.value?.requires_boss_selection && target.value.required_team_size === 2)
const actionDisabled = computed(() => busy.value || teammateLoading.value || (action.value === 'grab' && !!needsTeam.value && !partner.value) || ((action.value === 'start' || action.value === 'complete') && !image.value) || (action.value === 'reject' && !reason.value.trim()))
const description = computed(() => {
  if (action.value === 'grab') return target.value?.requires_boss_selection ? (needsTeam.value ? '双陪需要两名陪玩组队报名；队友确认、老板选定后正式接单。' : '提交报名后由老板选择陪玩。') : '确认接单后订单进入已接单。'
  if (action.value === 'start') return '请上传入队截图。双陪订单以双方实际提交进度为准。'
  if (action.value === 'complete') return '请上传结单截图。双陪订单以双方实际提交进度为准。'
  if (action.value === 'reject') return target.value?.dispatch_route === 'STAFF' ? '拒单后订单退回客服重新安排。' : target.value?.requires_boss_selection ? '拒单后订单回到公开池，由老板选择报名陪玩。' : '拒单后订单回到公开池，其他陪玩可直接接单。'
  return action.value === 'confirm_team' ? '确认后等待老板选择。' : '撤回后将不再参与本次报名。'
})
async function load(tab = selected.value, afterCurrent = false) {
  selected.value = tab; const version = ++sequence; loading.value = true; error.value = ''
  try {
    const id = String(route.query.order || '')
    const data = /^[1-9]\d*$/.test(id) ? [await api<Order>(`/orders/orders/${id}/`)] : tab === 'pending' ? await (afterCurrent ? loadPendingAfterCurrent<Order>() : loadPending<Order>()) : await api<Order[]>(`/orders/orders/?status=${encodeURIComponent(tab)}&role=provider`)
    if (version === sequence) orders.value = data
  }
  catch (cause) { if (version === sequence) { orders.value = []; error.value = cause instanceof Error ? cause.message : '订单加载失败' } }
  finally { if (version === sequence) loading.value = false }
}
async function selectTab(tab: string) { if (route.query.order) await router.replace({ path: '/orders' }); await load(tab) }
async function loadStats() { try { stats.value = await api<Stats>('/users/provider-stats/') } catch { stats.value = null } }
function can(order: Order, name: string, fallback = false) { return Array.isArray(order.allowed_actions) ? order.allowed_actions.includes(name) : fallback }
function reservationDate(value: string) { const time = new Date(value); return Number.isNaN(time.getTime()) ? '—' : time.toLocaleString('zh-CN', { timeZone:'Asia/Shanghai', hour12:false }) }
function canShowGameIdentity(order: Order) {
  return ['GRABBED', 'IN_SERVICE'].includes(order.status) && order.my_assignment_released !== true
    && (order.my_assignment_released === false || order.can_operate === true
      || !!order.participants?.some(member => String(member.id) === String(currentUser.value?.id)))
}
function closeDialog() { teamSequence++; teammateLoading.value = false; action.value = null; target.value = null; application.value = null; teammates.value = []; partner.value = ''; image.value = null; reason.value = '' }
async function openAction(next: Action, order: Order, item?: Application) {
  if (busy.value) return
  feedback.value = ''; target.value = order; application.value = item || null; action.value = next
  if (next !== 'grab' || !needsTeam.value) return
  if (!order.can_team_apply) { feedback.value = '当前订单暂不可组队，请刷新订单或联系客服'; closeDialog(); return }
  const version = ++teamSequence; teammateLoading.value = true
  try { const result = await api<Teammate[]>(`/orders/orders/${order.id}/teammates/`); if (version === teamSequence) teammates.value = result }
  catch (cause) { if (version === teamSequence) feedback.value = cause instanceof Error ? cause.message : '队友加载失败' }
  finally { if (version === teamSequence) teammateLoading.value = false }
}
function onFile(event: Event) { image.value = (event.target as HTMLInputElement).files?.[0] || null }
async function submit() {
  const order = target.value, operation = action.value
  if (!order || !operation || actionDisabled.value) return
  busy.value = true; feedback.value = ''
  try {
    let grabbed: Order | null = null
    if (operation === 'grab') { const bytes = crypto.getRandomValues(new Uint8Array(16)); grabbed = await api<Order>(`/orders/orders/${order.id}/grab/`, { method:'POST', data:{ team_size: needsTeam.value ? 2 : 1, ...(needsTeam.value ? { partner_id: Number(partner.value) } : {}), client_request_id: `grab-${Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')}` } }) }
    else if (operation === 'confirm_team' || operation === 'withdraw') await api(`/orders/orders/${order.id}/applications/${application.value?.id}/action/`, { method:'POST', data:{ action: operation === 'confirm_team' ? 'CONFIRM_TEAM' : 'WITHDRAW' } })
    else if (operation === 'reject') await api(`/orders/orders/${order.id}/reject/`, { method:'POST', data:{ reason: reason.value.trim() } })
    else {
      const form = new FormData(); form.append(operation === 'start' ? 'entry_image' : 'completion_image', image.value!)
      const bytes = crypto.getRandomValues(new Uint8Array(16)); form.append('client_request_id', `fulfillment-${Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')}`)
      await api(`/orders/orders/${order.id}/${operation === 'start' ? 'start-service' : 'complete'}/`, { method:'POST', data:form })
    }
    feedback.value = operation === 'grab' ? (grabbed?.request_replayed && (order.requires_boss_selection || !can(grabbed, 'START', !!grabbed.can_operate)) ? '本次请求已处理，请核对订单最新状态。' : order.requires_boss_selection ? '报名已提交' : '接单成功') : operation === 'reject' ? '已拒单' : operation === 'withdraw' ? '已撤回报名' : operation === 'confirm_team' ? '队伍已确认' : '截图已提交，请核对订单状态'
    closeDialog(); await Promise.all([load(), loadStats()])
  } catch (cause) {
    const { status, code } = cause as {status?: number;code?: number}
    const message = (cause as Error).message
    feedback.value = status && status < 500
      ? operation === 'grab' && /^非法状态转换: [A-Z_]+ -> GRABBED$/.test(message) ? '订单状态已变化，请查看最新抢单池。' : message
      : '操作结果未确认，请刷新订单后核对。'
    if (!status || status >= 500) { closeDialog(); await load() }
    else if (operation === 'grab' && (!code || code < 500)) {
      await Promise.all([load(selected.value, true), loadStats()])
      if (!error.value && !orders.value.some(row => row.id === order.id && row.status === 'PENDING')) closeDialog()
    }
  } finally { busy.value = false }
}
function logout() { clearSession(); void router.replace('/login') }
const refreshOrders = coalesced(() => load())
const refreshStats = coalesced(loadStats)
const offOrder = onRealtime('order_status_update', data => {
  if (document.hidden) return
  refreshOrders.request()
  if (data && typeof data === 'object' && Object.prototype.hasOwnProperty.call(data, 'provider')) refreshStats.request()
})
const offConnected = onRealtime('connected', () => { if (!document.hidden) { refreshOrders.request(); refreshStats.request() } })
const offPool = onPoolUpdate<Order>(rows => { if (!document.hidden && selected.value === 'pending' && !route.query.order) { orders.value = rows; error.value = '' } })
function onVisible() { if (!document.hidden) { refreshOrders.request(); refreshStats.request() } }
onMounted(() => { void Promise.all([load(), loadStats()]); document.addEventListener('visibilitychange', onVisible) })
onUnmounted(() => { sequence++; teamSequence++; offOrder(); offConnected(); offPool(); refreshOrders.dispose(); refreshStats.dispose(); document.removeEventListener('visibilitychange', onVisible) })
</script>

<template>
  <main class="page stack">
    <header class="row"><div><p class="muted">兴安电竞 · 陪玩端</p><h1>接单工作台</h1></div><div class="actions"><Button variant="outline" @click="router.push('/reports')">报单</Button><Button variant="outline" @click="router.push('/messages')">消息</Button><Button variant="outline" @click="router.push('/wallet')">钱包</Button><Button variant="outline" @click="router.push('/mine')">我的</Button><Button variant="outline" @click="logout">退出登录</Button></div></header>
    <div class="row"><p class="muted">{{ currentUser?.nickname }}，查看当前订单与服务进度。</p><div class="actions"><Button variant="outline" @click="router.push('/pass')">接单通行证</Button><Button variant="outline" @click="router.push('/reservations')">我的预约</Button><Button variant="outline" :aria-pressed="sound" @click="sound = !sound; setSoundEnabled(sound)">{{ sound ? '提醒声音已开' : '提醒声音已关' }}</Button></div></div>
    <section v-if="stats" class="stats" aria-label="接单概况"><div><strong>{{ stats.today_orders }}</strong><span>今日接单</span></div><div><strong>{{ stats.serving }}</strong><span>服务中</span></div><div><strong>{{ stats.completion_rate }}%</strong><span>完成率</span></div><div><strong>{{ formatXaCoin(stats.total_income) }} 币</strong><span>服务及推荐净收益</span></div></section>
    <nav class="tabs" aria-label="订单状态"><Button v-for="tab in tabs" :key="tab.id" :variant="!route.query.order && tab.id === selected ? 'default' : 'outline'" :aria-pressed="!route.query.order && tab.id === selected" @click="selectTab(tab.id)">{{ tab.label }}</Button></nav>
    <div class="row"><h2>{{ route.query.order ? '关联订单' : tabs.find(item => item.id === selected)?.label }}</h2><Button variant="outline" :disabled="loading" @click="load()">{{ loading ? '刷新中…' : '刷新' }}</Button></div>
    <p v-if="feedback" role="status" class="feedback">{{ feedback }}</p><p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-else-if="loading" class="muted">正在加载订单…</p><Card v-else-if="!orders.length" class="panel"><p class="muted">暂无订单</p></Card>
    <div v-else class="stack"><Card v-for="order in orders" :key="order.id" class="panel order-card">
      <div class="row"><strong>{{ order.service_name_snapshot || order.service?.name || '陪玩服务' }}</strong><span class="muted">{{ labels[order.status] || order.status }}</span></div>
      <p v-if="order.my_assignment_released === true" class="muted">本人已转出</p>
      <p class="muted">{{ order.order_no }} · {{ order.customer?.nickname || '老板' }}</p><p>{{ order.game_rounds }} {{ order.quantity_unit || '份' }}<template v-if="order.expected_income != null"> · {{ order.status === 'CANCELLED' || ['REFUNDED', 'PARTIALLY_REFUNDED'].includes(order.payment_status || '') ? '原预计' : '预计' }} {{ formatXaCoin(order.expected_income) }} 币</template><template v-else> · 收益待核对</template></p>
      <div v-if="order.fulfillment || order.gameplay_snapshot?.rules" class="gameplay-snapshot"><p v-if="order.fulfillment">履约目标：{{ order.fulfillment }}</p><p v-if="order.gameplay_snapshot?.rules">规则快照：{{ order.gameplay_snapshot.rules }}</p></div>
      <div v-if="order.reservation_origin" class="reservation-origin"><p class="muted">原预约时段（北京时间）：{{ reservationDate(order.reservation_origin.start_time) }} 至 {{ reservationDate(order.reservation_origin.end_time) }}</p><Button v-if="order.reservation_origin.can_open" variant="outline" @click="router.push({path:'/reservations',query:{id:order.reservation_origin.id}})">查看预约</Button></div>
      <p v-if="order.aftersale_status === 'OPEN'" class="muted">{{ order.settled_at ? '售后处理中，已结算收益请查看钱包流水。' : '售后处理中，本单暂不结算。' }}</p>
      <p v-else-if="order.aftersale_status === 'RESOLVED'" class="muted">售后已处理，请查看结算状态。</p>
      <p v-if="order.payment_status === 'PARTIALLY_REFUNDED'" class="muted">订单部分退款</p><p v-else-if="order.payment_status === 'REFUNDED' && order.amount !== 0" class="muted">订单已退款</p>
      <p v-if="order.status === 'CANCELLED' && order.cancel_reason" class="muted">{{ order.payment_status === 'REFUNDED' && order.amount !== 0 ? '退款原因' : '取消原因' }}：{{ order.cancel_reason }}</p>
      <div v-if="order.refund_summary && order.refund_summary.amount > 0" class="refund-summary"><p>已退老板：{{ formatXaCoin(order.refund_summary.amount) }} 币</p><p v-if="order.refund_summary.remaining_amount != null">订单剩余金额：{{ formatXaCoin(order.refund_summary.remaining_amount) }} 币</p><template v-if="order.settled_at"><p v-if="order.refund_summary.income_breakdown_unavailable || order.refund_summary.recovered_amount == null || order.refund_summary.net_income == null">本人收益明细待核对，请查看钱包流水或联系客服。</p><template v-else><p>本人服务收益已追回：{{ formatXaCoin(order.refund_summary.recovered_amount) }} 币</p><p>本人服务收益流水净额：{{ formatXaCoin(order.refund_summary.net_income) }} 币</p><p v-if="order.refund_summary.referral_recovered_amount">本人推荐收益已追回：{{ formatXaCoin(order.refund_summary.referral_recovered_amount) }} 币</p><p v-if="order.refund_summary.referral_net_income">本人推荐收益流水净额：{{ formatXaCoin(order.refund_summary.referral_net_income) }} 币</p><p v-if="order.refund_summary.remaining_frozen_amount != null">本单剩余冻结：{{ formatXaCoin(order.refund_summary.remaining_frozen_amount) }} 币</p></template><p>可用和冻结金额请查看钱包。</p></template></div>
      <p v-if="order.settlement_status === 'REVIEW'" class="muted">结算状态待核对，请查看钱包流水。</p><p v-else-if="order.settlement_status === 'SETTLED'" class="muted">订单已结算，个人到账请查看钱包流水。</p><p v-else-if="order.settlement_status === 'WAITING' && !order.settled_at" class="muted">订单尚未结算，到账请查看钱包流水。</p>
      <p v-if="order.my_assignment_released === true && order.my_assignment_settled === true" class="muted">本人转单份额已结算，实际到账请查看钱包流水。</p><p v-else-if="order.my_assignment_released === true && order.my_assignment_settled === false" class="muted">本人转单份额尚未结算，请查看钱包流水。</p>
      <div v-if="order.team_progress" class="team-progress"><strong>{{ order.team_progress.stage === 'ENTRY' ? '入队截图' : '结单截图' }} · {{ order.team_progress.submitted_count }}/{{ order.team_progress.total_count }} 已提交</strong><p v-for="member in order.team_progress.members" :key="member.id" class="muted">{{ member.nickname }} · {{ member.submitted ? '已提交' : '等待提交' }}</p></div>
      <div v-if="(order.status === 'PENDING' && order.game_region) || (canShowGameIdentity(order) && (order.game_region || order.game_nickname || order.game_uid))" class="game-context"><p v-if="order.game_region">游戏区服：{{ order.game_region }}</p><template v-if="canShowGameIdentity(order)"><p v-if="order.game_nickname">游戏昵称：{{ order.game_nickname }}</p><p v-if="order.game_uid">游戏 UID：{{ order.game_uid }}</p></template></div>
      <p v-if="order.remark" class="muted">{{ order.remark }}</p><div><Button variant="outline" @click="router.push({path:'/support-chat',query:{order_no:order.order_no}})">咨询客服</Button></div>
      <template v-if="order.status === 'PENDING' && order.my_applications?.some(item => item.status === 'PENDING')"><div v-for="item in order.my_applications.filter(row => row.status === 'PENDING')" :key="item.id" class="application"><p class="muted">{{ item.members.map(member => member.nickname + ' · ' + member.level).join(' + ') }} · {{ item.ready ? item.team_size === 2 ? '组队已确认' : '报名已提交' : '等待队友确认' }}</p><div class="actions"><Button variant="outline" :disabled="busy" @click="router.push({path:`/applications/${order.id}`,query:{application:item.id}})">查看报名状态</Button><Button v-if="item.is_partner && !item.partner_confirmed" :disabled="busy" @click="openAction('confirm_team', order, item)">确认组队</Button><Button variant="outline" :disabled="busy" @click="openAction('withdraw', order, item)">撤回报名</Button></div></div></template>
      <div v-else class="actions"><Button v-if="order.status === 'PENDING' && can(order, 'GRAB', true)" :disabled="busy || !!(stats && stats.max_concurrent_orders > 0 && stats.active_orders >= stats.max_concurrent_orders)" @click="openAction('grab', order)">{{ order.requires_boss_selection ? '报名抢单' : '抢单' }}</Button><template v-if="order.status === 'GRABBED' && can(order, 'START', !!order.can_operate)"><Button v-if="can(order, 'REJECT', !!order.can_reject)" variant="outline" :disabled="busy" @click="openAction('reject', order)">拒单</Button><Button :disabled="busy" @click="openAction('start', order)">开始服务</Button></template><Button v-if="order.status === 'IN_SERVICE' && can(order, 'COMPLETE', !!order.can_operate)" :disabled="busy" @click="openAction('complete', order)">完成订单</Button></div>
    </Card></div>
    <Dialog v-model:open="dialogOpen"><DialogContent :show-close-button="!busy"><DialogHeader><DialogTitle>{{ title }}</DialogTitle><DialogDescription>{{ description }}</DialogDescription></DialogHeader>
      <label v-if="action === 'grab' && needsTeam" class="field">{{ teammateLoading ? '正在加载可组队陪玩…' : '选择另一名陪玩' }}<NativeSelect v-model="partner" :disabled="busy || teammateLoading || !teammates.length"><NativeSelectOption value="">请选择队友</NativeSelectOption><NativeSelectOption v-for="person in teammates" :key="person.id" :value="person.id">{{ person.nickname }} · {{ person.level }}</NativeSelectOption></NativeSelect><span v-if="!teammateLoading && !teammates.length" class="muted">暂无符合条件的队友</span></label>
      <label v-if="action === 'start' || action === 'complete'" class="field">{{ action === 'start' ? '入队截图' : '结单截图' }}<Input type="file" accept="image/*" :disabled="busy" @change="onFile" /></label>
      <label v-if="action === 'reject'" class="field">拒单原因<Textarea v-model="reason" maxlength="200" placeholder="请说明原因" :disabled="busy" /></label>
      <p v-if="feedback" class="error" role="alert">{{ feedback }}</p><DialogFooter><Button variant="outline" :disabled="busy" @click="closeDialog">取消</Button><Button :disabled="actionDisabled" @click="submit">{{ busy ? '处理中…' : action === 'grab' && target?.requires_boss_selection ? '提交报名' : '确认' }}</Button></DialogFooter>
    </DialogContent></Dialog>
  </main>
</template>

<style scoped>
h1 { margin: 4px 0 0; font-size: 24px; } h2 { margin: 0; font-size: 16px; }
p { margin: 0; } .tabs { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; }
.order-card { gap: 8px; } .order-card p { overflow-wrap: anywhere; }
.team-progress { display:grid; gap:4px; padding:8px; background:var(--secondary); border-radius:8px; font-size:12px; }
.game-context { display:grid; gap:4px; font-size:12px; }
.gameplay-snapshot { display:grid; gap:4px; white-space:pre-wrap; }
.refund-summary { display:grid; gap:4px; font-size:12px; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.stats div { padding: 12px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.stats strong, .stats span { display: block; } .stats span { margin-top: 4px; font-size: 12px; color: var(--muted-foreground); }
.actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; } .application { border-top: 1px solid var(--border); padding-top: 8px; }
.feedback { padding: 8px 12px; border-radius: 8px; background: #eff6ff; color: #1e3a8a; font-size: 13px; }
@media (max-width: 600px) { .stats { grid-template-columns: repeat(2, 1fr); } }
</style>
