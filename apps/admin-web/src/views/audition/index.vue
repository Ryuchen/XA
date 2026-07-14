<template>
  <div class="au-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">管理对外投放的试音招募链接，分发老板端与陪玩端入口</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('audition:edit')" type="primary" @click="onCreate">新增试音链接</el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="auditionApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-input
          v-model="query.keyword"
          placeholder="标题/备注"
          clearable
          style="width: 200px"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.is_active" placeholder="状态" clearable style="width: 120px" @change="reload">
          <el-option label="启用" :value="1" />
          <el-option label="停用" :value="0" />
        </el-select>
        <el-button type="primary" @click="reload">查询</el-button>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="title" label="标题" min-width="140" />
      <el-table-column prop="remark" label="备注" min-width="140" show-overflow-tooltip />
      <el-table-column label="老板链接" width="120">
        <template #default="{ row }">
          <el-button link type="primary" @click="copy(row.boss_url)">复制</el-button>
        </template>
      </el-table-column>
      <el-table-column label="陪玩链接" width="120">
        <template #default="{ row }">
          <el-button link type="primary" @click="copy(row.provider_url)">复制</el-button>
        </template>
      </el-table-column>
      <el-table-column label="截止时间" width="160">
        <template #default="{ row }">{{ row.expire_at ? formatDateTime(row.expire_at) : '长期有效' }}</template>
      </el-table-column>
      <el-table-column prop="operator_name" label="创建客服" width="110" />
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" effect="light" round>{{ row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="160">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button v-if="auth.hasPerm('audition:edit')" link type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button v-if="auth.hasPerm('audition:delete')" link type="danger" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" :title="form.id ? '编辑试音链接' : '新增试音链接'" width="520px">
    <el-form :model="form" label-width="90px">
      <el-form-item label="标题"><el-input v-model="form.title" placeholder="如：王者荣耀招募" /></el-form-item>
      <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" :rows="2" /></el-form-item>
      <el-form-item label="截止时间">
        <el-date-picker
          v-model="form.expire_at"
          type="datetime"
          placeholder="留空为长期有效"
          value-format="YYYY-MM-DDTHH:mm:ss"
          style="width: 100%"
        />
      </el-form-item>
      <el-form-item label="启用"><el-switch v-model="form.is_active" /></el-form-item>
      <template v-if="form.id">
        <el-form-item label="老板链接">
          <el-input v-model="form.boss_url" readonly>
            <template #append><el-button @click="copy(form.boss_url)">复制</el-button></template>
          </el-input>
        </el-form-item>
        <el-form-item label="陪玩链接">
          <el-input v-model="form.provider_url" readonly>
            <template #append><el-button @click="copy(form.provider_url)">复制</el-button></template>
          </el-input>
        </el-form-item>
      </template>
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
import { auditionApi } from '@/api/modules'
import { formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({ keyword: '', is_active: '' })

const dialogVisible = ref(false)
const saving = ref(false)
const form = reactive<any>({})

function blank() {
  return { id: 0, title: '', remark: '', expire_at: null, is_active: true, boss_url: '', provider_url: '' }
}

function onCreate() {
  Object.assign(form, blank())
  dialogVisible.value = true
}

function onEdit(row: any) {
  Object.assign(form, blank(), row)
  dialogVisible.value = true
}

async function copy(text: string) {
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制链接')
  } catch {
    ElMessage.warning('复制失败，请手动复制')
  }
}

async function onSave() {
  if (!form.title) { ElMessage.warning('请填写标题'); return }
  saving.value = true
  try {
    const payload = {
      title: form.title,
      remark: form.remark,
      expire_at: form.expire_at || null,
      is_active: form.is_active,
    }
    if (form.id) {
      await auditionApi.update(form.id, payload)
    } else {
      await auditionApi.create(payload)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value.reload()
  } finally {
    saving.value = false
  }
}

async function onDelete(row: any) {
  await ElMessageBox.confirm(`确定删除试音链接「${row.title}」吗？`, '提示', { type: 'warning' })
  await auditionApi.remove(row.id)
  ElMessage.success('已删除')
  tableRef.value.reload()
}
</script>

<style scoped lang="scss">
.au-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.au-page :deep(.page-container) {
  padding: 0;
}
</style>
