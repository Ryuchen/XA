<template>
  <div class="rl-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">管理平台内的职位角色及其功能权限范围（PRD 7.15）</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('role:edit')" type="primary" @click="onCreate">新增角色</el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="roleApi.list" :query="query">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="name" label="角色名称" min-width="140" />
      <el-table-column prop="code" label="标识" min-width="140" />
      <el-table-column prop="description" label="描述" min-width="180" show-overflow-tooltip />
      <el-table-column label="权限数" width="90">
        <template #default="{ row }">{{ row.permissions?.length || 0 }}</template>
      </el-table-column>
      <el-table-column prop="member_count" label="成员数" width="90" />
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" effect="light" round>{{ row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <el-button v-if="auth.hasPerm('role:edit')" link type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button v-if="auth.hasPerm('role:delete')" link type="danger" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" :title="form.id ? '编辑角色' : '新增角色'" width="640px">
    <el-form :model="form" label-width="90px">
      <el-form-item label="角色名称"><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="标识"><el-input v-model="form.code" :disabled="!!form.id" placeholder="如 operator" /></el-form-item>
      <el-form-item label="描述"><el-input v-model="form.description" /></el-form-item>
      <el-form-item label="排序"><el-input-number v-model="form.sort_order" :min="0" /></el-form-item>
      <el-form-item label="启用"><el-switch v-model="form.is_active" /></el-form-item>
      <el-form-item label="权限">
        <div class="perm-tree">
          <div v-for="grp in groups" :key="grp.group" class="perm-group">
            <div class="perm-group-head">
              <el-checkbox
                :model-value="isGroupAll(grp)"
                :indeterminate="isGroupSome(grp)"
                @change="(v: any) => toggleGroup(grp, v)"
              >{{ grp.label }}</el-checkbox>
            </div>
            <el-checkbox-group v-model="form.permissions" class="perm-items">
              <el-checkbox
                v-for="p in grp.permissions"
                :key="p[0]"
                :value="p[0]"
                :label="p[0]"
              >{{ p[1] }}</el-checkbox>
            </el-checkbox-group>
          </div>
        </div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import { roleApi, permissionApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'

interface PermGroup {
  group: string
  label: string
  permissions: [string, string][]
}

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({})

const groups = ref<PermGroup[]>([])
const dialogVisible = ref(false)
const saving = ref(false)
const form = reactive<any>({ permissions: [] })

onMounted(async () => {
  const res = await permissionApi.tree() as any
  groups.value = res.data || []
})

function blank() {
  return { id: 0, name: '', code: '', description: '', sort_order: 0, is_active: true, permissions: [] as string[] }
}

function isGroupAll(grp: PermGroup) {
  return grp.permissions.every((p) => form.permissions.includes(p[0]))
}

function isGroupSome(grp: PermGroup) {
  const picked = grp.permissions.filter((p) => form.permissions.includes(p[0])).length
  return picked > 0 && picked < grp.permissions.length
}

function toggleGroup(grp: PermGroup, checked: boolean) {
  const codes = grp.permissions.map((p) => p[0])
  if (checked) {
    const set = new Set([...form.permissions, ...codes])
    form.permissions = Array.from(set)
  } else {
    form.permissions = form.permissions.filter((c: string) => !codes.includes(c))
  }
}

function onCreate() {
  Object.assign(form, blank())
  dialogVisible.value = true
}

function onEdit(row: any) {
  Object.assign(form, { ...blank(), ...row, permissions: [...(row.permissions || [])] })
  dialogVisible.value = true
}

async function onSave() {
  if (!form.name) { ElMessage.warning('请填写角色名称'); return }
  if (!form.code) { ElMessage.warning('请填写标识'); return }
  saving.value = true
  try {
    const payload = {
      name: form.name,
      code: form.code,
      description: form.description,
      sort_order: form.sort_order,
      is_active: form.is_active,
      permissions: form.permissions,
    }
    if (form.id) {
      await roleApi.update(form.id, payload)
    } else {
      await roleApi.create(payload)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value.reload()
  } finally {
    saving.value = false
  }
}

async function onDelete(row: any) {
  await ElMessageBox.confirm(`确定删除角色「${row.name}」吗？`, '提示', { type: 'warning' })
  await roleApi.remove(row.id)
  ElMessage.success('已删除')
  tableRef.value.reload()
}
</script>

<style scoped lang="scss">
.rl-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.rl-page :deep(.page-container) {
  padding: 0;
}
.perm-tree { width: 100%; }
.perm-group {
  padding: 8px 0;
  border-bottom: 1px dashed var(--border);
}
.perm-group:last-child { border-bottom: none; }
.perm-group-head { font-weight: 600; margin-bottom: 4px; }
.perm-items { padding-left: 24px; }
</style>
