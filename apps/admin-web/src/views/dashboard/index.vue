<template>
  <div class="db-page">
    <!-- 头部操作行 -->
    <div class="ap-page-head">
      <p class="ap-page-intro">实时监控平台运营核心指标</p>
      <div class="ap-page-head-actions">
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <!-- KPI 卡片 -->
    <div class="ap-kpi-grid">
      <article v-for="k in kpis" :key="k.label" class="ap-kpi">
        <div class="ap-kpi-top">
          <span class="ap-kpi-ic"><el-icon><component :is="k.icon" /></el-icon></span>
        </div>
        <p class="ap-kpi-label">{{ k.label }}</p>
        <p class="ap-kpi-value">{{ k.value }}</p>
        <p class="ap-kpi-note">{{ k.note }}</p>
      </article>
    </div>

    <!-- 图表区 -->
    <div class="db-charts">
      <article class="ap-card db-trend">
        <div class="ap-card-head">
          <div>
            <h2 class="ap-card-title">订单状态分布</h2>
            <p class="ap-card-sub">按当前订单状态统计数量</p>
          </div>
        </div>
        <div class="db-card-body">
          <e-chart v-if="hasOrderStatus" :option="statusBarOption" height="300px" />
          <el-empty v-else description="暂无订单数据" :image-size="90" />
        </div>
      </article>

      <article class="ap-card db-donut-card">
        <div class="ap-card-head">
          <div>
            <h2 class="ap-card-title">用户构成</h2>
            <p class="ap-card-sub">老板 / 陪玩占比</p>
          </div>
        </div>
        <div class="db-card-body">
          <e-chart v-if="userTotal > 0" :option="userDonutOption" height="300px" />
          <el-empty v-else description="暂无用户数据" :image-size="90" />
        </div>
      </article>
    </div>

    <!-- 实时动态 + 状态明细 -->
    <div class="db-secondary">
      <article class="ap-card db-feed">
        <div class="ap-card-head">
          <div>
            <h2 class="ap-card-title">实时订单动态</h2>
            <p class="ap-card-sub">最近平台交易事件</p>
          </div>
        </div>
        <div class="db-card-body">
          <ul v-if="recentOrders.length" class="db-timeline">
            <li v-for="o in recentOrders.slice(0, 6)" :key="o.id" class="db-tl-item">
              <span class="db-tl-dot" :style="{ background: statusColor(o.status) }" aria-hidden="true" />
              <div class="db-tl-body">
                <p class="db-tl-text">
                  <strong>{{ o.customer_name }}</strong>
                  下单 <strong>{{ o.provider_name || '待派单' }}</strong>
                  （{{ o.service_name_snapshot }} · {{ amountToXaCoin(o.amount) }}币）
                </p>
                <span class="db-tl-time">{{ formatDateTime(o.created_at) }} · {{ o.status_display }}</span>
              </div>
            </li>
          </ul>
          <el-empty v-else description="暂无订单动态" :image-size="90" />
        </div>
      </article>

      <article class="ap-card db-rank">
        <div class="ap-card-head">
          <div>
            <h2 class="ap-card-title">订单状态明细</h2>
            <p class="ap-card-sub">各状态订单占比</p>
          </div>
        </div>
        <div class="db-card-body">
          <ul class="db-rank-list">
            <li v-for="s in statusList" :key="s.key" class="db-rank-row">
              <el-tag :type="s.type" effect="light" size="small" round>{{ s.label }}</el-tag>
              <span class="db-rank-bar">
                <span class="db-rank-fill" :style="{ width: s.pct + '%', background: statusColor(s.key) }" />
              </span>
              <span class="db-rank-count">{{ s.count }}</span>
              <span class="db-rank-pct">{{ s.pct }}%</span>
            </li>
          </ul>
        </div>
      </article>
    </div>

    <!-- 近期订单表 -->
    <article class="ap-card db-table-card">
      <div class="ap-card-head">
        <div>
          <h2 class="ap-card-title">近期重点订单</h2>
          <p class="ap-card-sub">共 {{ recentOrders.length }} 笔</p>
        </div>
      </div>
      <el-table :data="recentOrders" v-loading="loading">
        <el-table-column prop="order_no" label="订单号" min-width="200" />
        <el-table-column prop="customer_name" label="老板" width="120" />
        <el-table-column label="陪玩" width="120">
          <template #default="{ row }">{{ row.provider_name || '-' }}</template>
        </el-table-column>
        <el-table-column prop="service_name_snapshot" label="服务" min-width="140" />
        <el-table-column label="金额" width="110" align="right">
          <template #default="{ row }">{{ amountToXaCoin(row.amount) }} 币</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="ORDER_STATUS[row.status]?.type" effect="light" round>
              {{ row.status_display }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="下单时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </article>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { EChartsOption } from 'echarts'
import { Refresh } from '@element-plus/icons-vue'
import EChart from '@/components/EChart.vue'
import { dashboardApi } from '@/api/modules'
import { amountToXaCoin, formatDateTime } from '@/utils/format'
import { ORDER_STATUS } from '@/utils/dict'

const stats = ref<any>({})
const recentOrders = ref<any[]>([])
const loading = ref(false)

// KPI —— 全部绑定真实字段
const kpis = computed(() => [
  {
    label: '订单总成交额',
    value: amountToXaCoin(stats.value.order_amount_total) + '币',
    note: `实付合计 ${amountToXaCoin(stats.value.paid_amount_total)}兴安币`,
    icon: 'Money',
  },
  {
    label: '订单总数',
    value: (stats.value.order_total ?? 0).toLocaleString(),
    note: `今日新增 ${stats.value.today_order_total ?? 0} 单`,
    icon: 'Tickets',
  },
  {
    label: '陪玩总数',
    value: (stats.value.provider_total ?? 0).toLocaleString(),
    note: '平台注册陪玩师',
    icon: 'Avatar',
  },
  {
    label: '用户总数',
    value: (stats.value.user_total ?? 0).toLocaleString(),
    note: `老板 ${stats.value.customer_total ?? 0} 人`,
    icon: 'User',
  },
])

const orderStatusMap = computed<Record<string, number>>(() => stats.value.order_status || {})
const hasOrderStatus = computed(() => Object.keys(orderStatusMap.value).length > 0)
const orderStatusTotal = computed(() =>
  Object.values(orderStatusMap.value).reduce((a, b) => a + b, 0),
)

const statusList = computed(() =>
  Object.entries(ORDER_STATUS).map(([key, v]) => {
    const count = orderStatusMap.value[key] ?? 0
    const total = orderStatusTotal.value
    return { key, label: v.label, type: v.type, count, pct: total ? Math.round((count / total) * 100) : 0 }
  }),
)

const STATUS_COLORS: Record<string, string> = {
  PENDING: 'var(--muted-foreground)',
  GRABBED: 'var(--primary)',
  IN_SERVICE: 'var(--warning)',
  COMPLETED: 'var(--success)',
  CANCELLED: 'var(--destructive)',
}
function statusColor(key: string) {
  return STATUS_COLORS[key] || 'var(--primary)'
}

const statusBarOption = computed<EChartsOption>(() => {
  const items = statusList.value.filter((s) => s.count > 0)
  return {
    grid: { left: 8, right: 16, top: 20, bottom: 8, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'category', data: items.map((s) => s.label), axisLine: { lineStyle: { color: 'var(--border)' } } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: 'var(--border)' } } },
    series: [
      {
        type: 'bar',
        barWidth: '46%',
        data: items.map((s) => ({ value: s.count, itemStyle: { color: statusColor(s.key) } })),
        itemStyle: { borderRadius: [8, 8, 0, 0] },
      },
    ],
  }
})

