<template>
  <div class="db-page">
    <div class="ap-page-head">
      <div><p class="ap-page-intro">订单处理与经营概览</p><p class="db-snapshot">{{ snapshotText }}</p></div>
      <div class="ap-page-head-actions">
        <el-button :icon="Refresh" :loading="loading" @click="requestRefresh()">刷新数据</el-button>
        <el-button v-if="auth.hasPerm('order:view')" type="primary" @click="router.push('/orders')">查看订单</el-button>
      </div>
    </div>

    <el-alert v-if="loadError" type="error" :closable="false" show-icon :title="stats ? '刷新失败，当前显示上次成功获取的数据' : '工作台数据加载失败'" :description="loadError">
      <el-button link type="primary" :disabled="loading" @click="requestRefresh()">重新加载</el-button>
    </el-alert>
    <el-skeleton v-if="loading && !stats" :rows="6" animated />
    <template v-else-if="stats">
      <section v-if="auth.hasPerm('order:view') || (auth.hasPerm('report:view') && typeof stats.pending_report_total === 'number') || (auth.hasPerm('withdraw:view') && typeof stats.pending_withdraw_total === 'number') || (auth.hasPerm('chat:view') && typeof stats.unread_chat_session_total === 'number')" class="db-work" aria-label="待处理事项">
        <div><h2>待处理</h2><p>数量为读取时快照，操作前请核对最新状态</p></div>
        <div class="db-work-items">
          <button v-if="auth.hasPerm('chat:view') && typeof stats.unread_chat_session_total === 'number'" class="db-work-item" @click="router.push({ path: '/chat', query: { only_unread: '1' } })">
            <span>未读客服会话<small>查看待处理咨询</small></span><strong>{{ count(stats.unread_chat_session_total) }} <small>条</small></strong><span aria-hidden="true">›</span>
          </button>
          <button v-if="auth.hasPerm('order:view')" class="db-work-item" @click="router.push({ path: '/orders', query: { queue: 'paid_pending' } })">
            <span>已支付待接单<small>不代表均可派单</small></span><strong>{{ count(stats.pending_paid_total) }} <small>单</small></strong><span aria-hidden="true">›</span>
          </button>
          <button v-if="auth.hasPerm('order:view') && typeof stats.open_aftersale_order_total === 'number'" class="db-work-item" @click="router.push({ path: '/orders', query: { queue: 'open_aftersale' } })">
            <span>处理中售后<small>查看相关订单</small></span><strong>{{ count(stats.open_aftersale_order_total) }} <small>单</small></strong><span aria-hidden="true">›</span>
          </button>
          <button v-if="auth.hasPerm('order:view') && typeof stats.pending_reservation_total === 'number'" class="db-work-item" @click="router.push({ path: '/reservations', query: { status: 'pending' } })">
            <span>待确认预约<small>等待陪玩或客服确认</small></span><strong>{{ count(stats.pending_reservation_total) }} <small>条</small></strong><span aria-hidden="true">›</span>
          </button>
          <button v-if="auth.hasPerm('order:view') && typeof stats.confirmed_reservation_total === 'number'" class="db-work-item" @click="router.push({ path: '/reservations', query: { status: 'confirmed' } })">
            <span>已确认待转单<small>核对服务安排和报价</small></span><strong>{{ count(stats.confirmed_reservation_total) }} <small>条</small></strong><span aria-hidden="true">›</span>
          </button>
          <button v-if="auth.hasPerm('report:view') && typeof stats.pending_report_total === 'number'" class="db-work-item" @click="router.push({ path: '/reports', query: { status: 'PENDING' } })">
            <span>待审核报单<small>包含订单状态暂不能通过的记录</small></span><strong>{{ count(stats.pending_report_total) }} <small>条</small></strong><span aria-hidden="true">›</span>
          </button>
          <button v-if="auth.hasPerm('report:view') && typeof stats.pending_report_order_blocked_total === 'number'" class="db-work-item" @click="router.push({ path: '/reports', query: { status: 'PENDING', order_blocked: '1' } })">
            <span>订单状态待核对<small>待审核报单中不能通过的记录</small></span><strong>{{ count(stats.pending_report_order_blocked_total) }} <small>条</small></strong><span aria-hidden="true">›</span>
          </button>
          <button v-if="auth.hasPerm('withdraw:view') && typeof stats.pending_withdraw_total === 'number'" class="db-work-item" @click="router.push({ path: '/withdrawals', query: { status: 'PENDING' } })">
            <span>待审核提现<small>核对申请及打款情况</small></span><strong>{{ count(stats.pending_withdraw_total) }} <small>笔</small></strong><span aria-hidden="true">›</span>
          </button>
        </div>
        <div v-if="auth.hasPerm('order:view') && hasPendingRoutes" class="db-route-breakdown" aria-label="已支付待接单按接单路径">
          <div class="db-route-title"><strong>已支付待接单 · 接单路径</strong><span>数量不代表均可派单</span></div>
          <div class="db-route-items">
            <button class="db-route-item" @click="router.push({ path: '/orders', query: { queue: 'paid_pending_staff' } })"><span>客服派单</span><strong>{{ count(stats.pending_paid_routes.staff) }} 单</strong><span aria-hidden="true">›</span></button>
            <button class="db-route-item" @click="router.push({ path: '/orders', query: { queue: 'paid_pending_public' } })"><span>公开接单</span><strong>{{ count(stats.pending_paid_routes.public) }} 单</strong><span aria-hidden="true">›</span></button>
          </div>
        </div>
        <div v-if="auth.hasPerm('withdraw:view') && hasPendingWithdrawClaims" class="db-route-breakdown" aria-label="待审核提现按领取状态">
          <div class="db-route-title"><strong>待审核提现 · 领取状态</strong><span>领取后由领取人审核</span></div>
          <div class="db-route-items">
            <button class="db-route-item" @click="router.push({ path: '/withdrawals', query: { status: 'PENDING', claim_scope: 'unclaimed' } })"><span>未领取</span><strong>{{ count(stats.pending_withdraw_unclaimed) }} 笔</strong><span aria-hidden="true">›</span></button>
            <button class="db-route-item" @click="router.push({ path: '/withdrawals', query: { status: 'PENDING', claim_scope: 'mine' } })"><span>我已领取</span><strong>{{ count(stats.pending_withdraw_mine) }} 笔</strong><span aria-hidden="true">›</span></button>
            <button class="db-route-item" @click="router.push({ path: '/withdrawals', query: { status: 'PENDING', claim_scope: 'other' } })"><span>其他客服已领取</span><strong>{{ count(stats.pending_withdraw_total - stats.pending_withdraw_unclaimed - stats.pending_withdraw_mine) }} 笔</strong><span aria-hidden="true">›</span></button>
          </div>
        </div>
      </section>
      <section aria-label="运营关键指标" class="ap-kpi-grid">
        <article v-for="k in kpis" :key="k.label" class="ap-kpi">
          <div class="ap-kpi-top"><span class="ap-kpi-ic"><el-icon><component :is="k.icon" /></el-icon></span></div>
          <p class="ap-kpi-label">{{ k.label }}</p><p class="ap-kpi-value">{{ k.value }}</p><p class="ap-kpi-note">{{ k.note }}</p>
          <el-button v-if="k.target && k.available && auth.hasPerm('order:view')" class="db-kpi-link" link type="primary" :aria-label="`查看${k.label}订单`" @click="router.push({ path: '/orders', query: k.target })">查看订单 <span aria-hidden="true">›</span></el-button>
        </article>
      </section>
      <section class="db-accounts" aria-label="账号规模">
        <strong>账号规模</strong><span>全部账号 {{ count(stats.user_total) }}</span><span>老板 {{ count(stats.customer_total) }}</span><span>陪玩 {{ count(stats.provider_total) }}</span><small>当前账号数量</small>
      </section>

      <div class="db-charts">
        <article class="ap-card">
          <div class="ap-card-head"><div><h2 class="ap-card-title">近七日下单趋势</h2><p class="ap-card-sub">按创建日统计：订单数含未支付和取消；净额为已支付订单扣除当前退款，非当日收入 · {{ stats.timezone || '服务端时区' }}<span v-if="auth.hasPerm('order:view')"> · 选择日期查看订单</span></p></div></div>
          <div class="db-card-body">
            <e-chart v-if="dailyOrders.length" :option="dailyOption" height="260px" />
            <el-empty v-else description="趋势数据暂不可用" :image-size="80" />
            <ol v-if="dailyOrders.length" class="db-daily-list" aria-label="近七日新增订单明细">
              <li v-for="day in dailyOrders" :key="day.date">
                <button v-if="auth.hasPerm('order:view')" type="button" class="db-daily-item" @click="router.push({ path: '/orders', query: { created_day: day.date } })"><span>{{ day.date.slice(5) }}</span><strong>{{ count(day.count) }} 单</strong><small>净额 {{ dailyNet(day.net_amount) }}</small></button>
                <span v-else class="db-daily-item"><span>{{ day.date.slice(5) }}</span><strong>{{ count(day.count) }} 单</strong><small>净额 {{ dailyNet(day.net_amount) }}</small></span>
              </li>
            </ol>
          </div>
        </article>
        <article class="ap-card">
          <div class="ap-card-head"><div><h2 class="ap-card-title">当前订单状态</h2><p class="ap-card-sub">全部历史订单的当前状态快照<span v-if="auth.hasPerm('order:view')"> · 选择状态查看订单</span></p></div></div>
          <div class="db-card-body">
            <ul class="db-rank-list">
              <li v-for="s in statusList" :key="s.key">
                <component :is="auth.hasPerm('order:view') ? 'button' : 'div'" class="db-rank-row" :type="auth.hasPerm('order:view') ? 'button' : undefined" :aria-label="auth.hasPerm('order:view') ? `${s.label} ${s.count} 单，查看订单` : undefined" @click="openOrderStatus(s.key)">
                <el-tag :type="s.type" effect="light" size="small" round>{{ s.label }}</el-tag>
                <span class="db-rank-bar"><span class="db-rank-fill" :style="{ width: s.pct + '%' }" /></span>
                <span class="db-rank-count">{{ s.count }}</span><span class="db-rank-pct">{{ s.pct }}%</span>
                <span v-if="auth.hasPerm('order:view')" aria-hidden="true">›</span>
                </component>
              </li>
            </ul>
          </div>
        </article>
      </div>

      <article class="ap-card db-table-card">
        <div class="ap-card-head"><div><h2 class="ap-card-title">最近创建的订单 <el-tooltip :content="auth.hasPerm('order:view') ? '陪玩编号与账号为当前资料，不是下单时快照；缺少资料时请查看订单核对。' : '陪玩身份以当前资料为准；缺少资料时请联系有权限人员核对。'" :trigger="['hover', 'focus', 'click']"><button type="button" class="db-help" aria-label="陪玩身份说明">?</button></el-tooltip></h2><p class="ap-card-sub">最近 {{ recentOrders.length }} 笔 · 订单金额已计优惠，未扣退款</p></div></div>
        <el-table class="db-recent-table" :data="recentOrders" v-loading="loading">
          <el-table-column label="订单号" min-width="190"><template #default="{ row }"><router-link v-if="auth.hasPerm('order:view')" class="db-order-link" :to="{ path: '/orders', query: { find_order: row.order_no } }">{{ row.order_no }}</router-link><span v-else>{{ row.order_no }}</span></template></el-table-column>
          <el-table-column prop="customer_name" label="老板" width="120" />
          <el-table-column label="陪玩" min-width="120"><template #default="{ row }">{{ recentProviderNames(row) }}</template></el-table-column>
          <el-table-column prop="service_name_snapshot" label="服务" min-width="140" />
          <el-table-column label="订单金额" width="110" align="right"><template #default="{ row }">{{ amountToXaCoin(row.amount) }} 币</template></el-table-column>
          <el-table-column label="订单状态" width="100"><template #default="{ row }"><el-tag :type="ORDER_STATUS[row.status]?.type" effect="light" size="small" round>{{ row.status_display }}</el-tag></template></el-table-column>
          <el-table-column label="支付状态" width="100"><template #default="{ row }"><el-tag :type="PAYMENT_STATUS[row.payment_status]?.type || 'info'" effect="light" size="small" round>{{ row.payment_status_display || PAYMENT_STATUS[row.payment_status]?.label || '—' }}</el-tag></template></el-table-column>
          <el-table-column label="下单时间" width="170"><template #default="{ row }">{{ formatDateTime(row.created_at) }}</template></el-table-column>
        </el-table>
        <div class="db-recent-mobile" v-loading="loading">
          <ul v-if="recentOrders.length" class="db-recent-list" aria-label="最近创建的订单">
            <li v-for="row in recentOrders" :key="row.id" class="db-recent-row">
              <div class="db-recent-head">
                <router-link v-if="auth.hasPerm('order:view')" class="db-order-link" :to="{ path: '/orders', query: { find_order: row.order_no } }" :aria-label="`查看订单 ${row.order_no}`">{{ row.order_no }}</router-link>
                <span v-else class="db-order-number">{{ row.order_no }}</span>
                <el-tag :type="ORDER_STATUS[row.status]?.type" effect="light" size="small" round>{{ row.status_display }}</el-tag>
              </div>
              <p class="db-recent-service">{{ row.service_name_snapshot || '服务待核对' }}</p>
              <dl class="db-recent-details">
                <div><dt>老板</dt><dd>{{ row.customer_name || '—' }}</dd></div>
                <div><dt>陪玩</dt><dd>{{ recentProviderNames(row) }}</dd></div>
                <div><dt>金额</dt><dd>{{ amountToXaCoin(row.amount) }} 币</dd></div>
                <div><dt>支付</dt><dd>{{ row.payment_status_display || PAYMENT_STATUS[row.payment_status]?.label || '—' }}</dd></div>
                <div><dt>下单</dt><dd>{{ formatDateTime(row.created_at) }}</dd></div>
              </dl>
            </li>
          </ul>
          <el-empty v-else-if="!loading" :image-size="72" description="暂无订单" />
        </div>
      </article>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import type { EChartsOption } from 'echarts'
