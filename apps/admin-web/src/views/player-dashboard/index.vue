<template>
  <div class="pd-page">
    <Card class="report-filter">
      <header class="report-heading">
        <h2>陪玩业绩与账户概览</h2>
        <Popover>
          <PopoverTrigger as-child><Button size="xs" variant="outline" aria-label="查看统计口径">统计口径</Button></PopoverTrigger>
          <PopoverContent class="report-help">订单指标按创建日期统计已支付及部分退款订单净额；双陪和转单按分账基数分配，部分退款按基数比例扣减，接单额不等于钱包到账。收益榜包含待结算与已结算快照；退款流水无法核对时，该陪玩不参与收益榜。账户指标为全量快照，不受日期筛选。已通过提现申请额按申请金额统计，未扣税。</PopoverContent>
        </Popover>
      </header>
      <form class="pd-filters" @submit.prevent="query">
        <label>开始日期<Input :model-value="startDate ?? ''" type="date" aria-label="开始日期" @update:model-value="value => { startDate = String(value) || null }" /></label>
        <label>结束日期<Input :model-value="endDate ?? ''" type="date" aria-label="结束日期" @update:model-value="value => { endDate = String(value) || null }" /></label>
        <div class="filter-actions">
          <Button size="xs" type="submit" :disabled="loading">{{ loading ? '查询中…' : '查询' }}</Button>
          <Button size="xs" type="button" variant="outline" @click="onReset">重置</Button>
        </div>
      </form>
      <p class="pd-snapshot">{{ snapshotText }}</p>
    </Card>

    <Alert v-if="rangeError" variant="destructive" role="alert"><AlertDescription>{{ rangeError }}</AlertDescription></Alert>
    <Alert v-if="loadError" variant="destructive" role="alert"><AlertTitle>{{ data ? '读取失败，当前显示上次成功的数据' : '看板数据加载失败' }}</AlertTitle><AlertDescription>请检查网络或权限后重试。<Button size="xs" variant="outline" @click="retry">重新加载</Button></AlertDescription></Alert>
    <p v-if="loading && !data" role="status">正在加载业绩数据…</p>
    <template v-else-if="data">
    <div class="pd-section-heading"><h2>订单业绩</h2><span>{{ rangeLabel }}</span></div>
    <!-- 接单额 KPI -->
    <div class="pd-kpi-grid">
      <Card v-for="(card, index) in amountCards" :key="card.label" :class="['metric-card', { 'metric-primary': index === 0 }]">
        <p class="ap-kpi-label">{{ card.label }}</p>
        <p class="ap-kpi-value">{{ amountToXaCoin(card.value) }}<span class="amount-unit">币</span></p>
      </Card>
    </div>

    <!-- 占比饼图 -->
    <div class="pd-charts">
      <Card class="chart-card">
        <div class="ap-card-head">
          <div>
            <h2 class="ap-card-title">订单净额占比</h2>
            <p class="ap-card-sub">游戏单 / 礼物单</p>
          </div>
        </div>
        <div class="pd-card-body">
          <e-chart v-if="totalHasData" :option="totalPie" height="216px" />
          <p v-if="totalHasData" class="pd-chart-values">游戏单 {{ amountToXaCoin(kpi.game_amount) }} 币 · 礼物单 {{ amountToXaCoin(kpi.gift_amount) }} 币</p>
          <p v-else class="pd-empty">暂无数据</p>
        </div>
      </Card>
      <Card class="chart-card">
        <div class="ap-card-head">
          <div>
            <h2 class="ap-card-title">游戏单分账占比</h2>
            <p class="ap-card-sub">仅含已标注性别的陪玩分账</p>
          </div>
        </div>
        <div class="pd-card-body">
          <e-chart v-if="gameHasData" :option="gamePie" height="216px" />
          <p v-if="gameHasData" class="pd-chart-values">男陪 {{ amountToXaCoin(kpi.male_game) }} 币 · 女陪 {{ amountToXaCoin(kpi.female_game) }} 币</p>
          <p v-else class="pd-empty">暂无已标注性别的分账</p>
        </div>
      </Card>
      <Card class="chart-card">
        <div class="ap-card-head">
          <div>
            <h2 class="ap-card-title">礼物单分账占比</h2>
            <p class="ap-card-sub">仅含已标注性别的陪玩分账</p>
          </div>
        </div>
        <div class="pd-card-body">
          <e-chart v-if="giftHasData" :option="giftPie" height="216px" />
          <p v-if="giftHasData" class="pd-chart-values">男陪 {{ amountToXaCoin(kpi.male_gift) }} 币 · 女陪 {{ amountToXaCoin(kpi.female_gift) }} 币</p>
          <p v-else class="pd-empty">暂无已标注性别的分账</p>
        </div>
      </Card>
    </div>

    <div class="pd-section-heading"><h2>陪玩排行榜</h2><span>{{ rangeLabel }}</span></div>
    <!-- 排行榜 -->
    <div class="pd-ranks">
      <Card v-for="rank in rankTables" :key="rank.key" class="rank-card">
        <div class="ap-card-head">
          <div>
            <h2 class="ap-card-title">{{ rank.title }}</h2>
          </div>
        </div>
        <div class="pd-card-body">
          <Alert v-if="rank.key === 'income' && data.income_review.order_count"><AlertTitle>{{ data.income_review.order_count }} 单的退款收益待核对</AlertTitle><AlertDescription>{{ data.income_review.provider_count }} 名已识别陪玩暂不参与排行{{ data.income_review.unidentified_count ? `，另有 ${data.income_review.unidentified_count} 条归属不明` : '' }}；请核对退款记录与钱包流水。</AlertDescription></Alert>
          <Table v-if="rank.rows.length" class="pd-rank-table"><TableHeader><TableRow><TableHead>#</TableHead><TableHead>昵称</TableHead><TableHead>编号</TableHead><TableHead>性别</TableHead><TableHead>{{ rank.valueLabel }}</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="(row, index) in rank.rows" :key="`${row.escort_no}-${index}`"><TableCell>{{ index + 1 }}</TableCell><TableCell>{{ row.nickname || '—' }}</TableCell><TableCell>{{ row.escort_no || '—' }}</TableCell><TableCell><Badge variant="outline">{{ GENDER[row.gender]?.label || '未知' }}</Badge></TableCell><TableCell>{{ rank.fmt(row.value) }}</TableCell></TableRow></TableBody></Table>
          <ol v-if="rank.rows.length" class="pd-rank-mobile" :aria-label="rank.title">
            <li v-for="(row, index) in rank.rows" :key="`${row.escort_no}-${index}`">
              <span class="pd-rank-index">{{ index + 1 }}</span><div class="pd-rank-person"><strong>{{ row.nickname || '—' }}</strong><small>{{ row.escort_no || '无编号' }} · {{ GENDER[row.gender]?.label || '未知' }}</small></div><strong class="pd-rank-value">{{ rank.fmt(row.value) }}</strong>
            </li>
          </ol>
          <p v-if="!rank.rows.length" class="pd-empty">{{ rank.key === 'income' && data.income_review.order_count ? '暂无可排列的收益数据' : '暂无数据' }}</p>
        </div>
      </Card>
    </div>
    <div class="pd-section-heading"><h2>账户与提现</h2><span>全量快照，不受日期筛选</span></div>
    <!-- 二次 KPI -->
    <div class="pd-kpi-grid">
      <Card v-for="card in secondaryCards" :key="card.label" class="metric-card">
        <p class="ap-kpi-label">{{ card.label }}</p>
        <p class="ap-kpi-value" :style="{ color: card.color }">{{ amountToXaCoin(card.value) }}<span class="amount-unit">币</span></p>
      </Card>
    </div>

    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import type { EChartsOption } from 'echarts'
