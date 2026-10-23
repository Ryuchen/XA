<template>
  <el-alert title="接单档位现在按游戏、方向、平台和难度分别管理。请在陪玩管理中配置个人资格，在服务项目中选择订单档位；下方旧等级仅用于历史服务。" type="info" :closable="false" style="margin-bottom:16px" />
  <el-card shadow="never" style="margin-bottom:16px"><template #header>当前档位与抽成规则</template><el-table :data="scopedRows" size="small"><el-table-column prop="scope" label="接单范围" min-width="180"/><el-table-column prop="grades" label="档位从低到高" min-width="240"/><el-table-column prop="rates" label="订单档位抽成" min-width="280"/></el-table><p style="font-size:12px;color:var(--el-text-color-secondary)">同一范围高档可接低档，抽成按订单档位；通行证仅影响接单时间。代肝、上分保留服务项目原抽成规则。</p></el-card>
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

  <el-dialog v-model="dialogVisible" :title="form.id ? '编辑陪玩等级' : '新增陪玩等级'" width="560px" class="ap-modal">
    <el-form :model="form"  class="ap-modal-form ap-form-grid" label-position="top">
      <el-form-item label="等级名称"><el-input v-model="form.name" placeholder="如：金牌打手" /></el-form-item>
      <el-form-item label="抽成率(%)">
        <el-input-number v-model="form.commission_rate" :min="0" :max="100" />
        <div class="tip">该等级陪玩接单时的平台抽成率，优先级高于店铺设置</div>
      </el-form-item>
      <el-form-item label="备注" class="ap-field--full"><el-input v-model="form.remark" type="textarea" :rows="2" /></el-form-item>
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
import { reactive, ref, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import { qualificationOptions } from '@/utils/qualifications'
import { escortLevelApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'

const scopedMeta=ref<any>({scopes:[],kinds:[],platforms:[]});onMounted(async()=>{scopedMeta.value=await qualificationOptions()})
const scopedRows=computed(()=>scopedMeta.value.scopes.map((s:any)=>({scope:[scopedMeta.value.platforms.find((p:any)=>p.value===s.platform)?.label,({CONFIDENTIAL:'机密',TOP_SECRET:'绝密',ANY:''} as Record<string,string>)[s.security_level],scopedMeta.value.kinds.find((k:any)=>k.value===s.provider_kind)?.label].filter(Boolean).join(' · '),grades:s.grades.map((g:any)=>g.label).join(' → '),rates:s.grades.map((g:any)=>g.label+' '+(g.commission_rate==null?'按项目':g.commission_rate+'%')).join(' / ')})))
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
