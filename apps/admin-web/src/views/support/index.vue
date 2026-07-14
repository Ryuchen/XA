<template>
  <div class="sp-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">维护客服名片信息，配置微信号与二维码，为用户提供便捷的咨询入口</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('support:edit')" type="primary" @click="onCreate">新增名片</el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="supportCardApi.list" :query="query">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="头像" width="70">
        <template #default="{ row }">
          <el-avatar :size="36" :src="row.avatar_url || undefined">{{ (row.name || '客').charAt(0) }}</el-avatar>
        </template>
      </el-table-column>
      <el-table-column prop="name" label="名称" min-width="120" />
      <el-table-column prop="company" label="所属" min-width="120" />
      <el-table-column prop="wechat_id" label="微信号" min-width="130" />
      <el-table-column label="企业微信客服" min-width="130">
        <template #default="{ row }">
          <el-tag :type="row.wecom_corp_id && row.wecom_service_url ? 'success' : 'info'" effect="light" round>
            {{ row.wecom_corp_id && row.wecom_service_url ? '已接入' : '未配置' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="二维码" width="80">
        <template #default="{ row }">
          <el-image v-if="row.qrcode_url" :src="row.qrcode_url" fit="cover" style="width: 40px; height: 40px; border-radius: 4px" :preview-src-list="[row.qrcode_url]" />
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
          <el-button v-if="auth.hasPerm('support:edit')" link type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button v-if="auth.hasPerm('support:delete')" link type="danger" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" :title="form.id ? '编辑客服名片' : '新增客服名片'" width="500px">
    <el-form :model="form" label-width="90px">
      <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="所属"><el-input v-model="form.company" /></el-form-item>
      <el-form-item label="微信号"><el-input v-model="form.wechat_id" /></el-form-item>
      <el-divider content-position="left">企业微信客服</el-divider>
      <el-form-item label="企业ID">
        <el-input v-model="form.wecom_corp_id" placeholder="例如：wwxxxxxxxxxxxxxxxx" />
      </el-form-item>
      <el-form-item label="客服链接">
        <el-input v-model="form.wecom_service_url" placeholder="https://work.weixin.qq.com/kfid/..." />
      </el-form-item>
      <el-alert class="wecom-tip" type="info" :closable="false" show-icon title="企业ID和客服链接需在企业微信「微信客服」后台获取，并同时填写。" />
      <el-form-item label="头像URL"><el-input v-model="form.avatar_url" /></el-form-item>
      <el-form-item label="二维码URL"><el-input v-model="form.qrcode_url" /></el-form-item>
      <el-form-item label="提示语"><el-input v-model="form.tips" type="textarea" :rows="2" /></el-form-item>
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
import { supportCardApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({})

const dialogVisible = ref(false)
const saving = ref(false)
const form = reactive<any>({})

function blank() {
  return { id: 0, name: '', company: '', wechat_id: '', wecom_corp_id: '', wecom_service_url: '', avatar_url: '', qrcode_url: '', tips: '', sort_order: 0, is_active: true }
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
  if (!form.name) { ElMessage.warning('请填写名称'); return }
  saving.value = true
  try {
    const payload = {
      name: form.name,
      company: form.company,
      wechat_id: form.wechat_id,
      wecom_corp_id: form.wecom_corp_id,
      wecom_service_url: form.wecom_service_url,
      avatar_url: form.avatar_url,
      qrcode_url: form.qrcode_url,
      tips: form.tips,
      sort_order: form.sort_order,
      is_active: form.is_active,
    }
    if (form.id) {
      await supportCardApi.update(form.id, payload)
    } else {
      await supportCardApi.create(payload)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value.reload()
  } finally {
    saving.value = false
  }
}

async function onDelete(row: any) {
  await ElMessageBox.confirm(`确定删除客服名片「${row.name}」吗？`, '提示', { type: 'warning' })
  await supportCardApi.remove(row.id)
  ElMessage.success('已删除')
  tableRef.value.reload()
}
</script>

<style scoped lang="scss">
.sp-page {
  display: flex;
  flex-direction: column;
  gap: 20px;

  :deep(.page-container) {
    padding: 0;
  }
}

.wecom-tip {
  margin: -4px 0 16px;
}
</style>
