<template>
  <div class="ac-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">配置用户成就体系的解锁指标与目标值，激励下单、消费等关键行为</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('achievement:edit')" type="primary" @click="onCreate">新增成就</el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="achievementApi.list" :query="query">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="code" label="标识" min-width="120" />
      <el-table-column prop="title" label="名称" min-width="140" />
      <el-table-column prop="desc" label="描述" min-width="160" />
      <el-table-column label="指标" width="130">
        <template #default="{ row }">
          <el-tag :type="ACHIEVEMENT_METRIC[row.metric]?.type" effect="light" round>{{ row.metric_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="target" label="目标值" width="100" />
      <el-table-column prop="sort_order" label="排序" width="80" />
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" effect="light" round>{{ row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button v-if="auth.hasPerm('achievement:edit')" link type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button v-if="auth.hasPerm('achievement:delete')" link type="danger" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" :title="form.id ? '编辑成就' : '新增成就'" width="480px">
    <el-form :model="form" label-width="100px">
      <el-form-item label="标识"><el-input v-model="form.code" :disabled="!!form.id" placeholder="如 first_order" /></el-form-item>
      <el-form-item label="名称"><el-input v-model="form.title" /></el-form-item>
      <el-form-item label="描述"><el-input v-model="form.desc" /></el-form-item>
      <el-form-item label="图标URL"><el-input v-model="form.icon" /></el-form-item>
      <el-form-item label="指标">
        <el-select v-model="form.metric" style="width: 100%">
          <el-option v-for="(v, k) in ACHIEVEMENT_METRIC" :key="k" :label="v.label" :value="k" />
        </el-select>
      </el-form-item>
      <el-form-item label="目标值"><el-input-number v-model="form.target" :min="0" /></el-form-item>
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
import { achievementApi } from '@/api/modules'
import { ACHIEVEMENT_METRIC } from '@/utils/dict'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({})

const dialogVisible = ref(false)
const saving = ref(false)
const form = reactive<any>({})

function blank() {
  return { id: 0, code: '', title: '', desc: '', icon: '', metric: 'orders', target: 1, sort_order: 0, is_active: true }
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
  if (!form.code) { ElMessage.warning('请填写标识'); return }
  if (!form.title) { ElMessage.warning('请填写名称'); return }
  saving.value = true
  try {
    const payload = {
      code: form.code,
      title: form.title,
      desc: form.desc,
      icon: form.icon,
      metric: form.metric,
      target: form.target,
      sort_order: form.sort_order,
      is_active: form.is_active,
    }
    if (form.id) {
      await achievementApi.update(form.id, payload)
    } else {
      await achievementApi.create(payload)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value.reload()
  } finally {
    saving.value = false
  }
}

async function onDelete(row: any) {
  await ElMessageBox.confirm(`确定删除成就「${row.title}」吗？`, '提示', { type: 'warning' })
  await achievementApi.remove(row.id)
  ElMessage.success('已删除')
  tableRef.value.reload()
}
</script>

<style scoped lang="scss">
.ac-page {
  display: flex;
  flex-direction: column;
  gap: 20px;

  :deep(.page-container) {
    padding: 0;
  }
}
</style>