import { Card } from '@xa/shadcn-vue/card'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Badge } from '@xa/shadcn-vue/badge'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { Popover, PopoverContent, PopoverTrigger } from '@xa/shadcn-vue/popover'
import EChart from '@/components/EChart.vue'
import { playerDashboardApi } from '@/api/modules'
import { amountToXaCoin, formatDateTime } from '@/utils/format'
import { GENDER } from '@/utils/dict'
import { useAuthStore } from '@/stores/auth'
import { adminWs } from '@/utils/ws'

const auth = useAuthStore()
const loading = ref(false)
const loadError = ref(false)
const startDate = ref<string | null>(null)
const endDate = ref<string | null>(null)
const appliedRange = ref<{ start: string | null; end: string | null }>({ start: null, end: null })
const rangeError = ref('')
const data = ref<any>(null)
let sequence = 0
let mounted = false
let pendingRefresh = false
let refreshTimer: ReturnType<typeof setTimeout> | null = null
let periodicTimer: ReturnType<typeof setInterval> | null = null
let unsubscribe: Array<() => void> = []
type DateRange = { start: string | null; end: string | null }
let failedRange: DateRange | null = null
const kpi = computed(() => data.value?.kpi || {})
const secondary = computed(() => data.value?.secondary || {})
const rank = computed(() => data.value?.rank || { amount: [], income: [], count: [] })
const rangeLabel = computed(() => {
  const { start, end } = appliedRange.value
  return `${start && end ? `${start} 至 ${end}` : start ? `${start} 起` : end ? `截至 ${end}` : '全部时间'} · 按订单创建日期`
})
const snapshotText = computed(() => data.value?.snapshot_at
  ? `开始读取：${formatDateTime(data.value.snapshot_at)}（本机时间）；各指标分别查询，订单日期按 ${data.value.timezone || '服务端时区'}；页面可见时约每分钟尝试更新`
  : data.value ? '服务端未提供本次读取的开始时间' : '等待读取服务端数据')

