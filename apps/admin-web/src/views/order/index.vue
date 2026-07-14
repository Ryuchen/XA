<template>
  <div class="od-page">
    <!-- 头部操作行 -->
    <div class="ap-page-head">
      <p class="ap-page-intro">管理平台全部陪玩订单与售后</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('order:dispatch')" type="primary" :icon="Plus" @click="onDispatch">快捷派单</el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="orderApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-autocomplete
          v-model="query.order_no"
          :fetch-suggestions="querySuggest('order_no')"
          placeholder="订单号"
          clearable
          value-key="value"
          style="width: 200px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.customer_username"
          :fetch-suggestions="querySuggest('customer_username')"
          placeholder="老板账号"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.customer_nickname"
          :fetch-suggestions="querySuggest('customer_nickname')"
          placeholder="老板昵称"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.customer_phone"
          :fetch-suggestions="querySuggest('customer_phone')"
          placeholder="手机号"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.status" placeholder="订单状态" clearable style="width: 130px" @change="reload">
          <el-option v-for="(v, k) in ORDER_STATUS" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-select v-model="query.payment_status" placeholder="支付状态" clearable style="width: 130px" @change="reload">
          <el-option v-for="(v, k) in PAYMENT_STATUS" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
      </template>

      <el-table-column prop="order_no" label="订单号" min-width="190" />
      <el-table-column prop="customer_name" label="老板" width="110" />
      <el-table-column prop="provider_name" label="陪玩" width="130">
        <template #default="{ row }">
          <template v-if="row.providers && row.providers.length">
            <el-tag v-if="row.escort_mode === 'DOUBLE'" size="small" type="warning" effect="light" round style="margin-right: 4px">双陪</el-tag>
            <span>{{ row.providers.map((p: any) => p.provider_name).join('、') }}</span>
          </template>
          <span v-else>{{ row.provider_name || '-' }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="service_name_snapshot" label="服务" min-width="130" />
      <el-table-column prop="support_contact_name_snapshot" label="负责客服" width="110">
        <template #default="{ row }">{{ row.support_contact_name_snapshot || '-' }}</template>
      </el-table-column>
      <el-table-column label="金额" width="100">
        <template #default="{ row }">{{ amountToXaCoin(row.amount) }} 币</template>
      </el-table-column>
      <el-table-column label="支付" width="90">
        <template #default="{ row }">
          <el-tag :type="PAYMENT_STATUS[row.payment_status]?.type" effect="light" round>{{ row.payment_status_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="ORDER_STATUS[row.status]?.type" effect="light" round>{{ row.status_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="下单时间" width="160">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="280" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="onDetail(row)">详情</el-button>
          <el-button
            v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING'"
            link
            type="primary"
            @click="onAssign(row)"
          >派单</el-button>
          <el-button
            v-if="auth.hasPerm('order:dispatch') && row.status === 'GRABBED'"
            link
            type="primary"
            @click="onStart(row)"
          >开始</el-button>
          <el-button
            v-if="auth.hasPerm('order:settle') && row.status === 'IN_SERVICE'"
            link
            type="success"
            @click="onComplete(row)"
          >完成</el-button>
          <el-button
            v-if="auth.hasPerm('order:cancel') && row.status === 'PENDING'"
            link
            type="warning"
            @click="onCancel(row)"
          >取消</el-button>
          <el-button
            v-if="auth.hasPerm('order:refund') && ['GRABBED', 'IN_SERVICE'].includes(row.status)"
            link
            type="danger"
            @click="onRefund(row)"
          >退款</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-drawer v-model="detailVisible" title="订单详情" size="520px">
    <template v-if="current">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="订单号">{{ current.order_no }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ current.status_display }}</el-descriptions-item>
        <el-descriptions-item label="支付状态">{{ current.payment_status_display }}</el-descriptions-item>
        <el-descriptions-item label="老板">{{ current.customer_name }}（{{ current.customer_phone_snapshot }}）</el-descriptions-item>
        <el-descriptions-item label="陪玩模式">{{ current.escort_mode_display }}</el-descriptions-item>
        <el-descriptions-item label="陪玩">{{ providerText(current) }}</el-descriptions-item>
        <el-descriptions-item label="服务">{{ current.service_name_snapshot }}</el-descriptions-item>
        <el-descriptions-item label="负责客服">{{ current.support_contact_name_snapshot || '-' }}</el-descriptions-item>
        <el-descriptions-item label="单价">{{ amountToXaCoin(current.unit_price_snapshot) }} 币</el-descriptions-item>
        <el-descriptions-item label="局数">{{ current.game_rounds }}</el-descriptions-item>
        <el-descriptions-item v-if="current.original_amount" label="原价">{{ amountToXaCoin(current.original_amount) }} 币</el-descriptions-item>
        <el-descriptions-item v-if="current.boss_discount" label="老板折扣">-{{ amountToXaCoin(current.boss_discount) }} 币</el-descriptions-item>
        <el-descriptions-item v-if="current.promo_discount" label="活动折扣">-{{ amountToXaCoin(current.promo_discount) }} 币</el-descriptions-item>
        <el-descriptions-item v-if="current.coupon_discount" label="优惠券折扣">-{{ amountToXaCoin(current.coupon_discount) }} 币</el-descriptions-item>
        <el-descriptions-item label="实付金额">{{ amountToXaCoin(current.amount) }} 币</el-descriptions-item>
        <el-descriptions-item v-if="current.promotion_title" label="命中活动">{{ current.promotion_title }}</el-descriptions-item>
        <template v-if="current.providers && current.providers.length">
          <el-descriptions-item
            v-for="(p, i) in current.providers"
            :key="p.id"
            :label="current.providers.length > 1 ? `打手${i + 1}` : '打手'"
          >
            {{ p.provider_name }} · {{ orderProviderCommissionDesc(p) }} · 实得 {{ amountToXaCoin(p.provider_income) }} 币
            <span v-if="p.settled_at" class="od-log-op"> · 已结算</span>
          </el-descriptions-item>
        </template>
        <template v-else>
          <el-descriptions-item label="平台抽成率">{{ current.commission_rate }}%</el-descriptions-item>
          <el-descriptions-item label="陪玩实得">{{ amountToXaCoin(current.provider_income) }} 币</el-descriptions-item>
        </template>
        <el-descriptions-item label="推荐人">{{ current.inviter_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="推荐人分佣">{{ amountToXaCoin(current.inviter_commission) }} 币</el-descriptions-item>
        <el-descriptions-item label="店铺留存">{{ amountToXaCoin(current.shop_income) }} 币</el-descriptions-item>
        <el-descriptions-item label="备注">{{ current.remark || '-' }}</el-descriptions-item>
        <el-descriptions-item label="取消原因">{{ current.cancel_reason || '-' }}</el-descriptions-item>
        <el-descriptions-item label="下单时间">{{ formatDateTime(current.created_at) }}</el-descriptions-item>
      </el-descriptions>

      <el-divider>状态流水</el-divider>
      <el-timeline>
        <el-timeline-item
          v-for="log in logs"
          :key="log.id"
          :timestamp="formatDateTime(log.created_at)"
        >
          {{ log.action_display }}
          <span v-if="log.from_status">（{{ log.from_status }} → {{ log.to_status }}）</span>
          <span v-if="log.reason"> · {{ log.reason }}</span>
          <span class="od-log-op"> · {{ log.operator_name }}</span>
        </el-timeline-item>
      </el-timeline>
    </template>
  </el-drawer>

  <el-dialog v-model="dispatchVisible" title="客服快捷派单" width="640px">
    <el-form label-position="top">
      <div class="od-form-section">
        <p class="od-section-title">下单信息</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="下单老板">
              <el-select
                v-model="dispatchForm.customer_id"
                filterable
                remote
                reserve-keyword
                placeholder="输入账号/昵称搜索"
                :remote-method="searchBoss"
                :loading="bossLoading"
                style="width: 100%"
                @change="onBossChange"
              >
                <el-option
                  v-for="u in bossOptions"
                  :key="u.id"
                  :label="`${u.nickname || u.username}（余额 ${amountToXaCoin(u.wallet_balance)}币）`"
                  :value="u.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="服务">
              <el-select
                v-model="dispatchForm.service_id"
                filterable
                placeholder="选择服务"
                style="width: 100%"
                @change="onServiceChange"
              >
                <el-option
                  v-for="s in serviceOptions"
                  :key="s.id"
                  :label="`${s.name}（${amountToXaCoin(s.price)}币）`"
                  :value="s.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
      </div>

      <div class="od-form-section">
        <p class="od-section-title">派单设置</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="局数">
              <el-input-number v-model="dispatchForm.game_rounds" :min="1" :step="1" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="陪玩模式">
              <el-radio-group v-model="dispatchForm.escort_mode" @change="onEscortModeChange">
                <el-radio-button label="SINGLE">单陪</el-radio-button>
                <el-radio-button label="DOUBLE">双陪</el-radio-button>
              </el-radio-group>
            </el-form-item>
          </el-col>
        </el-row>

        <div
          v-for="(slot, idx) in dispatchForm.providers"
          :key="idx"
          class="od-provider-row"
        >
          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item :label="`打手 ${idx + 1}`">
                <el-select
                  v-model="slot.provider_id"
                  filterable
                  remote
                  reserve-keyword
                  clearable
                  :placeholder="dispatchForm.escort_mode === 'SINGLE' ? '留空则进入待接单大厅' : '选择打手'"
                  :remote-method="searchProvider"
                  :loading="providerLoading"
                  style="width: 100%"
                  @change="onProviderPicked(idx)"
                >
                  <el-option
                    v-for="p in providerOptions"
                    :key="p.user"
                    :label="`${p.display_name || p.username}<${p.escort_no || '-'}>${p.level_name || '未定级'}`"
                    :value="p.user"
                  />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="5">
              <el-form-item label="抽成方式">
                <el-select v-model="slot.commission_type" style="width: 100%">
                  <el-option label="百分比" value="PERCENT" />
                  <el-option label="固定金额" value="FIXED" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="7">
              <el-form-item :label="slot.commission_type === 'FIXED' ? '固定抽成(兴安币)' : '抽成率(%)'">
                <el-input-number
                  v-if="slot.commission_type === 'PERCENT'"
                  v-model="slot.commission_rate"
                  :min="0"
                  :max="100"
                  :step="1"
                  controls-position="right"
                  style="width: 100%"
                />
                <el-input-number
                  v-else
                  v-model="slot.commission_fixed"
                  :min="0"
                  :precision="2"
                  :step="1"
                  controls-position="right"
                  style="width: 100%"
                />
              </el-form-item>
            </el-col>
          </el-row>
          <p v-if="slot.provider_id" class="od-provider-tip">
            实得 {{ amountToXaCoin(providerIncome(slot)) }} 币（{{ providerIncomeDesc(slot) }}）
            <template v-if="slot.source_label">
              <el-tag size="small" type="info" effect="plain" round style="margin-left: 8px">建议来源：{{ slot.source_label }}</el-tag>
              <el-button
                v-if="slot.commission_type === 'PERCENT' && typeof slot.suggested_rate === 'number' && slot.commission_rate !== slot.suggested_rate"
                link
                type="primary"
                size="small"
                @click="restoreSuggestedRate(idx)"
              >恢复建议 {{ slot.suggested_rate }}%</el-button>
            </template>
          </p>
        </div>

        <el-form-item label="备注">
          <el-input v-model="dispatchForm.remark" type="textarea" :rows="2" placeholder="可选" />
        </el-form-item>
      </div>

      <div class="od-summary" :class="{ 'od-summary--danger': insufficient }">
        <div class="od-summary-item">
          <span class="od-summary-label">老板余额</span>
          <span class="od-summary-value">{{ currentBoss ? `${amountToXaCoin(currentBoss.wallet_balance)}币` : '—' }}</span>
        </div>
        <div class="od-summary-item">
          <span class="od-summary-label">预计扣款</span>
          <span class="od-summary-value">{{ estimateAmount > 0 ? `${amountToXaCoin(estimateAmount)}币` : '—' }}</span>
        </div>
        <div v-if="insufficient" class="od-summary-warn">余额不足以支付本次派单</div>
      </div>
    </el-form>
    <template #footer>
      <el-button @click="dispatchVisible = false">取消</el-button>
      <el-button type="primary" :loading="dispatching" @click="onDispatchSave">确定派单</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="assignVisible" title="指派待接单订单" width="460px">
    <el-form label-position="top">
      <el-form-item label="订单">
        <el-input :model-value="assignOrder ? `${assignOrder.order_no} · ${assignOrder.service_name_snapshot}` : ''" disabled />
      </el-form-item>
      <el-form-item label="陪玩">
        <el-select
          v-model="assignProviderId"
          filterable
          remote
          reserve-keyword
          placeholder="输入昵称或账号搜索可接单陪玩"
          :remote-method="searchProvider"
          :loading="providerLoading"
          style="width: 100%"
        >
          <el-option
            v-for="p in providerOptions"
            :key="p.id"
            :label="p.display_name || p.nickname || p.username"
            :value="p.user || p.user_id || p.id"
          />
        </el-select>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="assignVisible = false">取消</el-button>
      <el-button type="primary" :loading="assigning" @click="onAssignSave">确认派单</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import { orderApi, serviceItemApi, userApi, escortApi } from '@/api/modules'
import { request } from '@/api/request'
import { ORDER_STATUS, PAYMENT_STATUS } from '@/utils/dict'
import { amountToXaCoin, formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({
  order_no: '',
  customer_username: '',
  customer_nickname: '',
  customer_phone: '',
  status: '',
  payment_status: '',
})

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询订单后，
 * 取返回记录对应字段值去重后作为下拉提示。
 */
function querySuggest(field: 'order_no' | 'customer_username' | 'customer_nickname' | 'customer_phone') {
  return async (keyword: string, cb: (items: any[]) => void) => {
    if (!keyword) {
      cb([])
      return
    }
    try {
      const res = (await orderApi.list({ [field]: keyword, page: 1, page_size: 10 })) as any
      const seen = new Set<string>()
      const items: { value: string }[] = []
      for (const o of res.data?.list || []) {
        const value = o[field] || ''
        if (value && !seen.has(value)) {
          seen.add(value)
          items.push({ value })
        }
      }
      cb(items)
    } catch {
      cb([])
    }
  }
}

const detailVisible = ref(false)
const current = ref<any>(null)
const logs = ref<any[]>([])
const assignVisible = ref(false)
const assigning = ref(false)
const assignOrder = ref<any>(null)
const assignProviderId = ref<number | undefined>()

async function onDetail(row: any) {
  current.value = row
  detailVisible.value = true
  const res = await request({ url: `/orders/${row.id}/logs/`, method: 'get' }) as any
  logs.value = res.data || []
}

async function askReason(title: string): Promise<string | null> {
  try {
    const { value } = await ElMessageBox.prompt('请填写原因', title, {
      inputPlaceholder: '原因（取消可留空，退款必填）',
    })
    return value || ''
  } catch {
    return null
  }
}

async function onCancel(row: any) {
  const reason = await askReason('取消订单')
  if (reason === null) return
  await orderApi.action(row.id, 'cancel', { reason })
  ElMessage.success('已取消')
  tableRef.value.load()
}

async function onRefund(row: any) {
  const reason = await askReason('订单退款')
  if (reason === null) return
  if (!reason) {
    ElMessage.warning('退款必须填写原因')
    return
  }
  await orderApi.action(row.id, 'refund', { reason })
  ElMessage.success('已退款')
  tableRef.value.load()
}

function onAssign(row: any) {
  assignOrder.value = row
  assignProviderId.value = undefined
  providerOptions.value = []
  assignVisible.value = true
}

async function onAssignSave() {
  if (!assignOrder.value || !assignProviderId.value) {
    ElMessage.warning('请选择陪玩')
    return
  }
  assigning.value = true
  try {
    await orderApi.action(assignOrder.value.id, 'assign', { provider_id: assignProviderId.value })
    ElMessage.success('派单成功')
    assignVisible.value = false
    tableRef.value.load()
  } finally {
    assigning.value = false
  }
}

// ---------------- 客服快捷派单 ----------------
const dispatchVisible = ref(false)
const dispatching = ref(false)
const bossLoading = ref(false)
const providerLoading = ref(false)
const bossOptions = ref<any[]>([])
const providerOptions = ref<any[]>([])
const serviceOptions = ref<any[]>([])
const currentBoss = ref<any>(null)
const currentService = ref<any>(null)
const dispatchForm = reactive<any>({
  customer_id: undefined,
  service_id: undefined,
  game_rounds: 1,
  escort_mode: 'SINGLE',
  providers: [{ provider_id: undefined, commission_type: 'PERCENT', commission_rate: 0, commission_fixed: 0 }],
  remark: '',
})

const estimateAmount = computed(() => {
  if (!currentService.value) return 0
  return currentService.value.price * (dispatchForm.game_rounds || 1)
})

function providerIncome(slot: any): number {
  if (slot.commission_type === 'FIXED') {
    const fixedFen = Math.max(Math.round((Number(slot.commission_fixed) || 0) * 10), 0)
    return Math.max(estimateAmount.value - Math.min(fixedFen, estimateAmount.value), 0)
  }
  const rate = Math.min(Math.max(Number(slot.commission_rate) || 0, 0), 100)
  return Math.floor((estimateAmount.value * (100 - rate)) / 100)
}

function providerIncomeDesc(slot: any): string {
  if (slot.commission_type === 'FIXED') {
    const fixedFen = Math.max(Math.round((Number(slot.commission_fixed) || 0) * 10), 0)
    return `订单金额 ${amountToXaCoin(estimateAmount.value)}币 - 固定抽成 ${amountToXaCoin(fixedFen)}币`
  }
  return `订单金额 ${amountToXaCoin(estimateAmount.value)}币 × ${100 - (slot.commission_rate || 0)}%`
}

function orderProviderCommissionDesc(provider: any): string {
  if (provider.commission_type === 'FIXED') {
    return `固定抽成 ${amountToXaCoin(provider.commission_fixed)}币`
  }
  return `抽成 ${provider.commission_rate}%`
}

const insufficient = computed(() => {
  if (!currentBoss.value || estimateAmount.value <= 0) return false
  return currentBoss.value.wallet_balance < estimateAmount.value
})

function onEscortModeChange(mode: string | number | boolean | undefined) {
  if (mode === 'DOUBLE') {
    while (dispatchForm.providers.length < 2) {
      dispatchForm.providers.push({ provider_id: undefined, commission_type: 'PERCENT', commission_rate: 0, commission_fixed: 0 })
    }
  } else {
    dispatchForm.providers.splice(1)
  }
}

// 选中打手后带出建议抽成率（客服可再手改）
async function onProviderPicked(idx: number) {
  const slot = dispatchForm.providers[idx]
  if (!slot.provider_id || !dispatchForm.service_id) return
  try {
    const res = await request({
      url: '/orders/suggest-commission/',
      method: 'get',
      params: { service_id: dispatchForm.service_id, provider_id: slot.provider_id },
    }) as any
    if (res.data && typeof res.data.commission_rate === 'number') {
      slot.commission_rate = res.data.commission_rate
      slot.suggested_rate = res.data.commission_rate
      slot.source_label = res.data.source_label || ''
    }
  } catch {
    // 建议抽成率获取失败不阻断，保持当前值
  }
}

// 将某打手抽成率恢复为系统建议值
function restoreSuggestedRate(idx: number) {
  const slot = dispatchForm.providers[idx]
  if (typeof slot.suggested_rate === 'number') {
    slot.commission_rate = slot.suggested_rate
  }
}

async function onDispatch() {
  Object.assign(dispatchForm, {
    customer_id: undefined,
    service_id: undefined,
    game_rounds: 1,
    escort_mode: 'SINGLE',
    providers: [{ provider_id: undefined, commission_type: 'PERCENT', commission_rate: 0, commission_fixed: 0 }],
    remark: '',
  })
  currentBoss.value = null
  currentService.value = null
  bossOptions.value = []
  providerOptions.value = []
  dispatchVisible.value = true
  // 预加载服务列表
  const res = await serviceItemApi.list({ is_active: 1, page_size: 200 }) as any
  serviceOptions.value = res.data?.list || []
}

async function searchBoss(keyword: string) {
  if (!keyword) { bossOptions.value = []; return }
  bossLoading.value = true
  try {
    const res = await userApi.list({ role: 'CUSTOMER', keyword, page_size: 20 }) as any
    bossOptions.value = res.data?.list || []
  } finally {
    bossLoading.value = false
  }
}

function onBossChange(id: number) {
  currentBoss.value = bossOptions.value.find((u) => u.id === id) || null
}

async function onServiceChange(id: number) {
  currentService.value = serviceOptions.value.find((s) => s.id === id) || null
  await Promise.all(
    dispatchForm.providers
      .map((_: any, idx: number) => idx)
      .filter((idx: number) => dispatchForm.providers[idx].provider_id)
      .map((idx: number) => onProviderPicked(idx)),
  )
}

async function searchProvider(keyword: string) {
  if (!keyword) { providerOptions.value = []; return }
  providerLoading.value = true
  try {
    const res = await escortApi.list({ keyword, page_size: 20 }) as any
    providerOptions.value = res.data?.list || []
  } finally {
    providerLoading.value = false
  }
}

async function onDispatchSave() {
  if (!dispatchForm.customer_id) { ElMessage.warning('请选择下单老板'); return }
  if (!dispatchForm.service_id) { ElMessage.warning('请选择服务'); return }
  if (!dispatchForm.game_rounds || dispatchForm.game_rounds < 1) {
    ElMessage.warning('局数至少为 1')
    return
  }
  const picked = dispatchForm.providers.filter((s: any) => s.provider_id)
  if (dispatchForm.escort_mode === 'DOUBLE') {
    if (picked.length !== 2) { ElMessage.warning('双陪需指定 2 名打手'); return }
    const ids = picked.map((s: any) => s.provider_id)
    if (new Set(ids).size !== ids.length) { ElMessage.warning('不可重复指派同一打手'); return }
  }
  const providersPayload = picked.map((s: any) => (
    s.commission_type === 'FIXED'
      ? {
          provider_id: s.provider_id,
          commission_type: 'FIXED',
          commission_fixed: Math.max(Math.round((Number(s.commission_fixed) || 0) * 10), 0),
        }
      : {
          provider_id: s.provider_id,
          commission_type: 'PERCENT',
          commission_rate: s.commission_rate,
        }
  ))
  dispatching.value = true
  try {
    await orderApi.collectionAction('dispatch', {
      customer_id: dispatchForm.customer_id,
      service_id: dispatchForm.service_id,
      game_rounds: dispatchForm.game_rounds,
      escort_mode: dispatchForm.escort_mode,
      providers: providersPayload,
      remark: dispatchForm.remark,
    })
    ElMessage.success('派单成功')
    dispatchVisible.value = false
    tableRef.value.load()
  } finally {
    dispatching.value = false
  }
}

function providerText(order: any): string {
  if (order.providers && order.providers.length) {
    return order.providers.map((p: any) => p.provider_name).join('、')
  }
  return order.provider_name || '-'
}

async function onStart(row: any) {
  await orderApi.action(row.id, 'start')
  ElMessage.success('已开始服务')
  tableRef.value.load()
}

async function onComplete(row: any) {
  try {
    await ElMessageBox.confirm('确认完成该订单？将给打手钱包结算入账。', '完成结算', {
      type: 'warning',
    })
  } catch {
    return
  }
  await orderApi.action(row.id, 'complete')
  ElMessage.success('订单已完成')
  tableRef.value.load()
}
</script>

<style scoped lang="scss">
.od-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 让 CrudTable 内置的外层 padding 归零，融入 od-page 的间距节奏 */
.od-page :deep(.page-container) {
  padding: 0;
}

.tip {
  font-size: 12px;
  color: var(--muted-foreground);
}
.od-log-op {
  color: var(--muted-foreground);
}

.od-form-section {
  margin-bottom: 4px;
}
.od-form-section + .od-form-section {
  margin-top: 8px;
}
.od-section-title {
  margin: 0 0 12px;
  padding-left: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--foreground);
  border-left: 3px solid var(--primary);
  line-height: 1.2;
}
.od-form-section :deep(.el-form-item) {
  margin-bottom: 16px;
}

.od-provider-row {
  padding: 12px 12px 0;
  margin-bottom: 12px;
  border-radius: 8px;
  background: var(--muted);
  border: 1px solid var(--border);
}
.od-provider-tip {
  margin: -4px 0 12px;
  padding-left: 2px;
  font-size: 12px;
  color: var(--muted-foreground);
}

.od-summary {
  display: flex;
  align-items: center;
  gap: 32px;
  margin-top: 8px;
  padding: 14px 16px;
  border-radius: 8px;
  background: var(--muted);
  border: 1px solid var(--border);
}
.od-summary--danger {
  background: color-mix(in srgb, var(--destructive) 8%, transparent);
  border-color: var(--destructive);
}
.od-summary-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.od-summary-label {
  font-size: 12px;
  color: var(--muted-foreground);
}
.od-summary-value {
  font-size: 18px;
  font-weight: 600;
  font-family: var(--font-mono);
  color: var(--foreground);
}
.od-summary-warn {
  margin-left: auto;
  font-size: 13px;
  font-weight: 600;
  color: var(--destructive);
}
</style>
