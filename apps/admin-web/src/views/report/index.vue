<template>
  <div class="rp-page">
    <!-- 头部操作行 -->
    <div class="ap-page-head">
      <p class="ap-page-intro">审核已完成订单的入队、结单与战绩凭证；平台订单不会重复结算</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('report:audit')" type="warning" plain @click="openCommission">
          全局抽成配置：{{ commissionRate }}%
        </el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="reportApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-autocomplete
          v-model="query.game_name"
          :fetch-suggestions="querySuggest('game_name')"
          placeholder="游戏/项目"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.provider_username"
          :fetch-suggestions="querySuggest('provider_username')"
          placeholder="陪玩账号"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.provider_nickname"
          :fetch-suggestions="querySuggest('provider_nickname')"
          placeholder="陪玩昵称"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.status" placeholder="审核状态" clearable style="width: 130px" @change="reload">
          <el-option v-for="(v, k) in REPORT_STATUS" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
    <el-table-column prop="provider_name" label="陪玩" min-width="110" />
    <el-table-column prop="game_name" label="游戏/项目" min-width="140" />
    <el-table-column label="报单金额" width="110">
      <template #default="{ row }">{{ amountToXaCoin(row.amount) }} 币</template>
    </el-table-column>
    <el-table-column label="抽成来源" width="170">
      <template #default="{ row }">
        <span>{{ reportCommissionDesc(row) }}</span>
      </template>
    </el-table-column>
    <el-table-column label="实得" width="130">
      <template #default="{ row }">
        <span v-if="row.status === 'APPROVED'">{{ amountToXaCoin(row.payout_amount) }} 币（{{ row.commission_rate }}%）</span>
        <span v-else>预计 {{ amountToXaCoin(reportEstimatedPayout(row)) }} 币</span>
      </template>
    </el-table-column>
    <el-table-column label="状态" width="90">
      <template #default="{ row }">
        <el-tag :type="REPORT_STATUS[row.status]?.type" effect="light" round>{{ row.status_display }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="提交时间" width="160">
      <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
    </el-table-column>
    <el-table-column label="操作" width="200" fixed="right">
      <template #default="{ row }">
        <el-button link type="primary" @click="onDetail(row)">详情</el-button>
        <template v-if="auth.hasPerm('report:audit') && row.status === 'PENDING'">
          <el-button link type="success" @click="onApprove(row)">通过</el-button>
          <el-button link type="danger" @click="onReject(row)">驳回</el-button>
        </template>
      </template>
    </el-table-column>
    </crud-table>
  </div>

  <el-drawer v-model="detailVisible" title="报单详情" size="480px">
    <template v-if="current">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="陪玩">{{ current.provider_name }}</el-descriptions-item>
        <el-descriptions-item label="游戏/项目">{{ current.game_name }}</el-descriptions-item>
        <el-descriptions-item v-if="current.order_no" label="订单号">{{ current.order_no }}</el-descriptions-item>
        <el-descriptions-item label="报单金额">{{ amountToXaCoin(current.amount) }} 币</el-descriptions-item>
        <el-descriptions-item label="状态">{{ current.status_display }}</el-descriptions-item>
        <el-descriptions-item label="抽成来源">{{ reportCommissionDesc(current) }}</el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'PENDING'" label="预计入账">
          {{ amountToXaCoin(reportEstimatedPayout(current)) }} 币
        </el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'APPROVED'" label="实际入账">
          {{ amountToXaCoin(current.payout_amount) }} 币（抽成 {{ current.commission_rate }}%）
        </el-descriptions-item>
        <el-descriptions-item label="服务内容">{{ current.description || '-' }}</el-descriptions-item>
        <el-descriptions-item label="备注">{{ current.remark || '-' }}</el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'REJECTED'" label="驳回原因">
          {{ current.audit_remark || '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="审核人">{{ current.auditor_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="提交时间">{{ formatDateTime(current.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="审核时间">{{ current.audited_at ? formatDateTime(current.audited_at) : '-' }}</el-descriptions-item>
      </el-descriptions>

      <el-divider>订单凭证</el-divider>
      <template v-if="current.order">
        <div class="evidence-section">
          <div class="evidence-title">入队截图</div>
          <el-image v-if="current.entry_image_url" :src="current.entry_image_url" :preview-src-list="allEvidenceUrls(current)" fit="cover" class="evidence-image" />
          <el-empty v-else description="未上传" :image-size="50" />
        </div>
        <div class="evidence-section">
          <div class="evidence-title">结单截图</div>
          <el-image v-if="current.completion_image_url" :src="current.completion_image_url" :preview-src-list="allEvidenceUrls(current)" fit="cover" class="evidence-image" />
          <el-empty v-else description="未上传" :image-size="50" />
        </div>
        <div class="evidence-section">
          <div class="evidence-title">战绩截图（{{ current.result_image_urls?.length || 0 }} 张）</div>
          <div v-if="current.result_image_urls?.length" class="evidence-grid">
            <el-image v-for="url in current.result_image_urls" :key="url" :src="url" :preview-src-list="allEvidenceUrls(current)" fit="cover" class="evidence-image" />
          </div>
          <el-empty v-else description="未上传" :image-size="50" />
        </div>
      </template>
      <template v-else>
        <el-image v-if="current.proof_image_url" :src="current.proof_image_url" :preview-src-list="[current.proof_image_url]" fit="contain" style="width: 100%; max-height: 360px" />
        <el-empty v-else description="无凭证" :image-size="80" />
      </template>
    </template>
  </el-drawer>

  <el-dialog v-model="commissionVisible" title="全局平台抽成配置" width="380px">
    <el-form label-width="100px">
      <el-form-item label="抽成率(%)">
        <el-input-number v-model="commissionInput" :min="0" :max="100" :step="1" style="width: 100%" />
        <div class="tip">仅在陪玩未设置启用等级时生效；陪玩实得 = 报单金额 ×（1 − 抽成率）</div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="commissionVisible = false">取消</el-button>
      <el-button type="primary" :loading="savingRate" @click="saveCommission">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import { reportApi, commissionApi } from '@/api/modules'
import { REPORT_STATUS } from '@/utils/dict'
import { amountToXaCoin, formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({
  game_name: '',
  provider_username: '',
  provider_nickname: '',
  status: '',
})

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询报单后，
 * 取返回记录对应字段值去重后作为下拉提示。
 */
function querySuggest(field: 'game_name' | 'provider_username' | 'provider_nickname') {
  return async (keyword: string, cb: (items: any[]) => void) => {
    if (!keyword) {
      cb([])
      return
    }
    try {
      const res = (await reportApi.list({ [field]: keyword, page: 1, page_size: 10 })) as any
      const seen = new Set<string>()
      const items: { value: string }[] = []
      for (const r of res.data?.list || []) {
        const value = r[field] || ''
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

const commissionRate = ref<number>(0)
const commissionVisible = ref(false)
const commissionInput = ref<number>(0)
const savingRate = ref(false)

async function loadCommission() {
  const res = await commissionApi.get()
  commissionRate.value = res.data.rate
}

onMounted(loadCommission)

function onDetail(row: any) {
  current.value = row
  detailVisible.value = true
}

function reportRate(row: any): number {
  if (row.status === 'APPROVED') return Number(row.commission_rate) || 0
  return Number(row.suggested_commission_rate) || 0
}

function reportCommissionDesc(row: any): string {
  const label = row.commission_source_label || '全局默认'
  return `${reportRate(row)}% · ${label}`
}

function reportEstimatedPayout(row: any): number {
  return Math.floor((Number(row.amount) || 0) * (100 - reportRate(row)) / 100)
}

function allEvidenceUrls(row: any): string[] {
  return [row.entry_image_url, row.completion_image_url, ...(row.result_image_urls || [])].filter(Boolean)
}

async function onApprove(row: any) {
  const isOrderReport = Boolean(row.order)
  try {
    await ElMessageBox.confirm(
      isOrderReport
        ? `确认「${row.provider_name}」提交的订单凭证完整有效？该订单已经结算，本次审核不会重复入账。`
        : `通过后将按 ${reportCommissionDesc(row)} 给「${row.provider_name}」入账，预计 ${amountToXaCoin(reportEstimatedPayout(row))} 兴安币，确认通过？`,
      '报单审核',
      { type: 'warning', confirmButtonText: '确认通过' },
    )
  } catch {
    return
  }
  await reportApi.action(row.id, 'approve')
  ElMessage.success(isOrderReport ? '凭证审核已通过' : '已通过并入账')
  tableRef.value.load()
}

async function onReject(row: any) {
  let audit_remark = ''
  try {
    const { value } = await ElMessageBox.prompt('请填写驳回原因', '驳回报单', {
      inputPlaceholder: '驳回原因（必填）',
      inputValidator: (v) => (v && v.trim() ? true : '请填写驳回原因'),
    })
    audit_remark = value
  } catch {
    return
  }
  await reportApi.action(row.id, 'reject', { audit_remark })
  ElMessage.success('已驳回')
  tableRef.value.load()
}

function openCommission() {
  commissionInput.value = commissionRate.value
  commissionVisible.value = true
}

async function saveCommission() {
  savingRate.value = true
  try {
    const res = await commissionApi.update(commissionInput.value)
    commissionRate.value = res.data.rate
    ElMessage.success('抽成率已更新')
    commissionVisible.value = false
  } finally {
    savingRate.value = false
  }
}
</script>

<style scoped lang="scss">
.rp-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 让 CrudTable 内置的外层 padding 归零，融入 rp-page 的间距节奏 */
.rp-page :deep(.page-container) {
  padding: 0;
}

.evidence-section { margin-bottom: 20px; }
.evidence-title { margin-bottom: 10px; color: var(--el-text-color-primary); font-size: 14px; font-weight: 600; }
.evidence-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.evidence-image { width: 100%; height: 120px; border-radius: 10px; }

.tip {
  font-size: 12px;
  color: var(--muted-foreground);
}
</style>
