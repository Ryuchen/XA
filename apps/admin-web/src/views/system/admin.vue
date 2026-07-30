<template>
  <div class="ad-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">管理平台客服账号、职位分配与在职状态（PRD 7.14）</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('admin:edit')" type="primary" @click="onCreate">新增客服</el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="adminApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-input
          v-model="query.keyword"
          placeholder="账号/昵称/手机号"
          clearable
          style="width: 220px"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-button type="primary" @click="reload">查询</el-button>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="nickname" label="昵称" min-width="110" />
      <el-table-column label="职位" min-width="140">
        <template #default="{ row }">
          <template v-if="row.role_names && row.role_names.length">
            <el-tag v-for="name in row.role_names" :key="name" size="small" effect="light" style="margin: 2px">{{ name }}</el-tag>
          </template>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column prop="username" label="账号" min-width="120" />
      <el-table-column prop="phone" label="手机号" width="130" />
      <el-table-column label="今日派单额" width="120">
        <template #default="{ row }">{{ amountToXaCoin(row.today_dispatch_amount) }} 币</template>
      </el-table-column>
      <el-table-column prop="remark" label="备注" min-width="140" show-overflow-tooltip />
      <el-table-column label="超管" width="70">
        <template #default="{ row }">
          <el-tag v-if="row.is_superuser" type="danger" effect="light" round>超管</el-tag>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column label="在职状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" effect="light" round>{{ row.is_active ? '在职' : '离职' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <el-button v-if="auth.hasPerm('admin:edit')" link type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button
            v-if="auth.hasPerm('admin:edit') && !row.is_superuser"
            link
            type="danger"
            @click="onDelete(row)"
          >删除</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" :title="form.id ? '编辑客服' : '新增客服'" width="480px">
    <el-form :model="form" label-width="90px">
      <el-form-item label="账号">
        <el-input v-if="!form.id" v-model="form.username" />
        <el-input v-else :model-value="form.username" disabled />
      </el-form-item>
      <el-form-item v-if="!form.id" label="密码">
        <el-input v-model="form.password" type="password" show-password />
      </el-form-item>
      <el-form-item label="昵称"><el-input v-model="form.nickname" /></el-form-item>
      <el-form-item label="手机号"><el-input v-model="form.phone" /></el-form-item>
      <el-form-item label="职位">
        <el-select v-model="form.roles" multiple style="width: 100%" :disabled="form.is_superuser">
          <el-option v-for="r in roles" :key="r.id" :label="r.name" :value="r.id" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="form.id" label="在职状态">
        <el-switch v-model="form.is_active" :disabled="form.is_superuser" />
      </el-form-item>
      <el-form-item label="备注">
        <el-input v-model="form.remark" type="textarea" :rows="2" placeholder="选填" />
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
import { adminApi, roleApi } from '@/api/modules'
import { amountToXaCoin } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({ keyword: '' })

const roles = ref<any[]>([])
const dialogVisible = ref(false)
const saving = ref(false)
const form = reactive<any>({})

onMounted(async () => {
  const res = await roleApi.list({ page_size: 100 }) as any
  roles.value = res.data?.list || []
})

function blank() {
  return {
    id: 0, username: '', password: '', nickname: '', phone: '',
    remark: '', roles: [] as number[], is_active: true, is_superuser: false,
  }
}

function onCreate() {
  Object.assign(form, blank())
  dialogVisible.value = true
}

function onEdit(row: any) {
  Object.assign(form, { ...blank(), ...row, roles: [...(row.role_ids || [])] })
  dialogVisible.value = true
}

async function onSave() {
  if (!form.roles || form.roles.length === 0) { ElMessage.warning('请选择职位'); return }
  saving.value = true
  try {
    if (form.id) {
      await adminApi.update(form.id, {
        nickname: form.nickname,
        phone: form.phone,
        remark: form.remark,
        roles: form.roles,
        is_active: form.is_active,
      })
    } else {
      if (!form.username) { ElMessage.warning('请填写账号'); saving.value = false; return }
      if (!form.password) { ElMessage.warning('请填写密码'); saving.value = false; return }
      await adminApi.create({
        username: form.username,
        password: form.password,
        nickname: form.nickname,
        phone: form.phone,
        remark: form.remark,
        roles: form.roles,
      })
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value.reload()
  } finally {
    saving.value = false
  }
}

async function onDelete(row: any) {
  await ElMessageBox.confirm(`确定删除客服「${row.nickname || row.username}」吗？`, '提示', { type: 'warning' })
  await adminApi.remove(row.id)
  ElMessage.success('已删除')
  tableRef.value.reload()
}
</script>

<style scoped lang="scss">
.ad-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.ad-page :deep(.page-container) {
  padding: 0;
}
</style>