// 分类图表使用传统蓝色色阶；奖罚和订单状态保留语义色。
const PIE_COLORS = [
  'var(--chart-1)',
  'var(--chart-2)',
  'var(--chart-3)',
  'var(--chart-4)',
  'var(--chart-5)',
  'var(--muted-foreground)',
]

const amountCards = computed(() => [
  { label: '订单净额', value: kpi.value.total_amount ?? 0, icon: 'Wallet' },
  { label: '游戏单', value: kpi.value.game_amount ?? 0, icon: 'Trophy' },
  { label: '礼物单', value: kpi.value.gift_amount ?? 0, icon: 'Present' },
  { label: '男陪游戏单', value: kpi.value.male_game ?? 0, icon: 'Male' },
  { label: '女陪游戏单', value: kpi.value.female_game ?? 0, icon: 'Female' },
  { label: '男陪礼物单', value: kpi.value.male_gift ?? 0, icon: 'Male' },
  { label: '女陪礼物单', value: kpi.value.female_gift ?? 0, icon: 'Female' },
])

const secondaryCards = computed(() => [
  { label: '押金总数', value: secondary.value.total_deposit ?? 0, color: 'var(--primary)', icon: 'Coin' },
  { label: '罚款总数', value: secondary.value.total_penalty ?? 0, color: 'var(--destructive)', icon: 'WarningFilled' },
  { label: '奖励总数', value: secondary.value.total_reward ?? 0, color: 'var(--success)', icon: 'Medal' },
  { label: '钱包总额', value: secondary.value.total_wallet ?? 0, color: 'var(--chart-4)', icon: 'Wallet' },
  { label: '冻结金额', value: secondary.value.frozen_amount ?? 0, color: 'var(--chart-3)', icon: 'Lock' },
  { label: '已通过提现申请额', value: secondary.value.settled_salary ?? 0, color: 'var(--muted-foreground)', icon: 'Money' },
])

function pie(title: string, data: { name: string; value: number }[]): EChartsOption {
  return {
    tooltip: {
      trigger: 'item',
      formatter: (p: any) => `${p.name}: ${amountToXaCoin(p.value)}币 (${p.percent}%)`,
    },
    legend: { bottom: 0, icon: 'circle' },
    series: [
      {
        name: title,
        type: 'pie',
        radius: ['52%', '76%'],
        center: ['50%', '45%'],
        avoidLabelOverlap: false,
        itemStyle: { borderRadius: 6, borderColor: 'var(--card)', borderWidth: 2 },
        label: { formatter: '{b}\n{d}%' },
        labelLine: { show: true },
        data: data.map((d, i) => ({ ...d, itemStyle: { color: PIE_COLORS[i % PIE_COLORS.length] } })),
      },
    ],
  }
}

const totalHasData = computed(() => (kpi.value.game_amount ?? 0) + (kpi.value.gift_amount ?? 0) > 0)
const gameHasData = computed(() => (kpi.value.male_game ?? 0) + (kpi.value.female_game ?? 0) > 0)
const giftHasData = computed(() => (kpi.value.male_gift ?? 0) + (kpi.value.female_gift ?? 0) > 0)