import { Refresh } from '@element-plus/icons-vue'
import EChart from '@/components/EChart.vue'
import { dashboardApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'
import { amountToXaCoin, formatDateTime } from '@/utils/format'
import { ORDER_STATUS, PAYMENT_STATUS } from '@/utils/dict'
import { providerListText } from '@/utils/orderParticipants'
import { adminWs } from '@/utils/ws'

const auth = useAuthStore()
const router = useRouter()
const stats = ref<any>(null)
const loading = ref(false)
const loadError = ref('')
let sequence = 0
let mounted = false
let pendingRefresh = false
let refreshTimer: ReturnType<typeof setTimeout> | null = null
let periodicTimer: ReturnType<typeof setInterval> | null = null
let unsubscribe: Array<() => void> = []
const recentOrders = computed<any[]>(() => stats.value?.recent_orders || [])
const recentProviderNames = providerListText
const dailyOrders = computed<{ date: string; count: number; net_amount?: number }[]>(() => stats.value?.daily_new_orders || [])
const dailyNet = (value: unknown) => typeof value === 'number' ? `${amountToXaCoin(value)} 币` : '—'
const snapshotText = computed(() => stats.value?.snapshot_at
  ? `开始读取：${formatDateTime(stats.value.snapshot_at)}（本机时间）；各指标分别查询，日期统计按 ${stats.value.timezone || '服务端时区'}；页面可见时约每分钟尝试更新`
  : '数据时间以服务端返回为准')
const count = (value: unknown) => typeof value === 'number' ? value.toLocaleString() : '—'
function openOrderStatus(status: string) {
  if (auth.hasPerm('order:view') && Object.prototype.hasOwnProperty.call(ORDER_STATUS, status)) {
    void router.push({ path: '/orders', query: { order_status: status } })
  }
}
const hasPendingRoutes = computed(() => {
  const routes = stats.value?.pending_paid_routes
  const total = stats.value?.pending_paid_total
  return Number.isSafeInteger(total) && total >= 0 && Number.isSafeInteger(routes?.staff) && routes.staff >= 0
    && Number.isSafeInteger(routes?.public) && routes.public >= 0 && routes.staff + routes.public <= total
})
const hasPendingWithdrawClaims = computed(() => {
  const total = stats.value?.pending_withdraw_total
  const unclaimed = stats.value?.pending_withdraw_unclaimed
  const mine = stats.value?.pending_withdraw_mine
  return [total, unclaimed, mine].every(value => Number.isSafeInteger(value) && value >= 0)
    && unclaimed + mine <= total
})
const inServiceCount = computed<number | null>(() => {
  const statuses = stats.value?.order_status
  if (!statuses || typeof statuses !== 'object' || Array.isArray(statuses)) return null
  const value = statuses.IN_SERVICE === undefined ? 0 : statuses.IN_SERVICE
  return Number.isSafeInteger(value) && value >= 0 ? value : null
})
// The final trend row uses the server's business timezone. Reuse that date
// rather than the browser clock so the drilldown matches the snapshot.
const todayOrderTarget = computed(() => {
  const day = dailyOrders.value[dailyOrders.value.length - 1]?.date
  return typeof day === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(day) ? { created_day: day } : undefined
})
const kpis = computed(() => [
  { label: '今日新增订单', value: count(stats.value.today_order_total), note: '按创建时间 · 含未支付及取消', icon: 'Tickets', target: todayOrderTarget.value, available: !!todayOrderTarget.value },
  { label: '未支付待接单', value: count(stats.value.pending_unpaid_total), note: '支付完成后才可派单', icon: 'Clock', target: { queue: 'unpaid_pending' }, available: Number.isSafeInteger(stats.value.pending_unpaid_total) && stats.value.pending_unpaid_total >= 0 },
  { label: '服务中', value: count(inServiceCount.value), note: '当前处于服务中状态的订单', icon: 'Service', target: { order_status: 'IN_SERVICE' }, available: inServiceCount.value !== null },
  { label: '累计订单净额', value: `${amountToXaCoin(stats.value.order_amount_total)}币`, note: `已支付及部分退款订单 ${count(stats.value.paid_order_total)} 单，已扣退款；非平台利润`, icon: 'Money' },
])
const statusList = computed(() => {
  const map: Record<string, number> = stats.value?.order_status || {}
  const total = Object.values(map).reduce((a, b) => a + b, 0)
  return Object.entries(ORDER_STATUS).map(([key, v]) => {
    const count = map[key] ?? 0
    return { key, label: v.label, type: v.type, count, pct: total ? Math.round((count / total) * 100) : 0 }
  })
})
const dailyOption = computed<EChartsOption>(() => ({
  grid: { left: 8, right: 16, top: 25, bottom: 8, containLabel: true },
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
  xAxis: { type: 'category', data: dailyOrders.value.map(d => d.date.slice(5)), axisLine: { lineStyle: { color: 'var(--border)' } } },
  yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: 'var(--border)' } } },
  series: [{ type: 'bar', barMaxWidth: 36, data: dailyOrders.value.map(d => d.count), itemStyle: { color: 'var(--primary)', borderRadius: [6, 6, 0, 0] } }],
}))
function requestRefresh(delay = 0) {
  if (!mounted || !auth.hasPerm('dashboard:view')) return
  if (document.hidden || loading.value) { pendingRefresh = true; return }
  if (refreshTimer) {
    if (delay) return
    clearTimeout(refreshTimer)
    refreshTimer = null
  }
  if (delay) {
    refreshTimer = setTimeout(() => { refreshTimer = null; requestRefresh() }, delay)
    return
  }
  pendingRefresh = false
  void load()
}
async function load() {
  const current = ++sequence
  const revision = auth.sessionRevision
  loading.value = true
  try {
    const res = await dashboardApi.stats(true)
    if (current === sequence && revision === auth.sessionRevision && auth.hasPerm('dashboard:view')) {
      stats.value = res.data
      loadError.value = ''
    }
  } catch {
    if (current === sequence && revision === auth.sessionRevision) loadError.value = '请检查网络和访问权限后重试。'
  } finally {
    if (current === sequence) {
      loading.value = false
      if (pendingRefresh && !document.hidden) { pendingRefresh = false; requestRefresh(5000) }
    }
  }
}
function onVisibilityChange() {
  if (!document.hidden) requestRefresh()
}
watch([() => auth.sessionRevision, () => auth.hasPerm('dashboard:view'), () => auth.hasPerm('order:view'), () => auth.hasPerm('report:view'), () => auth.hasPerm('withdraw:view'), () => auth.hasPerm('chat:view')], () => {
  ++sequence
  if (refreshTimer) clearTimeout(refreshTimer)
  refreshTimer = null
  pendingRefresh = false
  stats.value = null
  loading.value = false
  loadError.value = ''
  if (mounted && auth.hasPerm('dashboard:view')) requestRefresh()
}, { flush: 'sync' })
onMounted(() => {
  mounted = true
  unsubscribe = [adminWs.on('order_status_update', () => requestRefresh(5000)),
    adminWs.on('chat_message', () => requestRefresh(5000)),
    adminWs.on('chat_session_update', () => requestRefresh(5000)),
    adminWs.on('reservation_status_update', () => requestRefresh(5000)),
    adminWs.on('report_status_update', () => requestRefresh(5000)),
    adminWs.on('withdraw_status_update', () => requestRefresh(5000)),
    adminWs.on('connected', () => requestRefresh())]
  document.addEventListener('visibilitychange', onVisibilityChange)
  periodicTimer = setInterval(() => { if (!document.hidden) requestRefresh() }, 60000)
  requestRefresh()
  adminWs.connect()
})
onUnmounted(() => {
  mounted = false
  ++sequence
  if (refreshTimer) clearTimeout(refreshTimer)
  if (periodicTimer) clearInterval(periodicTimer)
  document.removeEventListener('visibilitychange', onVisibilityChange)
  unsubscribe.forEach(off => off())
})
</script>

