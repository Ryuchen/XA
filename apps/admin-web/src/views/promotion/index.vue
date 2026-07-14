<template>
  <div class="pm-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">统一管理促销活动的适用范围、折扣与抽成覆盖，实时掌控活动生效窗口</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('promotion:edit')" type="primary" @click="onCreate">新增活动</el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="promotionApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-input
          v-model="query.keyword"
          placeholder="活动名称/备注"
          clearable
          style="width: 200px"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.scope" placeholder="适用范围" clearable style="width: 130px" @change="reload">
          <el-option v-for="(v, k) in PROMOTION_SCOPE" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-select v-model="query.is_active" placeholder="状态" clearable style="width: 120px" @change="reload">
          <el-option label="启用" value="true" />
          <el-option label="停用" value="false" />
        </el-select>
        <el-button type="primary" @click="reload">查询</el-button>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="title" label="活动名称" min-width="150" />
      <el-table-column label="适用范围" width="120">
        <template #default="{ row }">
          <el-tag :type="PROMOTION_SCOPE[row.scope]?.type" effect="light" round>{{ row.scope_display }}</el-tag>
          <span v-if="row.scope === 'CATEGORY'" class="sub">{{ row.category_name || '—' }}</span>
          <span v-else-if="row.scope === 'ITEMS'" class="sub">{{ row.item_names?.length || 0 }} 件</span>
        </template>
      </el-table-column>
      <el-table-column label="活动折扣" width="100">
        <template #default="{ row }">
          <span v-if="row.discount_rate != null">{{ row.discount_rate }}%</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="覆盖抽成" width="100">
        <template #default="{ row }">
          <span v-if="row.commission_rate != null">{{ row.commission_rate }}%</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="时间窗" min-width="280">
        <template #default="{ row }">
          {{ formatDateTime(row.start_at) }} ~ {{ formatDateTime(row.end_at) }}
        </template>
      </el-table-column>
      <el-table-column prop="priority" label="优先级" width="80" />
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" effect="light" round>{{ row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button v-if="auth.hasPerm('promotion:edit')" link type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button v-if="auth.hasPerm('promotion:delete')" link type="danger" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" :title="form.id ? '编辑促销活动' : '新增促销活动'" width="560px">
    <el-form :model="form" label-width="110px">
      <el-form-item label="活动名称"><el-input v-model="form.title" placeholder="如：双十一限时折扣" /></el-form-item>
      <el-form-item label="适用范围">
        <el-radio-group v-model="form.scope">
          <el-radio v-for="(v, k) in PROMOTION_SCOPE" :key="k" :label="k">{{ v.label }}</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item v-if="form.scope === 'CATEGORY'" label="适用分类">
        <el-select v-model="form.category" placeholder="选择分类" style="width: 100%">
          <el-option v-for="c in serviceCats" :key="c.id" :label="c.name" :value="c.id" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="form.scope === 'ITEMS'" label="指定商品">
        <el-select
          v-model="form.items"
          multiple
          filterable
          placeholder="选择参与活动的商品"
          style="width: 100%"
        >
          <el-option
            v-for="s in serviceOptions"
            :key="s.id"
            :label="`${s.name}（${amountToXaCoin(s.price)}币）`"
            :value="s.id"
          />
        </el-select>
      </el-form-item>

      <el-form-item label="活动折扣">
        <el-switch v-model="discountEnabled" />
        <el-input-number
          v-if="discountEnabled"
          v-model="form.discount_rate"
          :min="1"
          :max="100"
          style="margin-left: 12px"
        />
        <span class="tip">{{ discountEnabled ? '100=原价，90=九折；与优惠券互斥' : '关闭=不影响老板实付' }}</span>
      </el-form-item>
      <el-form-item label="覆盖抽成">
        <el-switch v-model="commissionEnabled" />
        <el-input-number
          v-if="commissionEnabled"
          v-model="form.commission_rate"
          :min="0"
          :max="100"
          style="margin-left: 12px"
        />
        <span class="tip">{{ commissionEnabled ? '强制使用该抽成率，优先级最高' : '关闭=不覆盖抽成（按等级/店铺）' }}</span>
      </el-form-item>

      <el-form-item label="开始时间">
        <el-date-picker v-model="form.start_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
      </el-form-item>
      <el-form-item label="结束时间">
        <el-date-picker v-model="form.end_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
      </el-form-item>
      <el-form-item label="优先级">
        <el-input-number v-model="form.priority" :min="0" />
        <span class="tip">并发多活动时取优先级最高的一条</span>
      </el-form-item>
      <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" :rows="2" /></el-form-item>
      <el-form-item label="启用"><el-switch v-model="form.is_active" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive, ref, watch, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import { promotionApi, serviceItemApi, serviceCategoryApi } from '@/api/modules'