const totalPie = computed(() =>
  pie('订单净额', [
    { name: '游戏单', value: kpi.value.game_amount ?? 0 },
    { name: '礼物单', value: kpi.value.gift_amount ?? 0 },
  ]),
)
const gamePie = computed(() =>
  pie('游戏单', [
    { name: '男陪', value: kpi.value.male_game ?? 0 },
    { name: '女陪', value: kpi.value.female_game ?? 0 },
  ]),
)
const giftPie = computed(() =>
  pie('礼物单', [
    { name: '男陪', value: kpi.value.male_gift ?? 0 },
    { name: '女陪', value: kpi.value.female_gift ?? 0 },
  ]),
)

const rankTables = computed(() => [
  { key: 'amount', title: '接单额榜 Top10', valueLabel: '接单额', rows: rank.value.amount, fmt: (v: number) => Number.isFinite(v) ? `${amountToXaCoin(v)}币` : '—' },
  { key: 'income', title: '分账收益榜 Top10', valueLabel: '分账收益', rows: rank.value.income, fmt: (v: number) => Number.isFinite(v) ? `${amountToXaCoin(v)}币` : '—' },
  { key: 'count', title: '接单量榜 Top10', valueLabel: '接单量', rows: rank.value.count, fmt: (v: number) => Number.isFinite(v) ? `${v} 单` : '—' },
])

function query() {
  if (startDate.value && endDate.value && startDate.value > endDate.value) {
    rangeError.value = '开始日期不能晚于结束日期'
    return
  }
  rangeError.value = ''
  void load({ start: startDate.value, end: endDate.value }, true)
}

async function load(requestedRange: DateRange, explicit = false) {
  const current = ++sequence
  const revision = auth.sessionRevision
  const params: Record<string, any> = {}
  if (requestedRange.start) params.start_date = requestedRange.start
  if (requestedRange.end) params.end_date = requestedRange.end
  loading.value = true
  try {
    const res = await playerDashboardApi.stats(params, true)
    if (current !== sequence || revision !== auth.sessionRevision || !auth.hasPerm('player_dashboard:view')) return
    const kpiKeys = ['total_amount', 'game_amount', 'gift_amount', 'male_game', 'female_game', 'male_gift', 'female_gift']
    const secondaryKeys = ['total_deposit', 'total_penalty', 'total_reward', 'total_wallet', 'frozen_amount', 'settled_salary']
    if (!kpiKeys.every(key => Number.isFinite(res.data?.kpi?.[key])) || !secondaryKeys.every(key => Number.isFinite(res.data?.secondary?.[key])) || !['amount', 'income', 'count'].every(key => Array.isArray(res.data?.rank?.[key])) || !['order_count', 'provider_count', 'unidentified_count'].every(key => Number.isSafeInteger(res.data?.income_review?.[key]) && res.data.income_review[key] >= 0)) throw new Error('invalid dashboard response')
    appliedRange.value = requestedRange
    data.value = res.data
    if (explicit || !failedRange || (failedRange.start === requestedRange.start && failedRange.end === requestedRange.end)) {
      failedRange = null
      loadError.value = false
    }
  } catch {
    if (current === sequence && revision === auth.sessionRevision) {
      if (explicit || !failedRange) failedRange = requestedRange
      loadError.value = true
    }
  } finally {
    if (current === sequence) {
      loading.value = false
      if (pendingRefresh && !document.hidden) { pendingRefresh = false; requestRefresh(5000) }
    }
  }
}

function retry() {
  if (failedRange) void load({ ...failedRange }, true)
}

function requestRefresh(delay = 0) {
  if (!mounted || !auth.hasPerm('player_dashboard:view')) return
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
  void load({ ...appliedRange.value })
}

function onVisibilityChange() {
  if (!document.hidden) requestRefresh()
}

function onReset() {
  startDate.value = null
  endDate.value = null
  query()
}

