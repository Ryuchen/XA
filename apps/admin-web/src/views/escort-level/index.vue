<template>
  <div class="el-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">管理陪玩等级及其对应的平台抽成率（优先级高于店铺设置）</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('escort_level:edit')" type="primary" @click="onCreate">新增等级</el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="escortLevelApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-select v-model="query.is_active" placeholder="状态" clearable style="width: 120px" @change="reload">
          <el-option label="启用" value="true" />
          <el-option label="停用" value="false" />
        </el-select>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
    <el-table-column prop="name" label="等级名称" min-width="140" />
    <el-table-column label="平台抽成率" width="120">
      <template #default="{ row }">{{ row.commission_rate }}%</template>
    </el-table-column>
    <el-table-column prop="remark" label="备注" min-width="180" show-overflow-tooltip />
    <el-table-column prop="sort_order" label="排序" width="80" />
    <el-table-column label="状态" width="90">
      <template #default="{ row }">
        <el-tag :type="row.is_active ? 'success' : 'info'" effect="light" round>{{ row.is_active ? '启用' : '停用' }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="操作" width="140" fixed="right">
      <template #default="{ row }">
        <el-button v-if="auth.hasPerm('escort_level:edit')" link type="primary" @click="onEdit(row)">编辑</el-button>
        <el-button v-if="auth.hasPerm('escort_level:delete')" link type="danger" @click="onDelete(row)">删除</el-button>
      </template>
    </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" :title="form.id ? '编辑陪玩等级' : '新增陪玩等级'" width="460px">
    <el-form :model="form" label-width="110px">
      <el-form-item label="等级名称"><el-input v-model="form.name" placeholder="如：金牌打手" /></el-form-item>
      <el-form-item label="抽成率(%)">
        <el-input-number v-model="form.commission_rate" :min="0" :max="100" />
        <div class="tip">该等级陪玩接单时的平台抽成率，优先级高于店铺设置</div>
      </el-form-item>
      <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" :rows="2" /></el-form-item>
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
import { escortLevelApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({ is_active: '' })

const dialogVisible = ref(false)
const saving = ref(false)
const form = reactive<any>({})

function blank() {
  return { id: 0, name: '', commission_rate: 20, remark: '', sort_order: 0, is_active: true }
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
  if (!form.name) { ElMessage.warning('请填写等级名称'); return }
  saving.value = true
  try {
    const payload = {
      name: form.name,
      commission_rate: form.commission_rate,
      remark: form.remark,
      sort_order: form.sort_order,
      is_active: form.is_active,
    }
    if (form.id) {
      await escortLevelApi.update(form.id, payload)
    } else {
      await escortLevelApi.create(payload)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value.reload()
  } finally {
    saving.value = false
  }
}

async function onDelete(row: any) {
  await ElMessageBox.confirm(`确定删除等级「${row.name}」吗？`, '提示', { type: 'warning' })
  await escortLevelApi.remove(row.id)
  ElMessage.success('已删除')
  tableRef.value.reload()
}
</script>

<style scoped lang="scss">
.el-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.el-page :deep(.page-container) {
  padding: 0;
}
.tip {
  font-size: 12px;
  color: var(--muted-foreground);
  margin-left: 12px;
}
</style>
