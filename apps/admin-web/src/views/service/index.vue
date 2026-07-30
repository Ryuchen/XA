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
          <el-option-group label="陪玩">
            <el-option v-for="c in playCats" :key="c.id" :label="c.name" :value="c.id" />
          </el-option-group>
          <el-option-group label="礼物">
            <el-option v-for="c in giftCats" :key="c.id" :label="c.name" :value="c.id" />
          </el-option-group>
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
      <el-table-column label="要求档位" width="100">
        <template #default="{ row }">
          <el-tag v-if="row.required_level_name" type="warning" effect="light" round>{{ row.required_level_name }}</el-tag>
          <span v-else class="muted">不限</span>
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
          <el-option-group label="陪玩">
            <el-option v-for="c in playCats" :key="c.id" :label="c.name" :value="c.id" />
          </el-option-group>
          <el-option-group label="礼物">
            <el-option v-for="c in giftCats" :key="c.id" :label="c.name" :value="c.id" />
          </el-option-group>
        </el-select>
      </el-form-item>
      <el-form-item label="要求档位">
        <el-select v-model="form.required_level" placeholder="不限档位" clearable style="width: 100%">
          <el-option v-for="l in escortLevels" :key="l.id" :label="l.name" :value="l.id" />
        </el-select>
        <div class="tip">仅该档位及以上的陪玩可接单；留空表示不限档位</div>
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
  <el-dialog v-model="gameCatDialogVisible" title="游戏类目设置" width="760px" @closed="onGameCatDialogClosed">
    <div class="dict-head">
      <span class="dict-tip">游戏类目为字典项，新增服务项时按此下拉选择；图标将展示在老板端选择页</span>
      <el-button size="small" type="primary" @click="startCreateGameCat">新增类目</el-button>
    </div>
    <div class="gc-layout">
      <div class="gc-list">
        <div
          v-for="cat in gameCats"
          :key="cat.id"
          :class="['gc-list-item', gcEditing.id === cat.id ? 'active' : '']"
          @click="startEditGameCat(cat)"
        >
          <el-image v-if="cat.icon_url" :src="cat.icon_url" fit="contain" class="gc-list-icon" />
          <div v-else class="gc-list-icon gc-list-icon--empty"><el-icon><Picture /></el-icon></div>
          <div class="gc-list-main">
            <span class="gc-list-name">{{ cat.name }}</span>
            <span class="gc-list-remark">{{ cat.remark || '—' }}</span>
          </div>
          <el-tag :type="cat.is_active ? 'success' : 'info'" effect="light" round size="small">
            {{ cat.is_active ? '启用' : '停用' }}
          </el-tag>
        </div>
        <el-empty v-if="gameCats.length === 0" description="暂无游戏类目" :image-size="60" />
      </div>

      <div v-if="gcEditing.visible" class="gc-form">
        <div class="gc-form-title">{{ gcEditing.id ? '编辑类目' : '新增类目' }}</div>
        <el-form :model="gcEditing" label-width="72px">
          <el-form-item label="图标">
            <div class="gc-icon-edit">
              <el-image v-if="gcIconPreview" :src="gcIconPreview" fit="contain" class="gc-icon-preview" />
              <div v-else class="gc-icon-preview gc-icon-preview--empty"><el-icon :size="24"><Picture /></el-icon></div>
              <div class="gc-icon-ops">
                <el-upload :auto-upload="false" :show-file-list="false" accept="image/*" :on-change="onGcIconChange">
                  <el-button size="small">选择图标</el-button>
                </el-upload>
                <span class="tip">建议正方形图片，未上传则展示默认图标</span>
              </div>
            </div>
          </el-form-item>
          <el-form-item label="名称">
            <el-input v-model="gcEditing.name" placeholder="如：王者荣耀" />
          </el-form-item>
          <el-form-item label="备注">
            <el-input v-model="gcEditing.remark" placeholder="可选" />
          </el-form-item>
          <el-form-item label="排序">
            <el-input-number v-model="gcEditing.sort_order" :min="0" />
          </el-form-item>
          <el-form-item label="启用">
            <el-switch v-model="gcEditing.is_active" />
          </el-form-item>
        </el-form>
        <div class="gc-form-footer">
          <el-button
            v-if="gcEditing.id"
            type="danger"
            plain
            @click="deleteGameCat(gcEditing)"
          >删除</el-button>
          <div class="gc-form-footer-right">
            <el-button @click="cancelGameCatEdit">取消</el-button>
            <el-button type="primary" :loading="gcSaving" @click="saveGameCat">保存</el-button>
          </div>
        </div>
      </div>
      <div v-else class="gc-form gc-form--placeholder">
        <el-empty description="从左侧选择类目进行编辑，或点击「新增类目」" :image-size="70" />
      </div>
    </div>
  </el-dialog>

  <!-- 服务分类设置：两级结构（顶层 陪玩/礼物 固定，组内维护子类） -->
  <el-dialog v-model="serviceCatDialogVisible" title="分类设置" width="720px">
    <div class="dict-head">
      <span class="dict-tip">分类分「陪玩」「礼物」两大类，各自维护子类；「礼物」子类用于概览拆分与促销范围匹配</span>
    </div>
    <div class="sc-groups">
      <div v-for="group in scGroups" :key="group.key" class="sc-group">
        <div class="sc-group-head">
          <span class="sc-group-title">{{ group.title }}</span>
          <el-button size="small" type="primary" plain @click="addServiceCatRow(group.isGift)">新增{{ group.title }}子类</el-button>
        </div>
        <el-table :data="rowsOfGroup(group.isGift)" size="small" border>
          <el-table-column label="子类名称" min-width="130">
            <template #default="{ row }"><el-input v-model="row.name" size="small" :placeholder="group.placeholder" /></template>
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
          <template #empty>暂无子类，点击上方按钮新增</template>
        </el-table>
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type UploadFile } from 'element-plus'
import { Picture } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import { serviceItemApi, gameCategoryApi, serviceCategoryApi, escortLevelApi } from '@/api/modules'
import { amountToXaCoin, xaCoinToAmount } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({ game_category: '', service_category: '' })

