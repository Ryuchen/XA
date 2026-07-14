<template>
  <div class="es-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">管理入驻陪玩师的资质、状态与业绩</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('escort:edit')" type="primary" @click="onCreate">创建陪玩账号</el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="escortApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-input
          v-model="query.keyword"
          placeholder="昵称/账号/手机号"
          clearable
          style="width: 200px"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.status" placeholder="状态" clearable style="width: 120px" @change="reload">
          <el-option v-for="(v, k) in ESCORT_STATUS" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-select v-model="query.is_verified" placeholder="认证" clearable style="width: 120px" @change="reload">
          <el-option label="已认证" value="true" />
          <el-option label="未认证" value="false" />
        </el-select>
        <el-button type="primary" @click="reload">查询</el-button>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="display_name" label="昵称" min-width="120" />
      <el-table-column label="性别" width="80">
        <template #default="{ row }">
          <el-tag :type="GENDER[row.gender]?.type" effect="light" round>{{ row.gender_display || GENDER[row.gender]?.label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="username" label="账号" min-width="120" />
      <el-table-column prop="escort_no" label="编号" width="110">
        <template #default="{ row }">{{ row.escort_no || '-' }}</template>
      </el-table-column>
      <el-table-column prop="city" label="城市" width="90" />
      <el-table-column label="时薪" width="80">
        <template #default="{ row }">{{ row.price_per_hour ?? '-' }}</template>
      </el-table-column>
      <el-table-column prop="rating_avg" label="评分" width="70" />
      <el-table-column prop="completed_order_count" label="完成单" width="80" />
      <el-table-column label="抢单通行证" width="120">
        <template #default="{ row }">
          <el-tag :type="row.active_pass_tier === 'BLACK' ? 'info' : row.active_pass_tier ? 'warning' : 'info'" effect="light" round>
            {{ row.pass_tier_display }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="累计奖/罚" width="120">
        <template #default="{ row }">
          <span class="es-reward">+{{ amountToXaCoin(row.total_reward) }}币</span>
          /
          <span class="es-penalty">-{{ amountToXaCoin(row.total_penalty) }}币</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="ESCORT_STATUS[row.status]?.type" effect="light" round>{{ row.status_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="认证" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_verified ? 'success' : 'info'" effect="light" round>{{ row.is_verified ? '已认证' : '未认证' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="210" fixed="right">
        <template #default="{ row }">
          <el-button v-if="auth.hasPerm('escort:edit')" link type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button
            v-if="auth.hasPerm('escort:verify')"
            link
            :type="row.is_verified ? 'warning' : 'success'"
            @click="onVerify(row)"
          >{{ row.is_verified ? '取消认证' : '认证' }}</el-button>
          <el-button v-if="auth.hasPerm('escort:dispose')" link type="danger" @click="onDispose(row)">奖罚</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" title="编辑陪玩资料" width="640px" class="es-dialog">
    <el-form :model="editForm" label-width="96px" label-position="right">
      <div class="es-form-section">
        <p class="es-section-title">基础资料</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="昵称"><el-input v-model="editForm.display_name" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="性别">
              <el-select v-model="editForm.gender" style="width: 100%">
                <el-option v-for="(v, k) in GENDER" :key="k" :label="v.label" :value="k" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="城市"><el-input v-model="editForm.city" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="服务区"><el-input v-model="editForm.service_area" /></el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="简介"><el-input v-model="editForm.bio" type="textarea" :rows="2" placeholder="选填" /></el-form-item>
      </div>

      <div class="es-form-section">
        <p class="es-section-title">陪玩形象</p>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="头像" label-position="top">
              <el-upload
                :show-file-list="false"
                accept="image/*"
                :auto-upload="false"
                :on-change="(f: any) => onPickFile('avatar', f)"
              >
                <div class="es-uploader es-uploader--img">
                  <img v-if="editPreview.avatar" :src="editPreview.avatar" class="es-thumb" />
                  <el-icon v-else><Plus /></el-icon>
                </div>
              </el-upload>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="查外挂图" label-position="top">
              <el-upload
                :show-file-list="false"
                accept="image/*"
                :auto-upload="false"
                :on-change="(f: any) => onPickFile('cheat_proof', f)"
              >
                <div class="es-uploader es-uploader--img">
                  <img v-if="editPreview.cheat_proof" :src="editPreview.cheat_proof" class="es-thumb" />
                  <el-icon v-else><Plus /></el-icon>
                </div>
              </el-upload>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="视频介绍" label-position="top">
              <el-upload
                :show-file-list="false"
                accept="video/*"
                :auto-upload="false"
                :on-change="(f: any) => onPickFile('intro_video', f)"
              >
                <div class="es-uploader es-uploader--video">
                  <video v-if="editPreview.intro_video" :src="editPreview.intro_video" class="es-thumb" />
                  <el-icon v-else><VideoCamera /></el-icon>
                </div>
              </el-upload>
              <div v-if="editFiles.intro_video" class="tip">{{ editFiles.intro_video.name }}</div>
            </el-form-item>
          </el-col>
        </el-row>
      </div>

      <div class="es-form-section">
        <p class="es-section-title">业务档案</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="时薪"><el-input-number v-model="editForm.price_per_hour" :min="0" style="width: 100%" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="段位"><el-input v-model="editForm.rank_tier" /></el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="胜率(%)"><el-input-number v-model="editForm.win_rate" :min="0" :max="100" :precision="2" style="width: 100%" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="陪玩等级">
              <el-select v-model="editForm.level" clearable placeholder="未设置（按店铺抽成）" style="width: 100%">
                <el-option
                  v-for="lv in levelOptions"
                  :key="lv.id"
                  :label="`${lv.name}（抽成 ${lv.commission_rate}%）`"
                  :value="lv.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
      </div>

      <div class="es-form-section">
        <p class="es-section-title">押金与状态</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="应缴押金(币)"><el-input-number v-model="depositRequiredYuan" :min="0" :precision="1" :step="10" style="width: 100%" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="已缴押金(币)"><el-input-number v-model="depositPaidYuan" :min="0" :precision="1" :step="10" style="width: 100%" /></el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="状态">
          <el-select v-model="editForm.status" style="width: 100%">
            <el-option v-for="(v, k) in ESCORT_STATUS" :key="k" :label="v.label" :value="k" />
          </el-select>
        </el-form-item>
      </div>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="createVisible" title="创建陪玩账号" width="640px" class="es-dialog">
    <el-form :model="createForm" :rules="createRules" ref="createFormRef" label-width="96px" label-position="right">
      <div class="es-form-section">
        <p class="es-section-title">登录凭证</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="登录账号" prop="username">
              <el-input v-model="createForm.username" placeholder="陪玩登录用账号" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="登录密码" prop="password">
              <el-input v-model="createForm.password" type="password" show-password placeholder="至少 6 位" />
            </el-form-item>
          </el-col>
        </el-row>
      </div>

      <div class="es-form-section">
        <p class="es-section-title">基础资料</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="昵称" prop="display_name">
              <el-input v-model="createForm.display_name" placeholder="对外展示的陪玩昵称" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="性别">
              <el-select v-model="createForm.gender" style="width: 100%">
                <el-option v-for="(v, k) in GENDER" :key="k" :label="v.label" :value="k" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="手机号">
              <el-input v-model="createForm.phone" placeholder="选填" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="城市">
              <el-input v-model="createForm.city" placeholder="选填" />
            </el-form-item>
          </el-col>
        </el-row>
      </div>

      <div class="es-form-section">
        <p class="es-section-title">陪玩形象</p>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="头像" label-position="top">
              <el-upload
                :show-file-list="false"
                accept="image/*"
                :auto-upload="false"
                :on-change="(f: any) => onPickCreateFile('avatar', f)"
              >
                <div class="es-uploader es-uploader--img">
                  <img v-if="createPreview.avatar" :src="createPreview.avatar" class="es-thumb" />
                  <el-icon v-else><Plus /></el-icon>
                </div>
              </el-upload>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="查外挂图" label-position="top">
              <el-upload
                :show-file-list="false"
                accept="image/*"
                :auto-upload="false"
                :on-change="(f: any) => onPickCreateFile('cheat_proof', f)"
              >
                <div class="es-uploader es-uploader--img">
                  <img v-if="createPreview.cheat_proof" :src="createPreview.cheat_proof" class="es-thumb" />
                  <el-icon v-else><Plus /></el-icon>
                </div>
              </el-upload>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="视频介绍" label-position="top">
              <el-upload
                :show-file-list="false"
                accept="video/*"
                :auto-upload="false"
                :on-change="(f: any) => onPickCreateFile('intro_video', f)"
              >
                <div class="es-uploader es-uploader--video">
                  <video v-if="createPreview.intro_video" :src="createPreview.intro_video" class="es-thumb" />
                  <el-icon v-else><VideoCamera /></el-icon>
                </div>
              </el-upload>
              <div v-if="createFiles.intro_video" class="tip">{{ createFiles.intro_video.name }}</div>
            </el-form-item>
          </el-col>
        </el-row>
      </div>

      <div class="es-form-section">
        <p class="es-section-title">计费与押金</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="陪玩等级">
              <el-select v-model="createForm.level" clearable placeholder="未设置（按店铺抽成）" style="width: 100%">
                <el-option
                  v-for="lv in levelOptions"
                  :key="lv.id"
                  :label="`${lv.name}（抽成 ${lv.commission_rate}%）`"
                  :value="lv.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="应缴押金(币)">
              <el-input-number v-model="createDepositYuan" :min="0" :precision="2" :step="10" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
      </div>
    </el-form>
    <template #footer>
      <el-button @click="createVisible = false">取消</el-button>
      <el-button type="primary" :loading="creating" @click="onCreateSave">创建</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="disposeVisible" title="奖励 / 罚款" width="440px">
    <el-form label-width="100px">
      <el-form-item label="陪玩">{{ current?.display_name || current?.username }}</el-form-item>
      <el-form-item label="类型">
        <el-radio-group v-model="disposeType">
          <el-radio label="REWARD">奖励</el-radio>
          <el-radio label="PENALTY">罚款</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="金额(兴安币)">
        <el-input-number v-model="disposeYuan" :min="0" :precision="2" :step="10" style="width: 100%" />
        <div class="tip">{{ disposeType === 'REWARD' ? '奖励将增加陪玩余额' : '罚款将扣减陪玩余额（不可使余额为负）' }}</div>
      </el-form-item>
      <el-form-item label="原因">
        <el-input v-model="disposeReason" type="textarea" :rows="2" placeholder="奖罚原因" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="disposeVisible = false">取消</el-button>
      <el-button type="primary" :loading="disposing" @click="onDisposeSave">确定</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { Plus, VideoCamera } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import { escortApi, escortLevelApi } from '@/api/modules'
import { ESCORT_STATUS, GENDER } from '@/utils/dict'
import { amountToXaCoin, xaCoinToAmount } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({ keyword: '', status: '', is_verified: '' })

const dialogVisible = ref(false)
const saving = ref(false)
const editForm = reactive<any>({})
const depositRequiredYuan = ref<number>(0)
const depositPaidYuan = ref<number>(0)
const levelOptions = ref<any[]>([])

type MediaKey = 'avatar' | 'intro_video' | 'cheat_proof'
const editFiles = reactive<Record<MediaKey, File | null>>({ avatar: null, intro_video: null, cheat_proof: null })
const editPreview = reactive<Record<MediaKey, string>>({ avatar: '', intro_video: '', cheat_proof: '' })

function onPickFile(key: MediaKey, file: any) {
  const raw: File = file.raw
  editFiles[key] = raw
  editPreview[key] = URL.createObjectURL(raw)
}

async function ensureLevels() {
  if (levelOptions.value.length) return
  const res = (await escortLevelApi.list({ is_active: 'true', page_size: 200 })) as any
  levelOptions.value = res.data?.list || []
}

async function onEdit(row: any) {
  Object.assign(editForm, row)
  // DRF DecimalField 序列化为字符串，el-input-number 需要 Number
  editForm.win_rate = row.win_rate != null ? Number(row.win_rate) : 0
  depositRequiredYuan.value = Number(amountToXaCoin(row.deposit_required))
  depositPaidYuan.value = Number(amountToXaCoin(row.deposit_paid))
  editFiles.avatar = editFiles.intro_video = editFiles.cheat_proof = null
  editPreview.avatar = row.avatar_url || ''
  editPreview.intro_video = row.intro_video_url || ''
  editPreview.cheat_proof = row.cheat_proof_url || ''
  dialogVisible.value = true
  ensureLevels()
}

async function onSave() {
  saving.value = true
  try {
    const base: Record<string, any> = {
      display_name: editForm.display_name,
      gender: editForm.gender,
      city: editForm.city ?? '',
      service_area: editForm.service_area ?? '',
      price_per_hour: editForm.price_per_hour,
      rank_tier: editForm.rank_tier ?? '',
      win_rate: editForm.win_rate,
      level: editForm.level ?? null,
      deposit_required: xaCoinToAmount(depositRequiredYuan.value),
      deposit_paid: xaCoinToAmount(depositPaidYuan.value),
      status: editForm.status,
      bio: editForm.bio ?? '',
    }
    const hasFile = editFiles.avatar || editFiles.intro_video || editFiles.cheat_proof
    let payload: any = base
    if (hasFile) {
      // multipart 无法表达 null，level 为空时不提交以保留原值
      const fd = new FormData()
      Object.entries(base).forEach(([k, v]) => {
        if (k === 'level' && (v === null || v === '')) return
        if (v === null || v === undefined) return
        fd.append(k, String(v))
      })
      ;(['avatar', 'intro_video', 'cheat_proof'] as MediaKey[]).forEach((k) => {
        if (editFiles[k]) fd.append(k, editFiles[k] as File)
      })
      payload = fd
    }
    await escortApi.update(editForm.id, payload)
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value.load()
  } finally {
    saving.value = false
  }
}

async function onVerify(row: any) {
  await escortApi.action(row.id, 'verify', { is_verified: !row.is_verified })
  ElMessage.success('操作成功')
  tableRef.value.load()
}

// ---------------- 创建陪玩账号 ----------------
const createVisible = ref(false)
const creating = ref(false)
const createFormRef = ref<FormInstance>()
const createDepositYuan = ref<number>(0)
const defaultCreateForm = () => ({
  username: '',
  password: '',
  display_name: '',
  gender: 'UNKNOWN',
  phone: '',
  city: '',
  level: null as number | null,
})
const createForm = reactive<Record<string, any>>(defaultCreateForm())
const createFiles = reactive<Record<MediaKey, File | null>>({ avatar: null, intro_video: null, cheat_proof: null })
const createPreview = reactive<Record<MediaKey, string>>({ avatar: '', intro_video: '', cheat_proof: '' })
const createRules: FormRules = {
  username: [{ required: true, message: '请输入登录账号', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入登录密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
  display_name: [{ required: true, message: '请输入昵称', trigger: 'blur' }],
}

function onPickCreateFile(key: MediaKey, file: any) {
  const raw: File = file.raw
  createFiles[key] = raw
  createPreview[key] = URL.createObjectURL(raw)
}

function onCreate() {
  Object.assign(createForm, defaultCreateForm())
  createDepositYuan.value = 0
  createFiles.avatar = createFiles.intro_video = createFiles.cheat_proof = null
  createPreview.avatar = createPreview.intro_video = createPreview.cheat_proof = ''
  createVisible.value = true
  ensureLevels()
  createFormRef.value?.clearValidate()
}

async function onCreateSave() {
  if (!createFormRef.value) return
  await createFormRef.value.validate(async (valid) => {
    if (!valid) return
    creating.value = true
    try {
      const fd = new FormData()
      fd.append('username', createForm.username.trim())
      fd.append('password', createForm.password)
      fd.append('display_name', createForm.display_name.trim())
      fd.append('gender', createForm.gender)
      fd.append('phone', createForm.phone.trim())
      fd.append('city', createForm.city.trim())
      fd.append('deposit_required', String(xaCoinToAmount(createDepositYuan.value)))
      if (createForm.level != null) fd.append('level', String(createForm.level))
      ;(['avatar', 'intro_video', 'cheat_proof'] as MediaKey[]).forEach((k) => {
        if (createFiles[k]) fd.append(k, createFiles[k] as File)
      })
      await escortApi.create(fd)
      ElMessage.success('陪玩账号创建成功')
      createVisible.value = false
      tableRef.value.load()
    } finally {
      creating.value = false
    }
  })
}

const disposeVisible = ref(false)
const disposing = ref(false)
const current = ref<any>(null)
const disposeType = ref<'REWARD' | 'PENALTY'>('REWARD')
const disposeYuan = ref<number>(0)
const disposeReason = ref('')

function onDispose(row: any) {
  current.value = row
  disposeType.value = 'REWARD'
  disposeYuan.value = 0
  disposeReason.value = ''
  disposeVisible.value = true
}

async function onDisposeSave() {
  const amount = xaCoinToAmount(disposeYuan.value)
  if (amount <= 0) {
    ElMessage.warning('金额必须大于 0')
    return
  }
  disposing.value = true
  try {
    await escortApi.action(current.value.id, 'dispose', {
      dispose_type: disposeType.value,
      amount,
      reason: disposeReason.value,
    })
    ElMessage.success('操作成功')
    disposeVisible.value = false
    tableRef.value.load()
  } finally {
    disposing.value = false
  }
}
</script>

<style scoped lang="scss">
.tip {
  font-size: 12px;
  color: var(--muted-foreground);
}

.es-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.es-page :deep(.page-container) {
  padding: 0;
}

.es-reward {
  color: var(--success);
  font-variant-numeric: tabular-nums;
}
.es-penalty {
  color: var(--destructive);
  font-variant-numeric: tabular-nums;
}

.es-dialog :deep(.el-dialog__body) {
  padding-top: 8px;
}
.es-form-section {
  padding: 4px 0 2px;
}
.es-form-section + .es-form-section {
  margin-top: 8px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}
.es-section-title {
  margin: 0 0 14px;
  padding-left: 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--foreground);
  line-height: 1.2;
  border-left: 3px solid var(--primary);
}
.es-form-section :deep(.el-form-item) {
  margin-bottom: 16px;
}

.es-uploader {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100px;
  border: 1px dashed var(--border);
  border-radius: 8px;
  cursor: pointer;
  overflow: hidden;
  color: var(--muted-foreground);
  transition: border-color 0.2s;
  background: var(--muted);
}
.es-uploader:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.es-uploader .el-icon {
  font-size: 24px;
}
.es-thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
</style>
