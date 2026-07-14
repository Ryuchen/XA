<template>
  <div class="bt-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">管理老板类型及其下单折扣，折扣将直接影响订单金额</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('boss_type:edit')" type="primary" @click="onCreate">新增分级</el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="bossTypeApi.list" :query="query">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="name" label="分级名称" min-width="140" />
      <el-table-column label="折扣率" width="120">
        <template #default="{ row }">{{ row.discount_rate }}%</template>
      </el-table-column>
      <el-table-column prop="remark" label="备注" min-width="180" show-overflow-tooltip />
      <el-table-column prop="sort_order" label="排序" width="80" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" effect="light" round>
            {{ row.is_active ? '启用' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button v-if="auth.hasPerm('boss_type:edit')" link type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button v-if="auth.hasPerm('boss_type:delete')" link type="danger" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" :title="form.id ? '编辑老板分级' : '新增老板分级'" width="560px" class="bt-dialog">
    <el-form :model="form" label-width="82px" label-position="right">
      <div class="bt-form-section">
        <p class="bt-section-title">基础信息</p>
        <el-form-item label="分级名称"><el-input v-model="form.name" placeholder="如：黄金老板" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" :rows="2" placeholder="选填" /></el-form-item>
      </div>

      <div class="bt-form-section">
        <p class="bt-section-title">计费与状态</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="折扣率(%)">
              <el-input-number v-model="form.discount_rate" :min="1" :max="100" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="排序">
              <el-input-number v-model="form.sort_order" :min="0" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="折扣说明">
          <span class="tip">100=原价，80=八折；下单时按此折扣计费</span>
        </el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.is_active" /></el-form-item>
      </div>
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
import { bossTypeApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({})

const dialogVisible = ref(false)
const saving = ref(false)
const form = reactive<any>({})

function blank() {
  return { id: 0, name: '', discount_rate: 100, remark: '', sort_order: 0, is_active: true }
}

function onCreate() {
  Object.assign(form, blank())
  dialogVisible.value = true
}

function onEdit(row: any) {
  Object.assign(form, row)
  dialogVisible.value = true
}

async function onSave() {
  if (!form.name) { ElMessage.warning('请填写分级名称'); return }
  saving.value = true
  try {
    const payload = {
      name: form.name,
      discount_rate: form.discount_rate,
      remark: form.remark,
      sort_order: form.sort_order,
      is_active: form.is_active,
    }
    if (form.id) {
      await bossTypeApi.update(form.id, payload)
    } else {
      await bossTypeApi.create(payload)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value.reload()
  } finally {
    saving.value = false
  }
}

async function onDelete(row: any) {
  await ElMessageBox.confirm(`确定删除分级「${row.name}」吗？`, '提示', { type: 'warning' })
  await bossTypeApi.remove(row.id)
  ElMessage.success('已删除')
  tableRef.value.reload()
}
</script>

<style scoped lang="scss">
.bt-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.bt-page :deep(.page-container) {
  padding: 0;
}
.tip {
  font-size: 12px;
  color: var(--muted-foreground);
  margin-left: 12px;
}
.bt-dialog :deep(.el-dialog__body) {
  padding-top: 8px;
}
.bt-form-section {
  padding: 4px 0 2px;
}
.bt-form-section + .bt-form-section {
  margin-top: 8px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}
.bt-section-title {
  margin: 0 0 14px;
  padding-left: 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--foreground);
  line-height: 1.2;
  border-left: 3px solid var(--primary);
}
.bt-form-section :deep(.el-form-item) {
  margin-bottom: 16px;
}

</style>