import { PROMOTION_SCOPE } from '@/utils/dict'
import { amountToXaCoin, formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({ keyword: '', scope: '', is_active: '' })

const dialogVisible = ref(false)
const saving = ref(false)
const form = reactive<any>({})
const discountEnabled = ref(true)
const commissionEnabled = ref(false)
const serviceOptions = ref<any[]>([])
const serviceCats = ref<any[]>([])

onMounted(async () => {
  const res = await serviceCategoryApi.list({ page_size: 200 }) as any
  serviceCats.value = res.data?.list || []
})

// 关闭开关时清空对应字段，避免提交残留值
watch(discountEnabled, (on) => { if (!on) form.discount_rate = null })
watch(commissionEnabled, (on) => { if (!on) form.commission_rate = null })

function blank() {
  return {
    id: 0, title: '', scope: 'ALL', category: null, items: [],
    discount_rate: 90, commission_rate: null,
    start_at: '', end_at: '', priority: 0, remark: '', is_active: true,
  }
}

async function ensureServices() {
  if (serviceOptions.value.length) return
  const res = await serviceItemApi.list({ page_size: 200 }) as any
  serviceOptions.value = res.data?.list || []
}

async function onCreate() {
  Object.assign(form, blank())
  discountEnabled.value = true
  commissionEnabled.value = false
  await ensureServices()
  dialogVisible.value = true
}

async function onEdit(row: any) {
  Object.assign(form, {
    ...row,
    items: (row.item_names || []).map((i: any) => i.id),
  })
  discountEnabled.value = row.discount_rate != null
  commissionEnabled.value = row.commission_rate != null
  await ensureServices()
  dialogVisible.value = true
}

async function onSave() {
  if (!form.title) { ElMessage.warning('请填写活动名称'); return }
  if (form.scope === 'CATEGORY' && !form.category) { ElMessage.warning('请选择适用分类'); return }
  if (form.scope === 'ITEMS' && !form.items.length) { ElMessage.warning('请选择指定商品'); return }
  if (!form.start_at || !form.end_at) { ElMessage.warning('请选择起止时间'); return }
  if (form.end_at <= form.start_at) { ElMessage.warning('结束时间必须晚于开始时间'); return }
  saving.value = true
  try {
    const payload = {
      title: form.title,
      scope: form.scope,
      category: form.scope === 'CATEGORY' ? form.category : null,
      items: form.scope === 'ITEMS' ? form.items : [],
      discount_rate: discountEnabled.value ? form.discount_rate : null,
      commission_rate: commissionEnabled.value ? form.commission_rate : null,
      start_at: form.start_at,
      end_at: form.end_at,
      priority: form.priority,
      remark: form.remark,
      is_active: form.is_active,
    }
    if (form.id) {
      await promotionApi.update(form.id, payload)
    } else {
      await promotionApi.create(payload)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value.reload()
  } finally {
    saving.value = false
  }
}

async function onDelete(row: any) {
  await ElMessageBox.confirm(`确定删除活动「${row.title}」吗？`, '提示', { type: 'warning' })
  await promotionApi.remove(row.id)
  ElMessage.success('已删除')
  tableRef.value.reload()
}
</script>

<style scoped lang="scss">
.pm-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.pm-page :deep(.page-container) {
  padding: 0;
}
.tip {
  font-size: 12px;
  color: var(--muted-foreground);
  margin-left: 12px;
}
.sub {
  margin-left: 6px;
  font-size: 12px;
  color: var(--muted-foreground);
}
.muted {
  color: var(--muted-foreground);
}
</style>
