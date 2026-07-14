<template>
  <div class="wl-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">核对企业微信收款后兑换兴安币，并管理钱包余额与调账</p>
    </div>

    <crud-table ref="tableRef" :fetcher="walletApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-autocomplete
          v-model="query.boss_no"
          :fetch-suggestions="querySuggest('boss_no')"
          placeholder="编号"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.nickname"
          :fetch-suggestions="querySuggest('nickname')"
          placeholder="昵称"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.phone"
          :fetch-suggestions="querySuggest('phone')"
          placeholder="手机号"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
      </template>

      <el-table-column label="编号" width="110">
        <template #default="{ row }">
          <span class="wl-no">{{ row.boss_no || '-' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="头像" width="70">
        <template #default="{ row }">
          <el-avatar :size="32" :src="row.avatar_url || undefined">{{ (row.nickname || row.username).charAt(0) }}</el-avatar>
        </template>
      </el-table-column>
      <el-table-column prop="username" label="账号" min-width="130" />
      <el-table-column prop="nickname" label="昵称" min-width="120" />
      <el-table-column prop="phone" label="手机号" width="130" />
      <el-table-column label="老板分级" width="110">
        <template #default="{ row }">{{ row.boss_type_name || '-' }}</template>
      </el-table-column>
      <el-table-column label="兴安币余额" width="120">
        <template #default="{ row }">{{ amountToXaCoin(row.balance) }} 币</template>
      </el-table-column>
      <el-table-column label="冻结" width="100">
        <template #default="{ row }">{{ amountToXaCoin(row.frozen_amount) }} 币</template>
      </el-table-column>
      <el-table-column label="累计实充" width="110">
        <template #default="{ row }">{{ amountToXaCoin(row.total_recharge) }} 币</template>
      </el-table-column>
      <el-table-column label="累计赠送" width="110">
        <template #default="{ row }">{{ amountToXaCoin(row.total_gift) }} 币</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'danger'" effect="light" round>
            {{ row.is_active ? '正常' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        v-if="auth.hasPerm('wallet:adjust') || auth.hasPerm('wallet:recharge')"
        label="操作"
        width="160"
        fixed="right"
      >
        <template #default="{ row }">
          <el-button v-if="auth.hasPerm('wallet:recharge')" link type="success" @click="onRecharge(row)">确认企微充值</el-button>
          <el-button v-if="auth.hasPerm('wallet:adjust')" link type="primary" @click="onAdjust(row)">调账</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="adjustVisible" title="钱包调账" width="420px">
    <el-form label-width="100px">
      <el-form-item label="当前余额">{{ amountToXaCoin(current?.balance) }} 兴安币</el-form-item>
      <el-form-item label="调整兴安币">
        <el-input-number v-model="adjustCoin" :precision="1" :step="10" style="width: 100%" />
        <div class="tip">正数增加，负数扣减</div>
      </el-form-item>
      <el-form-item label="备注">
        <el-input v-model="adjustRemark" placeholder="调账原因" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="adjustVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="onAdjustSave">确定</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="rechargeVisible" title="企微充值确认" width="560px" class="wl-dialog">
    <el-form label-width="92px" label-position="right">
      <el-alert
        title="请先确认企业微信已收款，再按 1:10 兑换比例录入到账兴安币"
        type="info"
        :closable="false"
        show-icon
        class="wl-recharge-alert"
      />
      <div class="wl-form-section">
        <p class="wl-section-title">充值对象</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="老板">{{ current?.nickname || current?.username }}</el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="当前余额">{{ amountToXaCoin(current?.balance) }} 币</el-form-item>
          </el-col>
        </el-row>
      </div>

      <div class="wl-form-section">
        <p class="wl-section-title">充值金额</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="充值兴安币">
              <el-input-number v-model="rechargeCoin" :min="0" :precision="1" :step="100" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="赠送兴安币">
              <el-input-number v-model="giftCoin" :min="0" :precision="1" :step="10" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="预计到账">
          <strong class="wl-credit-preview">{{ rechargeCoin + giftCoin }} 兴安币</strong>
          <span class="tip">（充值兑换 + 客服赠送）</span>
        </el-form-item>
      </div>

      <div class="wl-form-section">
        <p class="wl-section-title">溯源信息</p>
        <el-form-item label="交易编号">
          <el-input v-model="tradeNo" placeholder="企业微信收款流水号，建议必填" maxlength="64" />
        </el-form-item>
        <el-form-item label="充值凭证">
          <el-upload
            :auto-upload="false"
            :show-file-list="false"
            accept="image/*"
            :on-change="onProofChange"
          >
            <el-button>选择凭证图片</el-button>
          </el-upload>
          <el-image
            v-if="proofPreview"
            :src="proofPreview"
            :preview-src-list="[proofPreview]"
            fit="contain"
            class="wl-proof-preview"
          />
          <div class="tip">转账/收款截图，选填但建议上传以便对账溯源</div>
        </el-form-item>
      </div>

      <el-form-item label="备注">
        <el-input v-model="rechargeRemark" placeholder="充值备注" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="rechargeVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="onRechargeSave">确认收款并入账</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage, type UploadFile } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import { walletApi } from '@/api/modules'
import { amountToXaCoin, xaCoinToAmount } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = ref<Record<string, any>>({ boss_no: '', nickname: '', phone: '' })

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询后端，
 * 取返回记录的该字段值去重后作为下拉提示。
 */
function querySuggest(field: 'boss_no' | 'nickname' | 'phone') {
  return async (keyword: string, cb: (items: any[]) => void) => {
    if (!keyword) {
      cb([])
      return
    }
    try {
      const res = (await walletApi.list({ [field]: keyword, page: 1, page_size: 10 })) as any
      const seen = new Set<string>()
      const items: { value: string }[] = []
      for (const w of res.data.list || []) {
        const value = w[field]
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

const saving = ref(false)
const current = ref<any>(null)

const adjustVisible = ref(false)
const adjustCoin = ref<number>(0)
const adjustRemark = ref('')

function onAdjust(row: any) {
  current.value = row
  adjustCoin.value = 0
  adjustRemark.value = ''
  adjustVisible.value = true
}

async function onAdjustSave() {
  const amount = xaCoinToAmount(adjustCoin.value)
  if (amount === 0) {
    ElMessage.warning('调账金额不能为 0')
    return
  }
  saving.value = true
  try {
    await walletApi.action(current.value.id, 'adjust', { amount, remark: adjustRemark.value })
    ElMessage.success('调账成功')
    adjustVisible.value = false
    tableRef.value.load()
  } finally {
    saving.value = false
  }
}

const rechargeVisible = ref(false)
const rechargeCoin = ref<number>(0)
const giftCoin = ref<number>(0)
const rechargeRemark = ref('')
const tradeNo = ref('')
const proofFile = ref<File | null>(null)
const proofPreview = ref('')

function onProofChange(uploadFile: UploadFile) {
  const raw = uploadFile.raw
  if (!raw) return
  proofFile.value = raw
  proofPreview.value = URL.createObjectURL(raw)
}

function onRecharge(row: any) {
  current.value = row
  rechargeCoin.value = 0
  giftCoin.value = 0
  rechargeRemark.value = ''
  tradeNo.value = ''
  proofFile.value = null
  proofPreview.value = ''
  rechargeVisible.value = true
}

async function onRechargeSave() {
  const amount = xaCoinToAmount(rechargeCoin.value)
  const giftAmount = xaCoinToAmount(giftCoin.value)
  if (amount <= 0) {
    ElMessage.warning('实充金额必须大于 0')
    return
  }
  saving.value = true
  try {
    const fd = new FormData()
    fd.append('user_id', String(current.value.user))
    fd.append('amount', String(amount))
    fd.append('gift_amount', String(giftAmount))
    fd.append('remark', rechargeRemark.value)
    fd.append('trade_no', tradeNo.value)
    if (proofFile.value) fd.append('proof_image', proofFile.value)
    await walletApi.collectionAction('recharge', fd)
    ElMessage.success(`已入账 ${rechargeCoin.value + giftCoin.value} 兴安币`)
    rechargeVisible.value = false
    tableRef.value.load()
  } finally {
    saving.value = false
  }
}
</script>

<style scoped lang="scss">
.wl-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.wl-page :deep(.page-container) {
  padding: 0;
}
.tip {
  font-size: 12px;
  color: var(--muted-foreground);
}
.wl-no {
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.02em;
}
.wl-dialog :deep(.el-dialog__body) {
  padding-top: 8px;
}
.wl-recharge-alert {
  margin-bottom: 18px;
}
.wl-credit-preview {
  margin-right: 8px;
  color: var(--primary);
  font-size: 18px;
}
.wl-form-section {
  padding: 4px 0 2px;
}
.wl-form-section + .wl-form-section {
  margin-top: 8px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}
.wl-section-title {
  margin: 0 0 14px;
  padding-left: 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--foreground);
  line-height: 1.2;
  border-left: 3px solid var(--primary);
}
.wl-form-section :deep(.el-form-item) {
  margin-bottom: 16px;
}
.wl-proof-preview {
  display: block;
  width: 120px;
  height: 120px;
  margin-top: 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
</style>
