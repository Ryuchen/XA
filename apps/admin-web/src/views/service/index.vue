<template>
  <div class="sv-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">维护游戏类目与服务分类字典，配置分类下的项目 / 礼物、单价与抽成</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('service:edit')" @click="openGameCatDialog">类目设置</el-button>
        <el-button v-if="auth.hasPerm('service:edit')" @click="openServiceCatDialog">分类设置</el-button>
        <el-button v-if="auth.hasPerm('service:edit')" type="primary" @click="onCreate">新增服务项</el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="serviceItemApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-select v-model="query.game_category" placeholder="游戏类目" clearable style="width: 150px" @change="reload">
          <el-option v-for="c in gameCats" :key="c.id" :label="c.name" :value="c.id" />
        </el-select>
        <el-select v-model="query.service_category" placeholder="分类" clearable style="width: 150px" @change="reload">
          <el-option v-for="c in serviceCats" :key="c.id" :label="c.name" :value="c.id" />
        </el-select>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="封面" width="80">
        <template #default="{ row }">
          <el-image v-if="row.cover_url" :src="row.cover_url" fit="cover" style="width: 44px; height: 44px; border-radius: 6px" />
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column label="游戏类目" width="120">
        <template #default="{ row }">
          <span v-if="row.game_category_name">{{ row.game_category_name }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="分类" width="110">
        <template #default="{ row }">
          <el-tag v-if="row.service_category_name" effect="light" round>{{ row.service_category_name }}</el-tag>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="价格" width="100">
        <template #default="{ row }">{{ amountToXaCoin(row.price) }} 币</template>
      </el-table-column>
      <el-table-column label="抽成率" width="90">
        <template #default="{ row }">{{ row.commission_rate == null ? '全局' : row.commission_rate + '%' }}</template>
      </el-table-column>
      <el-table-column prop="sort_order" label="排序" width="80" />
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" effect="light" round>{{ row.is_active ? '上架' : '下架' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button v-if="auth.hasPerm('service:edit')" link type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button v-if="auth.hasPerm('service:delete')" link type="danger" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" :title="form.id ? '编辑服务项' : '新增服务项'" width="540px">
    <el-form :model="form" label-width="100px">
      <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="描述"><el-input v-model="form.description" /></el-form-item>
      <el-form-item label="游戏类目">
        <el-select v-model="form.game_category" placeholder="选择游戏类目" clearable style="width: 100%">
          <el-option v-for="c in gameCats" :key="c.id" :label="c.name" :value="c.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="分类">
        <el-select v-model="form.service_category" placeholder="选择分类" clearable style="width: 100%">
          <el-option v-for="c in serviceCats" :key="c.id" :label="c.name" :value="c.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="价格(兴安币)"><el-input-number v-model="priceCoin" :min="0" :precision="1" /></el-form-item>
      <el-form-item label="抽成率(%)">
        <el-input-number v-model="commissionRate" :min="0" :max="100" placeholder="留空" />
        <div class="tip">留空则按全局抽成率结算</div>
      </el-form-item>
      <el-form-item label="封面URL"><el-input v-model="form.cover_url" /></el-form-item>
      <el-form-item label="图集">
        <el-input v-model="imagesText" type="textarea" :rows="2" placeholder="每行一个图片URL" />
      </el-form-item>
      <el-form-item label="卖点">
        <el-input v-model="highlightsText" type="textarea" :rows="2" placeholder="每行一个卖点文案" />
      </el-form-item>
      <el-form-item label="排序"><el-input-number v-model="form.sort_order" :min="0" /></el-form-item>
      <el-form-item label="上架"><el-switch v-model="form.is_active" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
    </template>
  </el-dialog>

  <!-- 游戏类目设置 -->
  <el-dialog v-model="gameCatDialogVisible" title="游戏类目设置" width="640px">
    <div class="dict-head">
      <span class="dict-tip">游戏类目为字典项，新增服务项时按此下拉选择</span>
      <el-button size="small" type="primary" @click="addGameCatRow">新增类目</el-button>
    </div>
    <el-table :data="gameCatRows" size="small" border>
      <el-table-column label="名称" min-width="140">
        <template #default="{ row }"><el-input v-model="row.name" size="small" placeholder="如：王者荣耀" /></template>
      </el-table-column>
      <el-table-column label="备注" min-width="140">
        <template #default="{ row }"><el-input v-model="row.remark" size="small" /></template>
      </el-table-column>
      <el-table-column label="排序" width="90">
        <template #default="{ row }"><el-input-number v-model="row.sort_order" :min="0" size="small" controls-position="right" style="width: 76px" /></template>
      </el-table-column>
      <el-table-column label="启用" width="70">
        <template #default="{ row }"><el-switch v-model="row.is_active" size="small" /></template>
      </el-table-column>
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="saveGameCat(row)">保存</el-button>
          <el-button link type="danger" size="small" @click="deleteGameCat(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-dialog>

  <!-- 服务分类设置 -->
  <el-dialog v-model="serviceCatDialogVisible" title="分类设置" width="680px">
    <div class="dict-head">
      <span class="dict-tip">分类为字典项，「礼物」分类用于概览拆分与促销范围匹配</span>
      <el-button size="small" type="primary" @click="addServiceCatRow">新增分类</el-button>
    </div>
    <el-table :data="serviceCatRows" size="small" border>
      <el-table-column label="名称" min-width="130">
        <template #default="{ row }"><el-input v-model="row.name" size="small" placeholder="如：陪玩服务" /></template>
      </el-table-column>
      <el-table-column label="礼物分类" width="90">
        <template #default="{ row }"><el-switch v-model="row.is_gift" size="small" /></template>
      </el-table-column>
      <el-table-column label="备注" min-width="130">
        <template #default="{ row }"><el-input v-model="row.remark" size="small" /></template>
      </el-table-column>
      <el-table-column label="排序" width="90">
        <template #default="{ row }"><el-input-number v-model="row.sort_order" :min="0" size="small" controls-position="right" style="width: 76px" /></template>
      </el-table-column>
      <el-table-column label="启用" width="70">
        <template #default="{ row }"><el-switch v-model="row.is_active" size="small" /></template>
      </el-table-column>
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="saveServiceCat(row)">保存</el-button>
          <el-button link type="danger" size="small" @click="deleteServiceCat(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-dialog>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import { serviceItemApi, gameCategoryApi, serviceCategoryApi } from '@/api/modules'
import { amountToXaCoin, xaCoinToAmount } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({ game_category: '', service_category: '' })

const gameCats = ref<any[]>([])
const serviceCats = ref<any[]>([])

async function loadDicts() {
  const [g, s] = await Promise.all([
    gameCategoryApi.list({ page_size: 200 }) as any,
    serviceCategoryApi.list({ page_size: 200 }) as any,
  ])
  gameCats.value = g.data?.list || []
  serviceCats.value = s.data?.list || []
}

onMounted(loadDicts)

const dialogVisible = ref(false)
const saving = ref(false)
const priceCoin = ref(0)
const commissionRate = ref<number | undefined>(undefined)
const imagesText = ref('')
const highlightsText = ref('')
const form = reactive<any>({})

function blank() {
  return { id: 0, name: '', description: '', game_category: null, service_category: null, cover_url: '', sort_order: 0, is_active: true }
}

function toLines(arr: any): string {
  return Array.isArray(arr) ? arr.join('\n') : ''
}

function fromLines(text: string): string[] {
  return text.split('\n').map((s) => s.trim()).filter(Boolean)
}

function onCreate() {
  Object.assign(form, blank())
  priceCoin.value = 0
  commissionRate.value = undefined
  imagesText.value = ''
  highlightsText.value = ''
  dialogVisible.value = true
}

function onEdit(row: any) {
  Object.assign(form, row)
  priceCoin.value = Number(amountToXaCoin(row.price))
  commissionRate.value = row.commission_rate ?? undefined
  imagesText.value = toLines(row.images)
  highlightsText.value = toLines(row.highlights)
  dialogVisible.value = true
}

async function onSave() {
  if (!form.name) { ElMessage.warning('请填写名称'); return }
  saving.value = true
  try {
    const payload = {
      name: form.name,
      description: form.description,
      game_category: form.game_category || null,
      service_category: form.service_category || null,
      price: xaCoinToAmount(priceCoin.value),
      commission_rate: commissionRate.value == null ? null : commissionRate.value,
      cover_url: form.cover_url,
      images: fromLines(imagesText.value),
      highlights: fromLines(highlightsText.value),
      sort_order: form.sort_order,
      is_active: form.is_active,
    }
    if (form.id) {
      await serviceItemApi.update(form.id, payload)
    } else {
      await serviceItemApi.create(payload)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value.reload()
  } finally {
    saving.value = false
  }
}

async function onDelete(row: any) {
  await ElMessageBox.confirm(`确定删除服务项「${row.name}」吗？`, '提示', { type: 'warning' })
  await serviceItemApi.remove(row.id)
  ElMessage.success('已删除')
  tableRef.value.reload()
}

// ---------------- 游戏类目字典 ----------------
const gameCatDialogVisible = ref(false)
const gameCatRows = ref<any[]>([])

function openGameCatDialog() {
  gameCatRows.value = gameCats.value.map((c) => ({ ...c }))
  gameCatDialogVisible.value = true
}

function addGameCatRow() {
  gameCatRows.value.unshift({ id: 0, name: '', remark: '', sort_order: 0, is_active: true })
}

async function saveGameCat(row: any) {
  if (!row.name?.trim()) { ElMessage.warning('请填写名称'); return }
  const payload = { name: row.name, remark: row.remark, sort_order: row.sort_order, is_active: row.is_active }
  if (row.id) await gameCategoryApi.update(row.id, payload)
  else await gameCategoryApi.create(payload)
  ElMessage.success('已保存')
  await loadDicts()
  gameCatRows.value = gameCats.value.map((c) => ({ ...c }))
}

async function deleteGameCat(row: any) {
  if (!row.id) { gameCatRows.value = gameCatRows.value.filter((r) => r !== row); return }
  await ElMessageBox.confirm(`确定删除类目「${row.name}」吗？`, '提示', { type: 'warning' })
  await gameCategoryApi.remove(row.id)
  ElMessage.success('已删除')
  await loadDicts()
  gameCatRows.value = gameCats.value.map((c) => ({ ...c }))
}

// ---------------- 服务分类字典 ----------------
const serviceCatDialogVisible = ref(false)
const serviceCatRows = ref<any[]>([])

function openServiceCatDialog() {
  serviceCatRows.value = serviceCats.value.map((c) => ({ ...c }))
  serviceCatDialogVisible.value = true
}

function addServiceCatRow() {
  serviceCatRows.value.unshift({ id: 0, name: '', is_gift: false, remark: '', sort_order: 0, is_active: true })
}

async function saveServiceCat(row: any) {
  if (!row.name?.trim()) { ElMessage.warning('请填写名称'); return }
  const payload = { name: row.name, is_gift: row.is_gift, remark: row.remark, sort_order: row.sort_order, is_active: row.is_active }
  if (row.id) await serviceCategoryApi.update(row.id, payload)
  else await serviceCategoryApi.create(payload)
  ElMessage.success('已保存')
  await loadDicts()
  serviceCatRows.value = serviceCats.value.map((c) => ({ ...c }))
}

async function deleteServiceCat(row: any) {
  if (!row.id) { serviceCatRows.value = serviceCatRows.value.filter((r) => r !== row); return }
  await ElMessageBox.confirm(`确定删除分类「${row.name}」吗？`, '提示', { type: 'warning' })
  await serviceCategoryApi.remove(row.id)
  ElMessage.success('已删除')
  await loadDicts()
  serviceCatRows.value = serviceCats.value.map((c) => ({ ...c }))
}
</script>

<style scoped lang="scss">
.sv-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.sv-page :deep(.page-container) {
  padding: 0;
}
.tip {
  font-size: 12px;
  color: var(--muted-foreground);
  margin-left: 12px;
}
.muted {
  color: var(--muted-foreground);
}
.dict-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.dict-tip {
  font-size: 12px;
  color: var(--muted-foreground);
}
</style>
