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
        <template #default="{ row }">{{ amountToXaCoin(row.amount) }} 币</template>
      </el-table-column>
      <el-table-column label="发放/已领" width="110">
        <template #default="{ row }">{{ row.total_qty || '不限' }} / {{ row.claimed_qty }}</template>
      </el-table-column>
      <el-table-column label="有效期至" width="160">
        <template #default="{ row }">{{ formatDateTime(row.valid_to) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" effect="light" round>{{ row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button v-if="auth.hasPerm('coupon:edit')" link type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button v-if="auth.hasPerm('coupon:delete')" link type="danger" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" :title="form.id ? '编辑优惠券' : '新增优惠券'" width="480px">
    <el-form :model="form" label-width="100px">
      <el-form-item label="券名称"><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="类型">
        <el-select v-model="form.discount_type" style="width: 100%">
          <el-option v-for="(v, k) in COUPON_TYPE" :key="k" :label="v.label" :value="k" />
        </el-select>
      </el-form-item>
      <el-form-item label="门槛(兴安币)"><el-input-number v-model="thresholdCoin" :min="0" :precision="1" /></el-form-item>
      <el-form-item label="面额(兴安币)"><el-input-number v-model="amountCoin" :min="0" :precision="1" /></el-form-item>
      <el-form-item label="发放总量"><el-input-number v-model="form.total_qty" :min="0" /><span class="tip">0 为不限</span></el-form-item>
      <el-form-item label="有效期至">
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
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import { couponApi } from '@/api/modules'
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

function blank() {
  return {
    id: 0, name: '', discount_type: 'THRESHOLD', total_qty: 0,
    valid_to: '', sort_order: 0, is_active: true,
  }
}

function onCreate() {
  Object.assign(form, blank())
  thresholdCoin.value = 0
  amountCoin.value = 0
  dialogVisible.value = true
}

function onEdit(row: any) {
  Object.assign(form, row)
  thresholdCoin.value = Number(amountToXaCoin(row.threshold))
  amountCoin.value = Number(amountToXaCoin(row.amount))
  dialogVisible.value = true
}

async function onSave() {
  if (!form.name) { ElMessage.warning('请填写券名称'); return }
  if (!form.valid_to) { ElMessage.warning('请选择有效期'); return }
  saving.value = true
  try {
    const payload = {
      name: form.name,
      discount_type: form.discount_type,
      threshold: xaCoinToAmount(thresholdCoin.value),
      amount: xaCoinToAmount(amountCoin.value),
      total_qty: form.total_qty,
      valid_to: form.valid_to,
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