<style scoped lang="scss">
.db-kpi-link { margin-top: 8px; }
.db-page { display: flex; flex-direction: column; gap: 20px; }
.db-daily-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(75px, 1fr)); gap: 6px; padding: 0; margin: 8px 0 0; list-style: none; }
.db-daily-item { display: flex; width: 100%; flex-direction: column; gap: 3px; padding: 6px 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--muted); color: var(--foreground); text-align: left; font-size: 12px; }
button.db-daily-item { cursor: pointer; }
button.db-daily-item:hover { border-color: var(--primary); }
button.db-daily-item:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.db-snapshot { margin: 5px 0 0; color: var(--muted-foreground); font-size: 12px; }
.db-work { padding: 18px 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.db-work h2 { margin: 0; color: var(--foreground); font-size: 16px; }
.db-work p { margin: 6px 0 14px; color: var(--muted-foreground); font-size: 12px; }
.db-work-items { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.db-work-item { display: flex; align-items: center; gap: 12px; min-height: 68px; padding: 12px 14px; border: 1px solid var(--border); border-radius: 10px; background: var(--background); color: var(--foreground); text-align: left; cursor: pointer; }
.db-work-item > span:first-child { flex: 1; min-width: 0; font-weight: 600; }
.db-work-item small { display: block; margin-top: 4px; color: var(--muted-foreground); font-size: 12px; font-weight: 400; }
.db-work-item strong { font-size: 20px; font-variant-numeric: tabular-nums; white-space: nowrap; }
.db-work-item strong small { display: inline; font-size: 12px; }
.db-work-item:hover { border-color: var(--el-color-primary); }
.db-work-item:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
.db-route-breakdown { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--border); }
.db-route-title { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 12px; font-size: 13px; }
.db-route-title span { color: var(--muted-foreground); font-size: 12px; }
.db-route-items { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; margin-top: 12px; }
.db-route-item { display: flex; align-items: center; gap: 8px; min-height: 40px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--background); color: var(--foreground); text-align: left; cursor: pointer; }
.db-route-item > span:first-child { flex: 1; }.db-route-item strong { font-size: 13px; white-space: nowrap; font-variant-numeric: tabular-nums; }
.db-route-item:hover { border-color: var(--el-color-primary); }.db-route-item:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
.db-accounts { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 20px; padding: 14px 18px; border: 1px solid var(--border); border-radius: 12px; background: var(--xa-color-surface-card); font-size: 13px; }
.db-accounts strong { color: var(--foreground); }
.db-accounts span, .db-accounts small { color: var(--muted-foreground); }
.db-charts { display: grid; grid-template-columns: 1.4fr 1fr; gap: 20px; }
.db-card-body { padding: 20px 24px; }
.db-rank-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 16px; }
.db-rank-row { display: grid; grid-template-columns: 72px minmax(0, 1fr) 48px 42px 12px; align-items: center; gap: 12px; width: 100%; padding: 4px; color: var(--foreground); text-align: left; }
button.db-rank-row { border: 0; border-radius: 8px; background: transparent; font: inherit; cursor: pointer; }
button.db-rank-row:hover { background: var(--secondary); }
button.db-rank-row:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
.db-rank-bar { height: 8px; border-radius: 999px; background: var(--secondary); overflow: hidden; }
.db-rank-fill { display: block; height: 100%; border-radius: 999px; background: var(--primary); transition: width .25s ease; }
.db-rank-count { font-size: 13px; font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; }
.db-rank-pct { font-size: 12px; color: var(--muted-foreground); text-align: right; }
.db-table-card :deep(.el-table) { margin: 0 8px 8px; }
.db-order-link { color: var(--el-color-primary); text-decoration: none; overflow-wrap: anywhere; }
.db-order-link:hover { text-decoration: underline; }
.db-order-link:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
.db-help { display: inline-grid; place-items: center; width: 28px; height: 28px; padding: 0; border: 1px solid var(--border); border-radius: 50%; color: var(--muted-foreground); background: transparent; font-size: 12px; cursor: pointer; }
.db-help:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
.db-recent-mobile { display: none; }
.db-recent-list { list-style: none; padding: 0 16px; margin: 0; }
.db-recent-row { padding: 16px 0; border-top: 1px solid var(--border); }
.db-recent-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.db-recent-head .el-tag { flex: none; }
.db-order-number { overflow-wrap: anywhere; }
.db-recent-service { margin: 10px 0; color: var(--foreground); font-weight: 600; }
.db-recent-details { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 12px; margin: 0; font-size: 12px; }
.db-recent-details div { display: flex; gap: 6px; min-width: 0; }
.db-recent-details > div:nth-child(2) { grid-column: 1 / -1; order: 1; }
.db-recent-details > div:nth-child(4), .db-recent-details > div:nth-child(5) { order: 2; }
.db-recent-details dt { color: var(--muted-foreground); flex: none; }
.db-recent-details dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
@media (max-width: 1000px) { .db-charts { grid-template-columns: 1fr; } }
@media (max-width: 900px) { .db-work-items { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 720px) { .db-table-card :deep(.db-recent-table) { display: none; } .db-recent-mobile { display: block; } }
@media (max-width: 600px) { .db-work-items { grid-template-columns: 1fr; } }
@media (prefers-reduced-motion: reduce) { .db-rank-fill { transition: none; } }
</style>
