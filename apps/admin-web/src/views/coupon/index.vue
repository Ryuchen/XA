<template>
  <div class="cp-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">管理优惠券模板的类型、门槛与面额，控制发放数量与有效期</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('coupon:edit')" type="primary" @click="onCreate">新增优惠券</el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="couponApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-select v-model="query.is_active" placeholder="状态" clearable style="width: 120px" @change="reload">
          <el-option label="启用" value="true" />
          <el-option label="停用" value="false" />
        </el-select>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="name" label="券名称" min-width="140" />
      <el-table-column label="类型" width="100">
        <template #default="{ row }">
          <el-tag :type="COUPON_TYPE[row.discount_type]?.type" effect="light" round>{{ row.discount_type_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="门槛" width="100">
        <template #default="{ row }">{{ amountToXaCoin(row.threshold) }} 币</template>
      </el-table-column>
      <el-table-column label="面额" width="100">
        <template #default="{ row }">{{ row.discount_type === 'QUALIFICATION' ? '套餐资格' : row.discount_type === 'RATE' ? `${row.discount_rate}%` : `${amountToXaCoin(row.amount)} 币` }}</template>
      </el-table-column>
      <el-table-column label="发放/已领" width="110">
        <template #default="{ row }">{{ row.total_qty || '不限' }} / {{ row.claimed_qty }}</template>
      </el-table-column>
      <el-table-column label="有效期至" width="160">
        <template #default="{ row }">{{ row.valid_to ? formatDateTime(row.valid_to) : '永久有效 / 每日券按领取日' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" effect="light" round>{{ row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="onClaims(row)">领取记录</el-button>
          <el-button v-if="auth.hasPerm('coupon:edit')" link type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button v-if="auth.hasPerm('coupon:delete')" link type="danger" :disabled="row.claimed_qty > 0" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" :title="form.id ? '编辑优惠券' : '新增优惠券'" width="680px" class="ap-modal">
    <el-form :model="form"  class="ap-modal-form ap-form-grid" label-position="top">
      <el-form-item label="券名称"><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="类型">
        <el-select v-model="form.discount_type" style="width: 100%">
          <el-option v-for="(v, k) in COUPON_TYPE" :key="k" :label="v.label" :value="k" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="form.discount_type !== 'QUALIFICATION'" label="门槛(兴安币)"><el-input-number v-model="thresholdCoin" :min="0" :precision="1" /></el-form-item>
      <el-form-item v-if="!['QUALIFICATION', 'RATE'].includes(form.discount_type)" label="面额(兴安币)"><el-input-number v-model="amountCoin" :min="0.1" :precision="1" /></el-form-item>
      <el-form-item v-if="form.discount_type === 'RATE'" label="折扣率(%)"><el-input-number v-model="form.discount_rate" :min="0" :max="100" /><span class="tip">0 为赠送服务，100 为原价</span></el-form-item>
      <el-form-item v-if="form.discount_type === 'QUALIFICATION' || form.service_ids !== null" label="适用套餐">
        <el-select v-model="form.service_ids" multiple filterable :loading="loadingServices" placeholder="请选择套餐" style="width: 100%">
          <el-option v-for="item in serviceOptions" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="form.discount_type === 'QUALIFICATION'" label="使用规则"><span>每张券仅能购买一份指定套餐，按套餐价格支付；核销后取消退款不返还资格。</span></el-form-item>
      <el-form-item v-if="form.discount_type === 'RATE'" label="每张优惠上限（元）"><el-input-number :model-value="(form.max_discount || 0)/100" :min="0" :precision="2" @update:model-value="value=>form.max_discount=Math.round((value||0)*100)"/><span class="tip">0 表示不另设上限</span></el-form-item>
      <el-form-item v-if="form.discount_type === 'QUALIFICATION'" label="体验资格"><el-select v-model="form.experience_kind"><el-option value="" label="其他指定套餐资格"/><el-option value="NEW_VALUE" label="新客超值：每周期一次、永久有效"/><el-option value="DAILY_VALUE" label="每日超值：每个平台每日一次、当天有效"/></el-select></el-form-item>
      <el-form-item v-if="form.experience_kind" label="适用平台"><el-select v-model="form.platform"><el-option value="" label="端游/手游共享新客次数"/><el-option value="PC" label="端游"/><el-option value="MOBILE" label="手游"/></el-select></el-form-item>
      <el-form-item label="发放总量"><el-input-number v-model="form.total_qty" :min="0" /><span class="tip">0 为不限</span></el-form-item>
      <el-form-item label="有效期至（留空永久有效）">
        <el-date-picker v-model="form.valid_to" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
      </el-form-item>
      <el-form-item label="排序"><el-input-number v-model="form.sort_order" :min="0" /></el-form-item>
      <el-form-item label="启用"><el-switch v-model="form.is_active" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
    </template>
  </el-dialog>

  <el-drawer v-model="claimsVisible" :title="`领取记录 · ${claimsCoupon?.name || ''}`" size="760px" destroy-on-close class="ap-panel">
    <p class="ap-page-intro">已领取的优惠券保留领取与核销记录；停止发放请在编辑中关闭启用。</p>
    <crud-table v-if="claimsVisible && claimsCoupon" :key="claimsCoupon.id" :fetcher="userCouponApi.list" :query="claimsQuery">
      <template #toolbar="{ reload }">
        <el-select v-model="claimsQuery.status" placeholder="领取状态" clearable style="width: 140px" @change="reload">
          <el-option label="未使用" value="UNUSED" />
          <el-option label="已使用" value="USED" />
          <el-option label="已过期" value="EXPIRED" />
        </el-select>
      </template>
      <el-table-column prop="user_name" label="领取人" min-width="120" />
      <el-table-column prop="status_display" label="状态" width="90" />
      <el-table-column label="领取时间" width="165"><template #default="{ row }">{{ formatDateTime(row.claimed_at) }}</template></el-table-column>
      <el-table-column label="使用时间" width="165"><template #default="{ row }">{{ formatDateTime(row.used_at) }}</template></el-table-column>
      <el-table-column prop="order_no" label="核销订单" min-width="180" />
    </crud-table>
  </el-drawer>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import { couponApi, userCouponApi } from '@/api/modules'
import { COUPON_TYPE } from '@/utils/dict'
import { amountToXaCoin, xaCoinToAmount, formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({ is_active: '' })

const dialogVisible = ref(false)
const saving = ref(false)
const thresholdCoin = ref(0)
const amountCoin = ref(0)
const form = reactive<any>({})
const serviceOptions = ref<{ id: number; name: string }[]>([])
const loadingServices = ref(false)
watch([dialogVisible, () => form.discount_type], async ([visible, kind]) => {
  if (!visible) return
  if (kind === 'QUALIFICATION' && form.service_ids === null) form.service_ids = []
  if (kind !== 'QUALIFICATION' && form.service_ids === null) return
  loadingServices.value = true
  try { serviceOptions.value = (await couponApi.serviceOptions()).data }
  catch { ElMessage.error('套餐加载失败，请稍后重试') }
  finally { loadingServices.value = false }
})
const claimsVisible = ref(false)
const claimsCoupon = ref<any>(null)
const claimsQuery = reactive({ coupon: 0, status: '' })

function onClaims(row: any) {
  claimsCoupon.value = row
  claimsQuery.coupon = row.id
  claimsQuery.status = ''
  claimsVisible.value = true
}

function blank() {
  return {
    id: 0, name: '', discount_type: 'THRESHOLD', total_qty: 0,
    valid_to: null, max_discount: 0, experience_kind: '', platform: '', sort_order: 0, is_active: true, discount_rate: 100, service_ids: null,
    scope: 'ALL', scope_variant: '', limit_units: 0, is_public: true,
  }
}

function onCreate() {
  Object.assign(form, blank())
  thresholdCoin.value = 0
  amountCoin.value = 0
  dialogVisible.value = true
}

function onEdit(row: any) {
  Object.assign(form, blank(), row, { service_ids: row.service_ids === null ? null : [...(row.service_ids || [])] })
  thresholdCoin.value = Number(amountToXaCoin(row.threshold))
  amountCoin.value = Number(amountToXaCoin(row.amount))
  dialogVisible.value = true
}

async function onSave() {
  if (!form.name) { ElMessage.warning('请填写券名称'); return }
  const qualification = form.discount_type === 'QUALIFICATION'
  if (qualification && !form.service_ids?.length) { ElMessage.warning('请选择资格券适用套餐'); return }
  if (!qualification && form.discount_type !== 'RATE' && !(amountCoin.value > 0)) { ElMessage.warning('面额必须大于零'); return }
  if (form.total_qty > 0 && form.total_qty < (form.claimed_qty || 0)) { ElMessage.warning('发放总量不能少于已领取数量'); return }
  saving.value = true
  try {
    const payload = {
      name: form.name,
      discount_type: form.discount_type,
      threshold: qualification ? 0 : xaCoinToAmount(thresholdCoin.value),
      amount: qualification ? 0 : form.discount_type === 'RATE' ? Math.max(1, xaCoinToAmount(amountCoin.value)) : xaCoinToAmount(amountCoin.value),
      discount_rate: qualification ? 100 : form.discount_rate,
      service_ids: form.service_ids,
      limit_units: qualification ? 1 : form.limit_units,
      scope: form.scope, scope_variant: form.scope_variant, is_public: form.is_public,
      total_qty: form.total_qty,
      valid_to: form.valid_to || null, max_discount: form.max_discount || 0, experience_kind: qualification ? form.experience_kind : '', platform: qualification ? form.platform : '',
      sort_order: form.sort_order,
      is_active: form.is_active,
    }
    if (form.id) {
      await couponApi.update(form.id, payload)
    } else {
      await couponApi.create(payload)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value.reload()
  } finally {
    saving.value = false
  }
}

async function onDelete(row: any) {
  await ElMessageBox.confirm(`确定删除优惠券「${row.name}」吗？`, '提示', { type: 'warning' })
  await couponApi.remove(row.id)
  ElMessage.success('已删除')
  tableRef.value.reload()
}
</script>

<style scoped lang="scss">
.cp-page {
  display: flex;
  flex-direction: column;
  gap: 20px;

  :deep(.page-container) {
    padding: 0;
  }
}

.tip {
  margin-left: 8px;
  font-size: 12px;
  color: var(--muted-foreground);
}
</style>
