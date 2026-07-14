<template>
  <div class="bn-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">维护首页轮播图的图片、跳转目标与展示顺序，把控用户首屏的运营触点</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('banner:edit')" type="primary" @click="onCreate">新增轮播</el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="bannerApi.list" :query="query">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="图片" width="160">
        <template #default="{ row }">
          <el-image v-if="row.image_url" :src="row.image_url" fit="cover" style="width: 120px; height: 51px; border-radius: 6px" />
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column prop="title" label="标题" min-width="140" />
      <el-table-column label="跳转类型" width="120">
        <template #default="{ row }">
          <el-tag :type="LINK_TYPE[row.link_type]?.type" effect="light" round>{{ row.link_type_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="link_value" label="跳转值" min-width="140" />
      <el-table-column prop="sort_order" label="排序" width="80" />
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" effect="light" round>{{ row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button v-if="auth.hasPerm('banner:edit')" link type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button v-if="auth.hasPerm('banner:delete')" link type="danger" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" :title="form.id ? '编辑轮播' : '新增轮播'" width="520px">
    <el-form :model="form" label-width="100px">
      <el-form-item label="图片">
        <div>
          <el-image v-if="previewUrl" :src="previewUrl" fit="cover" style="width: 225px; height: 96px; border-radius: 6px; margin-bottom: 8px" />
          <el-upload
            :auto-upload="false"
            :show-file-list="false"
            accept="image/*"
            :on-change="onFileChange"
          >
            <el-button>选择图片</el-button>
          </el-upload>
          <span class="tip">必须为 {{ IMG_W }}×{{ IMG_H }} 像素</span>
        </div>
      </el-form-item>
      <el-form-item label="标题"><el-input v-model="form.title" /></el-form-item>
      <el-form-item label="跳转类型">
        <el-select v-model="form.link_type" style="width: 100%">
          <el-option v-for="(v, k) in LINK_TYPE" :key="k" :label="v.label" :value="k" />
        </el-select>
      </el-form-item>
      <el-form-item label="跳转值">
        <el-input v-model="form.link_value" placeholder="商品ID/公告ID/URL，无跳转可留空" />
      </el-form-item>
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
import { ElMessage, ElMessageBox, type UploadFile } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import { bannerApi } from '@/api/modules'
import { LINK_TYPE } from '@/utils/dict'
import { useAuthStore } from '@/stores/auth'

const IMG_W = 750
const IMG_H = 320

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({})

const dialogVisible = ref(false)
const saving = ref(false)
const previewUrl = ref('')
const pickedFile = ref<File | null>(null)
const form = reactive<any>({})

function blank() {
  return { id: 0, title: '', link_type: 'NONE', link_value: '', sort_order: 0, is_active: true }
}

function onCreate() {
  Object.assign(form, blank())
  previewUrl.value = ''
  pickedFile.value = null
  dialogVisible.value = true
}

function onEdit(row: any) {
  Object.assign(form, row)
  previewUrl.value = row.image_url || ''
  pickedFile.value = null
  dialogVisible.value = true
}

function checkDimension(file: File): Promise<boolean> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      URL.revokeObjectURL(url)
      resolve(img.width === IMG_W && img.height === IMG_H)
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      resolve(false)
    }
    img.src = url
  })
}

async function onFileChange(uploadFile: UploadFile) {
  const raw = uploadFile.raw
  if (!raw) return
  const ok = await checkDimension(raw)
  if (!ok) {
    ElMessage.error(`图片尺寸必须为 ${IMG_W}×${IMG_H} 像素`)
    return
  }
  pickedFile.value = raw
  previewUrl.value = URL.createObjectURL(raw)
}

async function onSave() {
  if (!form.id && !pickedFile.value) { ElMessage.warning('请上传图片'); return }
  saving.value = true
  try {
    const fd = new FormData()
    if (pickedFile.value) fd.append('image', pickedFile.value)
    fd.append('title', form.title || '')
    fd.append('link_type', form.link_type)
    fd.append('link_value', form.link_value || '')
    fd.append('sort_order', String(form.sort_order))
    fd.append('is_active', String(form.is_active))
    if (form.id) {
      await bannerApi.update(form.id, fd)
    } else {
      await bannerApi.create(fd)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value.reload()
  } finally {
    saving.value = false
  }
}

async function onDelete(row: any) {
  await ElMessageBox.confirm('确定删除该轮播图吗？', '提示', { type: 'warning' })
  await bannerApi.remove(row.id)
  ElMessage.success('已删除')
  tableRef.value.reload()
}
</script>

<style scoped lang="scss">
.bn-page {
  display: flex;
  flex-direction: column;
  gap: 20px;

  :deep(.page-container) {
    padding: 0;
  }
}

.tip {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: var(--muted-foreground);
}
</style>
