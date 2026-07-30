<template>
  <div class="pd-page" v-loading="loading">
    <!-- 头部操作行 -->
    <div class="ap-page-head">
      <p class="ap-page-intro">接单额/接单量按「已支付」订单统计；押金/钱包等为实时快照</p>
      <div class="ap-page-head-actions">
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          value-format="YYYY-MM-DD"
          @change="load"
        />
        <el-button :icon="Refresh" @click="onReset">重置</el-button>
      </div>
    </div>

    <!-- 接单额 KPI -->
    <div class="ap-kpi-grid pd-kpi-grid">
      <article v-for="card in amountCards" :key="card.label" class="ap-kpi">
        <div class="ap-kpi-top">
          <span class="ap-kpi-ic"><el-icon><component :is="card.icon" /></el-icon></span>
        </div>
        <p class="ap-kpi-label">{{ card.label }}</p>
        <p class="ap-kpi-value">{{ amountToXaCoin(card.value) }}币</p>
      </article>
    </div>

    <!-- 占比饼图 -->
    <div class="pd-charts">
      <article class="ap-card">
        <div class="ap-card-head">
          <div>
            <h2 class="ap-card-title">总接单额占比</h2>
            <p class="ap-card-sub">游戏单 / 礼物单</p>
          </div>
        </div>
        <div class="pd-card-body">
          <e-chart v-if="totalHasData" :option="totalPie" height="280px" />
          <el-empty v-else description="暂无数据" :image-size="90" />
        </div>
      </article>
      <article class="ap-card">
        <div class="ap-card-head">
          <div>
            <h2 class="ap-card-title">游戏单男女占比</h2>
            <p class="ap-card-sub">男陪 / 女陪</p>
          </div>
        </div>
        <div class="pd-card-body">
          <e-chart v-if="gameHasData" :option="gamePie" height="280px" />
          <el-empty v-else description="暂无数据" :image-size="90" />
        </div>
      </article>
      <article class="ap-card">
        <div class="ap-card-head">
          <div>
            <h2 class="ap-card-title">礼物单男女占比</h2>
            <p class="ap-card-sub">男陪 / 女陪</p>
          </div>
        </div>
        <div class="pd-card-body">
          <e-chart v-if="giftHasData" :option="giftPie" height="280px" />
          <el-empty v-else description="暂无数据" :image-size="90" />
        </div>
      </article>
    </div>

    <!-- 二次 KPI -->
    <div class="ap-kpi-grid pd-kpi-grid">
      <article v-for="card in secondaryCards" :key="card.label" class="ap-kpi">
        <div class="ap-kpi-top">
          <span class="ap-kpi-ic"><el-icon><component :is="card.icon" /></el-icon></span>
        </div>
        <p class="ap-kpi-label">{{ card.label }}</p>
        <p class="ap-kpi-value" :style="{ color: card.color }">{{ amountToXaCoin(card.value) }}币</p>
      </article>
    </div>

    <!-- 排行榜 -->
    <div class="pd-ranks">
      <article v-for="rank in rankTables" :key="rank.key" class="ap-card">
        <div class="ap-card-head">
          <div>
            <h2 class="ap-card-title">{{ rank.title }}</h2>
          </div>
        </div>
        <div class="pd-card-body">
          <el-table v-if="rank.rows.length" :data="rank.rows" size="small">
            <el-table-column type="index" label="#" width="48" />
            <el-table-column prop="nickname" label="昵称" min-width="100" show-overflow-tooltip />
            <el-table-column prop="escort_no" label="编号" width="90">
              <template #default="{ row }">{{ row.escort_no || '-' }}</template>
            </el-table-column>
            <el-table-column label="性别" width="70">
              <template #default="{ row }">
                <el-tag size="small" :type="GENDER[row.gender]?.type" effect="light" round>{{ GENDER[row.gender]?.label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="rank.valueLabel" width="100" align="right">
              <template #default="{ row }">{{ rank.fmt(row.value) }}</template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="暂无数据" :image-size="70" />
        </div>
      </article>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { EChartsOption } from 'echarts'
import { Refresh } from '@element-plus/icons-vue'
import EChart from '@/components/EChart.vue'
import { playerDashboardApi } from '@/api/modules'
import { amountToXaCoin } from '@/utils/format'
import { GENDER } from '@/utils/dict'

const loading = ref(false)
const dateRange = ref<[string, string] | null>(null)
const kpi = ref<any>({})
const secondary = ref<any>({})
const rank = ref<any>({ amount: [], income: [], count: [] })

// 统一图表色板（与 dashboard 的 STATUS_COLORS 一致）
const PIE_COLORS = [
  'var(--primary)',
  'var(--success)',
  'var(--warning)',
  'var(--chart-4)',
  'var(--chart-5)',
  'var(--destructive)',
  'var(--muted-foreground)',
]

const amountCards = computed(() => [
  { label: '总接单额', value: kpi.value.total_amount ?? 0, icon: 'Wallet' },
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
  { label: '已结算工资', value: secondary.value.settled_salary ?? 0, color: 'var(--muted-foreground)', icon: 'Money' },
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
        itemStyle: { borderRadius: 6, borderColor: '#ffffff', borderWidth: 2 },
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
  pie('总接单额', [
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
  { key: 'amount', title: '接单额榜 Top10', valueLabel: '接单额', rows: rank.value.amount, fmt: (v: number) => `${amountToXaCoin(v)}币` },
  { key: 'income', title: '收入榜 Top10', valueLabel: '收入', rows: rank.value.income, fmt: (v: number) => `${amountToXaCoin(v)}币` },
  { key: 'count', title: '接单量榜 Top10', valueLabel: '接单量', rows: rank.value.count, fmt: (v: number) => `${v} 单` },
])

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = {}
    if (dateRange.value?.length === 2) {
      params.start_date = dateRange.value[0]
      params.end_date = dateRange.value[1]
    }
    const res = await playerDashboardApi.stats(params)
    kpi.value = res.data.kpi
    secondary.value = res.data.secondary
    rank.value = res.data.rank
  } finally {
    loading.value = false
  }
}

function onReset() {
  dateRange.value = null
  load()
}

onMounted(load)
</script>

<style scoped lang="scss">
.pd-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 接单额 7 项 / 二次 6 项，适度收窄单卡最小宽度 */
.pd-kpi-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.pd-charts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 20px;
}
.pd-ranks {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 20px;
}
.pd-card-body {
  padding: 16px 20px 20px;
}

@media (max-width: 1180px) {
  .pd-charts,
  .pd-ranks {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 900px) {
  .pd-kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 720px) {
  .pd-kpi-grid {
    grid-template-columns: 1fr;
  }
}
</style>
