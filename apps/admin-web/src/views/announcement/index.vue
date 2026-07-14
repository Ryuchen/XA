<template>
  <div class="an-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">发布与维护平台公告，支持置顶与排序，及时向用户传达重要通知</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('announcement:edit')" type="primary" @click="onCreate">新增公告</el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="announcementApi.list" :query="query">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="title" label="标题" min-width="180" />
      <el-table-column prop="content" label="内容" min-width="240" show-overflow-tooltip />
      <el-table-column label="置顶" width="80">
        <template #default="{ row }">
          <el-tag v-if="row.is_pinned" type="danger" effect="light" round>置顶</el-tag>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column prop="sort_order" label="排序" width="80" />
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" effect="light" round>{{ row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button v-if="auth.hasPerm('announcement:edit')" link type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button v-if="auth.hasPerm('announcement:delete')" link type="danger" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" :title="form.id ? '编辑公告' : '新增公告'" width="560px">
    <el-form :model="form" label-width="80px">
      <el-form-item label="标题"><el-input v-model="form.title" /></el-form-item>
      <el-form-item label="内容"><el-input v-model="form.content" type="textarea" :rows="6" /></el-form-item>
      <el-form-item label="置顶"><el-switch v-model="form.is_pinned" /></el-form-item>
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
import { announcementApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({})

const dialogVisible = ref(false)
const saving = ref(false)
const form = reactive<any>({})

function blank() {
  return { id: 0, title: '', content: '', is_pinned: false, sort_order: 0, is_active: true }
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
  if (!form.title) { ElMessage.warning('请填写标题'); return }
  saving.value = true
  try {
    const payload = {
      title: form.title,
      content: form.content,
      is_pinned: form.is_pinned,
      sort_order: form.sort_order,
      is_active: form.is_active,
    }
    if (form.id) {
      await announcementApi.update(form.id, payload)
    } else {
      await announcementApi.create(payload)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value.reload()
  } finally {
    saving.value = false
  }
}

async function onDelete(row: any) {
  await ElMessageBox.confirm(`确定删除公告「${row.title}」吗？`, '提示', { type: 'warning' })
  await announcementApi.remove(row.id)
  ElMessage.success('已删除')
  tableRef.value.reload()
}
</script>

<style scoped lang="scss">
.an-page {
  display: flex;
  flex-direction: column;
  gap: 20px;

  :deep(.page-container) {
    padding: 0;
  }
}
</style>
