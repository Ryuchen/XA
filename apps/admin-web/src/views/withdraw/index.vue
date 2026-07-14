<template>
  <div class="wd-page">
    <!-- 头部操作行 -->
    <div class="ap-page-head">
      <p class="ap-page-intro">平台资金流水、结算与提现审核</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('withdraw:audit')" type="warning" plain @click="openConfig">
          最低提现：{{ amountToXaCoin(minAmount) }} 兴安币
        </el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="withdrawApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-autocomplete
          v-model="query.provider_username"
          :fetch-suggestions="querySuggest('provider_username')"
          placeholder="陪玩账号"
          clearable
          value-key="value"
          style="width: 140px"
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
          style="width: 140px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.payee_name"
          :fetch-suggestions="querySuggest('payee_name')"
          placeholder="收款人"
          clearable
          value-key="value"
          style="width: 140px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.payee_account"
          :fetch-suggestions="querySuggest('payee_account')"
          placeholder="收款账号"
          clearable
          value-key="value"
          style="width: 160px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.status" placeholder="审核状态" clearable style="width: 130px" @change="reload">
          <el-option v-for="(v, k) in WITHDRAW_STATUS" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="user_name" label="陪玩" min-width="110" />
      <el-table-column label="提现金额" width="110">
        <template #default="{ row }">{{ amountToXaCoin(row.amount) }} 兴安币</template>
      </el-table-column>
      <el-table-column label="收款方式" width="100">
        <template #default="{ row }">
          <el-tag :type="PAYEE_METHOD[row.payee_method]?.type" effect="light" round>{{ row.payee_method_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="payee_name" label="收款人" width="100" />
      <el-table-column prop="payee_account" label="收款账号" min-width="160" show-overflow-tooltip />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="WITHDRAW_STATUS[row.status]?.type" effect="light" round>{{ row.status_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="提交时间" width="160">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="onDetail(row)">详情</el-button>
          <template v-if="auth.hasPerm('withdraw:audit') && row.status === 'PENDING'">
            <el-button link type="success" @click="onApprove(row)">通过</el-button>
            <el-button link type="danger" @click="onReject(row)">驳回</el-button>
          </template>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-drawer v-model="detailVisible" title="提现详情" size="480px">
    <template v-if="current">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="陪玩">{{ current.user_name }}</el-descriptions-item>
        <el-descriptions-item label="提现金额">{{ amountToXaCoin(current.amount) }} 兴安币</el-descriptions-item>
        <el-descriptions-item label="收款方式">{{ current.payee_method_display }}</el-descriptions-item>
        <el-descriptions-item label="收款人">{{ current.payee_name }}</el-descriptions-item>
        <el-descriptions-item label="收款账号">{{ current.payee_account }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ current.status_display }}</el-descriptions-item>
        <el-descriptions-item label="备注">{{ current.remark || '-' }}</el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'REJECTED'" label="驳回原因">
          {{ current.audit_remark || '-' }}
        </el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'APPROVED'" label="打款流水号">
          {{ current.payout_reference || '-' }}
        </el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'APPROVED'" label="实际打款时间">
          {{ current.paid_at ? formatDateTime(current.paid_at) : '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="审核人">{{ current.auditor_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="提交时间">{{ formatDateTime(current.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="审核时间">{{ current.audited_at ? formatDateTime(current.audited_at) : '-' }}</el-descriptions-item>
      </el-descriptions>
    </template>
  </el-drawer>

  <el-dialog v-model="configVisible" title="最低提现金额配置" width="380px">
    <el-form label-width="120px">
      <el-form-item label="最低提现(兴安币)">
        <el-input-number v-model="configInput" :min="0" :step="10" :precision="1" style="width: 100%" />
        <div class="tip">陪玩单次提现金额不得低于该值</div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="configVisible = false">取消</el-button>
      <el-button type="primary" :loading="savingConfig" @click="saveConfig">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import { withdrawApi, withdrawConfigApi } from '@/api/modules'
import { WITHDRAW_STATUS, PAYEE_METHOD } from '@/utils/dict'
import { amountToXaCoin, xaCoinToAmount, formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({
  provider_username: '',
  provider_nickname: '',
  payee_name: '',
  payee_account: '',
  status: '',
})

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询提现单后，
 * 取返回记录对应字段值去重后作为下拉提示。
 */
function querySuggest(field: 'provider_username' | 'provider_nickname' | 'payee_name' | 'payee_account') {
  return async (keyword: string, cb: (items: any[]) => void) => {
    if (!keyword) {
      cb([])
      return
    }
    try {
      const res = (await withdrawApi.list({ [field]: keyword, page: 1, page_size: 10 })) as any
      const seen = new Set<string>()
      const items: { value: string }[] = []
      for (const w of res.data?.list || []) {
        const value = w[field] || ''
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

const minAmount = ref<number>(0)
const configVisible = ref(false)
const configInput = ref<number>(0)
const savingConfig = ref(false)

async function loadConfig() {
  const res = await withdrawConfigApi.get()
  minAmount.value = res.data.min_amount
}

onMounted(loadConfig)

function onDetail(row: any) {
  current.value = row
  detailVisible.value = true
}

async function onApprove(row: any) {
  let payout_reference = ''
  try {
    const { value } = await ElMessageBox.prompt(
      `请先完成向「${row.user_name}」结算 ${amountToXaCoin(row.amount)} 兴安币，再填写渠道流水号或转账凭证编号。`,
      '提现审核',
      {
        type: 'warning', confirmButtonText: '确认已打款',
        inputPlaceholder: '打款流水号/凭证编号（必填）',
        inputValidator: (v) => (v && v.trim() ? true : '请填写打款流水号/凭证编号'),
      },
    )
    payout_reference = value.trim()
  } catch {
    return
  }
  await withdrawApi.action(row.id, 'approve', { payout_reference })
  ElMessage.success('已通过')
  tableRef.value.load()
}

async function onReject(row: any) {
  let audit_remark = ''
  try {
    const { value } = await ElMessageBox.prompt('请填写驳回原因（驳回后冻结金额将退还陪玩余额）', '驳回提现', {
      inputPlaceholder: '驳回原因（必填）',
      inputValidator: (v) => (v && v.trim() ? true : '请填写驳回原因'),
    })
    audit_remark = value
  } catch {
    return
  }
  await withdrawApi.action(row.id, 'reject', { audit_remark })
  ElMessage.success('已驳回并退还')
  tableRef.value.load()
}

function openConfig() {
  configInput.value = Number(amountToXaCoin(minAmount.value))
  configVisible.value = true
}

async function saveConfig() {
  savingConfig.value = true
  try {
    const res = await withdrawConfigApi.update(xaCoinToAmount(configInput.value))
    minAmount.value = res.data.min_amount
    ElMessage.success('最低提现金额已更新')
    configVisible.value = false
  } finally {
    savingConfig.value = false
  }
}
</script>

<style scoped lang="scss">
.wd-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 让 CrudTable 内置的外层 padding 归零，融入 wd-page 的间距节奏 */
.wd-page :deep(.page-container) {
  padding: 0;
}

.tip {
  font-size: 12px;
  color: var(--muted-foreground);
}
</style>