const userTotal = computed(() => stats.value.user_total ?? 0)
const userDonutOption = computed<EChartsOption>(() => {
  const customer = stats.value.customer_total ?? 0
  const provider = stats.value.provider_total ?? 0
  const other = Math.max(0, userTotal.value - customer - provider)
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, icon: 'circle' },
    series: [
      {
        type: 'pie',
        radius: ['55%', '78%'],
        center: ['50%', '44%'],
        avoidLabelOverlap: false,
        label: { show: true, position: 'center', formatter: `${userTotal.value}\n用户`, fontSize: 18, fontWeight: 700, color: 'var(--foreground)' },
        labelLine: { show: false },
        data: [
          { value: customer, name: '老板', itemStyle: { color: 'var(--primary)' } },
          { value: provider, name: '陪玩', itemStyle: { color: 'var(--success)' } },
          { value: other, name: '其他', itemStyle: { color: 'var(--muted-foreground)' } },
        ].filter((d) => d.value > 0),
      },
    ],
  }
})

async function load() {
  loading.value = true
  try {
    const res = await dashboardApi.stats()
    stats.value = res.data
    recentOrders.value = res.data.recent_orders || []
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped lang="scss">
.db-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.db-charts {
  display: grid;
  grid-template-columns: 1.7fr 1fr;
  gap: 20px;
}
.db-secondary {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}
.db-card-body {
  padding: 20px 24px;
}

/* 时间线 */
.db-timeline {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.db-tl-item {
  display: flex;
  gap: 12px;
}
.db-tl-dot {
  flex: 0 0 10px;
  width: 10px;
  height: 10px;
  margin-top: 5px;
  border-radius: 999px;
}
.db-tl-body {
  min-width: 0;
}
.db-tl-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--foreground);
}
.db-tl-time {
  font-size: 12px;
  color: var(--muted-foreground);
}

/* 排行/状态明细 */
.db-rank-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.db-rank-row {
  display: grid;
  grid-template-columns: 72px 1fr 48px 42px;
  align-items: center;
  gap: 12px;
}
.db-rank-bar {
  height: 8px;
  border-radius: 999px;
  background: var(--secondary);
  overflow: hidden;
}
.db-rank-fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  transition: width 0.4s ease;
}
.db-rank-count {
  font-size: 13px;
  font-weight: 600;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.db-rank-pct {
  font-size: 12px;
  color: var(--muted-foreground);
  text-align: right;
}

.db-table-card :deep(.el-table) {
  margin: 0 8px 8px;
}

@media (max-width: 1180px) {
  .db-charts,
  .db-secondary {
    grid-template-columns: 1fr;
  }
}
</style>