const gameCats = ref<any[]>([])
const serviceCats = ref<any[]>([])
const escortLevels = ref<any[]>([])

// 分类按顶层归属派生：陪玩(is_gift=false) / 礼物(is_gift=true)
const playCats = computed(() => serviceCats.value.filter((c) => !c.is_gift))
const giftCats = computed(() => serviceCats.value.filter((c) => c.is_gift))

async function loadDicts() {
  const [g, s, l] = await Promise.all([
    gameCategoryApi.list({ page_size: 200 }) as any,
    serviceCategoryApi.list({ page_size: 200 }) as any,
    escortLevelApi.list({ page_size: 200 }) as any,
  ])
  gameCats.value = g.data?.list || []
  serviceCats.value = s.data?.list || []
  escortLevels.value = l.data?.list || []
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
  return { id: 0, name: '', description: '', game_category: null, service_category: null, required_level: null, cover_url: '', sort_order: 0, is_active: true }
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
      required_level: form.required_level || null,
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
const gcSaving = ref(false)
const gcIconPreview = ref('')
const gcIconFile = ref<File | null>(null)
const gcEditing = reactive<any>({ visible: false, id: 0, name: '', remark: '', sort_order: 0, is_active: true })

function openGameCatDialog() {
  resetGameCatEdit()
  gameCatDialogVisible.value = true
}

function resetGameCatEdit() {
  Object.assign(gcEditing, { visible: false, id: 0, name: '', remark: '', sort_order: 0, is_active: true })
  gcIconPreview.value = ''
  gcIconFile.value = null
}

function startCreateGameCat() {
  Object.assign(gcEditing, { visible: true, id: 0, name: '', remark: '', sort_order: 0, is_active: true })
  gcIconPreview.value = ''
  gcIconFile.value = null
}

function startEditGameCat(cat: any) {
  Object.assign(gcEditing, {
    visible: true,
    id: cat.id,
    name: cat.name,
    remark: cat.remark,
    sort_order: cat.sort_order,
    is_active: cat.is_active,
  })
  gcIconPreview.value = cat.icon_url || ''
  gcIconFile.value = null
}

function cancelGameCatEdit() {
  resetGameCatEdit()
}

function onGameCatDialogClosed() {
  resetGameCatEdit()
}

function onGcIconChange(uploadFile: UploadFile) {
  const raw = uploadFile.raw
  if (!raw) return
  gcIconFile.value = raw
  gcIconPreview.value = URL.createObjectURL(raw)
}

async function saveGameCat() {
  if (!gcEditing.name?.trim()) { ElMessage.warning('请填写名称'); return }
  gcSaving.value = true
  try {
    const fd = new FormData()
    fd.append('name', gcEditing.name.trim())
    fd.append('remark', gcEditing.remark || '')
    fd.append('sort_order', String(gcEditing.sort_order))
    fd.append('is_active', String(gcEditing.is_active))
    if (gcIconFile.value) fd.append('icon', gcIconFile.value)
    if (gcEditing.id) await gameCategoryApi.update(gcEditing.id, fd)
    else await gameCategoryApi.create(fd)
    ElMessage.success('已保存')
    await loadDicts()
    resetGameCatEdit()
  } finally {
    gcSaving.value = false
  }
}

async function deleteGameCat(cat: any) {
  if (!cat.id) { resetGameCatEdit(); return }
  await ElMessageBox.confirm(`确定删除类目「${cat.name}」吗？`, '提示', { type: 'warning' })
  await gameCategoryApi.remove(cat.id)
  ElMessage.success('已删除')
  await loadDicts()
  resetGameCatEdit()
}

// ---------------- 服务分类字典（两级：陪玩/礼物 顶层固定，组内维护子类） ----------------
const serviceCatDialogVisible = ref(false)
const serviceCatRows = ref<any[]>([])

const scGroups = [
  { key: 'play', title: '陪玩', isGift: false, placeholder: '如：排位' },
  { key: 'gift', title: '礼物', isGift: true, placeholder: '如：限定' },
]

function rowsOfGroup(isGift: boolean) {
  return serviceCatRows.value.filter((r) => !!r.is_gift === isGift)
}

function openServiceCatDialog() {
  serviceCatRows.value = serviceCats.value.map((c) => ({ ...c }))
  serviceCatDialogVisible.value = true
}

function addServiceCatRow(isGift: boolean) {
  serviceCatRows.value.unshift({ id: 0, name: '', is_gift: isGift, remark: '', sort_order: 0, is_active: true })
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
.gc-layout {
  display: flex;
  gap: 16px;
  align-items: stretch;
}
.gc-list {
  width: 320px;
  flex-shrink: 0;
  max-height: 420px;
  overflow-y: auto;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 6px;
}
.gc-list-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.2s;
}
.gc-list-item:hover {
  background: var(--el-fill-color-light);
}
.gc-list-item.active {
  background: var(--el-color-primary-light-9);
}
.gc-list-icon {
  width: 36px;
  height: 36px;
  border-radius: 6px;
  flex-shrink: 0;
  background: var(--el-fill-color-lighter);
}
.gc-list-icon--empty {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--muted-foreground);
}
.gc-list-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.gc-list-name {
  font-size: 14px;
  color: var(--el-text-color-primary);
}
.gc-list-remark {
  font-size: 12px;
  color: var(--muted-foreground);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.gc-form {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
}
.gc-form--placeholder {
  align-items: center;
  justify-content: center;
}
.gc-form-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 16px;
}
.gc-icon-edit {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  width: 100%;
}
.gc-icon-preview {
  width: 64px;
  height: 64px;
  border-radius: 8px;
  flex-shrink: 0;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-lighter);
}
.gc-icon-preview--empty {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--muted-foreground);
}
.gc-icon-ops {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  min-width: 0;
  padding-top: 2px;
}
.gc-icon-ops .tip {
  font-size: 12px;
  line-height: 1.4;
  color: var(--muted-foreground);
}
.gc-form-footer {
  margin-top: auto;
  padding-top: 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.gc-form-footer-right {
  margin-left: auto;
}
.sc-groups {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.sc-group-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.sc-group-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
</style>