watch([() => auth.sessionRevision, () => auth.hasPerm('player_dashboard:view')], () => {
  ++sequence
  if (refreshTimer) clearTimeout(refreshTimer)
  refreshTimer = null
  pendingRefresh = false
  data.value = null
  failedRange = null
  loading.value = false
  loadError.value = false
  if (mounted && auth.hasPerm('player_dashboard:view')) requestRefresh()
}, { flush: 'sync' })
onMounted(() => {
  mounted = true
  unsubscribe = [adminWs.on('order_status_update', () => requestRefresh(5000)),
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

<style scoped>
.pd-page { display:grid; grid-template-columns:minmax(0,1fr); container:performance / inline-size; min-width:0; gap:12px; }
.report-filter,.metric-card,.chart-card,.rank-card { min-width:0; padding:12px; gap:8px; border:1px solid var(--border); border-radius:12px; background:var(--card); box-shadow:none; }
.report-filter { display:grid; }
.report-heading,.pd-section-heading,.filter-actions { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.report-heading { justify-content:space-between; }
h2,p { margin:0; }
.report-heading h2,.pd-section-heading h2,.ap-card-title { font-size:16px; line-height:24px; font-weight:600; }
.pd-snapshot,.pd-filters label,.report-help,.pd-section-heading span,.pd-chart-values,.ap-card-sub,.pd-empty { color:var(--muted-foreground); font-size:12px; line-height:16px; }
.pd-filters { display:grid; grid-template-columns:repeat(2,minmax(116px,180px)) auto; gap:8px; align-items:end; }
.pd-filters label { min-width:0; display:grid; gap:4px; }
.pd-filters :deep(input) { min-width:0; width:100%; height:28px; min-height:28px; font-size:12px; }
.report-help { width:min(360px,calc(100vw - 32px)); font-size:13px; line-height:20px; }
.pd-kpi-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; }
.metric-card { gap:4px; }
.metric-primary { grid-column:span 2; background:linear-gradient(105deg,var(--secondary),var(--card)); }
.ap-kpi-label { color:var(--muted-foreground); font-size:12px; line-height:16px; overflow-wrap:anywhere; }
.ap-kpi-value { font-size:20px; line-height:28px; font-weight:650; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
.amount-unit { margin-left:4px; color:var(--muted-foreground); font-size:12px; font-weight:400; }
.pd-charts,.pd-ranks { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; align-items:start; }
.pd-ranks { grid-template-columns:repeat(auto-fit,minmax(min(100%,448px),1fr)); }
.pd-card-body { min-width:0; display:grid; gap:8px; }
.ap-card-head { display:block; padding:0; border:0; }
.ap-card-sub,.pd-chart-values { margin-top:4px; }
.pd-chart-values { text-align:center; }
.pd-empty { padding:12px; text-align:center; background:var(--muted); border-radius:8px; }
.pd-rank-mobile { display:none; margin:0; padding:0; list-style:none; }
.rank-card { container:rank-card / inline-size; }
.pd-card-body :deep(td),.pd-card-body :deep(th) { padding:4px 8px; white-space:normal; overflow-wrap:anywhere; }
.pd-rank-mobile li { display:flex; align-items:center; gap:8px; padding:8px 0; border-bottom:1px solid var(--border); }
.pd-rank-mobile li:last-child { border:0; } .pd-rank-index { width:20px; flex:none; text-align:center; color:var(--muted-foreground); }
.pd-rank-person { flex:1; min-width:0; overflow-wrap:anywhere; }
.pd-rank-person small { display:block; color:var(--muted-foreground); margin-top:4px; font-size:12px; }
.pd-rank-value { max-width:40%; text-align:right; overflow-wrap:anywhere; font-variant-numeric:tabular-nums; }
@container rank-card (max-width:400px) { .pd-card-body :deep([data-slot='table-container']) { display:none; } .pd-rank-mobile { display:block; } }
@container performance (max-width:850px) {
 .pd-charts,.pd-ranks { grid-template-columns:repeat(2,minmax(0,1fr)); }
 .pd-kpi-grid { grid-template-columns:repeat(3,minmax(0,1fr)); }
}
@container performance (max-width:560px) {
 .pd-charts,.pd-ranks { grid-template-columns:minmax(0,1fr); }
 .pd-kpi-grid,.pd-filters { grid-template-columns:repeat(2,minmax(0,1fr)); }
 .filter-actions { grid-column:1/-1; }
}
@container performance (max-width:320px) { .pd-filters { grid-template-columns:minmax(0,1fr); } }
@media(pointer:coarse) { .report-filter :deep(button),.report-filter :deep(input) { min-height:44px; } .report-filter :deep(button) { min-width:44px; } }
</style>
