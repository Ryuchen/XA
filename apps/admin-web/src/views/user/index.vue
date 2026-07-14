<template>
  <div class="us-page">
    <!-- 头部操作行 -->
    <div class="ap-page-head">
      <p class="ap-page-intro">管理老板账户余额、分级与消费信息（PRD 7.3）</p>
    </div>

    <!-- 老板余额三态说明卡 -->
    <section class="us-balance ap-card">
      <div class="us-balance-head">
        <span class="us-balance-ic"><el-icon><CreditCard /></el-icon></span>
        <div>
          <p class="ap-card-title">老板余额三态</p>
          <p class="ap-card-sub">派单并报单通过后自动从账户余额扣除；未报单金额为锁定态，报单前不计入可用余额</p>
        </div>
      </div>
      <div class="us-balance-grid">
        <article class="us-state us-state-avail">
          <span class="us-state-label">账户余额 <code class="us-code">wallet_balance</code></span>
          <span class="us-state-note">可用于派单扣款的余额</span>
        </article>
        <article class="us-state us-state-lock">
          <span class="us-state-label">未报单金额 <span class="us-lock-tag">锁定态</span></span>
          <span class="us-state-note">已派单未报单的锁定金额</span>
        </article>
        <article class="us-state us-state-spent">
          <span class="us-state-label">累计消费</span>
          <span class="us-state-note">历史累计已消费总额</span>
        </article>
      </div>
    </section>

    <crud-table ref="tableRef" :fetcher="userApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-autocomplete
          v-model="query.boss_no"
          :fetch-suggestions="querySuggest('boss_no')"
          placeholder="编号"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.nickname"
          :fetch-suggestions="querySuggest('nickname')"
          placeholder="昵称"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.phone"
          :fetch-suggestions="querySuggest('phone')"
          placeholder="手机号"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.is_active" placeholder="状态" clearable style="width: 120px" @change="reload">
          <el-option label="启用" value="true" />
          <el-option label="禁用" value="false" />
        </el-select>
        <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
      </template>

      <el-table-column label="编号" width="110">
      <template #default="{ row }">
        <span class="us-no">{{ row.boss_no || '-' }}</span>
      </template>
    </el-table-column>
    <el-table-column label="头像" width="70">
      <template #default="{ row }">
        <el-avatar :size="32" :src="row.avatar_url || undefined">{{ (row.nickname || row.username).charAt(0) }}</el-avatar>
      </template>
    </el-table-column>
    <el-table-column prop="username" label="账号" min-width="120" />
    <el-table-column prop="nickname" label="昵称" min-width="110" />
    <el-table-column prop="phone" label="手机号" width="120" />
    <el-table-column label="老板分级" width="110">
      <template #default="{ row }">{{ row.boss_type_name || '-' }}</template>
    </el-table-column>
    <el-table-column label="账户余额" width="120" align="right">
      <template #default="{ row }">
        <span class="us-balance-cell">{{ amountToXaCoin(row.wallet_balance) }} 币</span>
      </template>
    </el-table-column>
    <el-table-column label="推荐人" width="110">
      <template #default="{ row }">{{ row.inviter_name || '-' }}</template>
    </el-table-column>
    <el-table-column label="状态" width="80">
      <template #default="{ row }">
        <el-tag :type="row.is_active ? 'success' : 'danger'" effect="light" round>
          {{ row.is_active ? '启用' : '禁用' }}
        </el-tag>
      </template>
    </el-table-column>
    <el-table-column label="注册时间" width="160">
      <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
    </el-table-column>
    <el-table-column v-if="auth.hasPerm('user:edit')" label="操作" width="100" fixed="right">
      <template #default="{ row }">
        <el-button link type="primary" @click="onEdit(row)">编辑</el-button>
      </template>
    </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" title="编辑用户" width="640px" class="us-dialog">
    <el-form :model="editForm" label-width="82px" label-position="right">
      <div class="us-form-section">
        <p class="us-section-title">基础信息</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="账号"><el-input v-model="editForm.username" disabled /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="编号"><el-input v-model="editForm.boss_no" disabled placeholder="系统自动生成" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="昵称"><el-input v-model="editForm.nickname" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="真实姓名"><el-input v-model="editForm.real_name" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="手机号"><el-input v-model="editForm.phone" /></el-form-item>
          </el-col>
        </el-row>
      </div>

      <div class="us-form-section">
        <p class="us-section-title">归属与消费</p>
        <el-form-item label="老板分级">
          <el-select v-model="editForm.boss_type" placeholder="不分级" clearable style="width: 100%">
            <el-option
              v-for="bt in bossTypes"
              :key="bt.id"
              :label="`${bt.name}（${bt.discount_rate}%）`"
              :value="bt.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="推荐人">
          <el-select
            v-model="editForm.inviter"
            placeholder="搜索昵称"
            clearable
            filterable
            remote
            :remote-method="searchInviter"
            :loading="inviterLoading"
            style="width: 100%"
          >
            <el-option
              v-for="u in inviterOptions"
              :key="u.id"
              :label="u.boss_no ? `${u.nickname || '未命名'}（${u.boss_no}）` : (u.nickname || '未命名')"
              :value="u.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="分佣比例">
          <el-input-number v-model="editForm.inviter_commission_rate" :min="0" :max="100" />
          <span class="tip">推荐人从该老板订单收益的平台抽成中分得的比例（%），非订单金额</span>
        </el-form-item>
      </div>

      <div class="us-form-section">
        <p class="us-section-title">权限与状态</p>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="允许登录"><el-switch v-model="editForm.can_login" /></el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="允许查看"><el-switch v-model="editForm.can_view" /></el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="启用状态"><el-switch v-model="editForm.is_active" /></el-form-item>
          </el-col>
        </el-row>
      </div>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import { bossTypeApi, userApi } from '@/api/modules'
import { formatDateTime, amountToXaCoin } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({ boss_no: '', nickname: '', phone: '', is_active: '' })

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询后端，
 * 取返回记录的该字段值去重后作为下拉提示。
 */
function querySuggest(field: 'boss_no' | 'nickname' | 'phone') {
  return async (keyword: string, cb: (items: any[]) => void) => {
    if (!keyword) {
      cb([])
      return
    }
    try {
      const res = (await userApi.list({ [field]: keyword, page: 1, page_size: 10 })) as any
      const seen = new Set<string>()
      const items: { value: string }[] = []
      for (const u of res.data.list || []) {
        const value = u[field]
        if (value && !seen.has(value)) {
          seen.add(value)
          items.push({ value })
        }
      }
      cb(items)
    } catch {
      cb([])
    }
  }
}

const dialogVisible = ref(false)
const saving = ref(false)
const editForm = reactive<any>({})

const bossTypes = ref<any[]>([])
const inviterOptions = ref<any[]>([])
const inviterLoading = ref(false)

async function loadBossTypes() {
  if (bossTypes.value.length) return
  const res = await bossTypeApi.list({ page: 1, page_size: 100 }) as any
  bossTypes.value = res.data.list || []
}

async function searchInviter(keyword: string) {
  if (!keyword) {
    inviterOptions.value = []
    return
  }
  inviterLoading.value = true
  try {
    const res = await userApi.list({ nickname: keyword, page: 1, page_size: 20 }) as any
    inviterOptions.value = (res.data.list || []).filter((u: any) => u.id !== editForm.id)
  } finally {
    inviterLoading.value = false
  }
}

async function onEdit(row: any) {
  Object.assign(editForm, row)
  // 预置推荐人选项，保证回显当前推荐人名称（编号未知，仅回显昵称）
  inviterOptions.value = row.inviter
    ? [{ id: row.inviter, nickname: row.inviter_name, boss_no: '' }]
    : []
  dialogVisible.value = true
  await loadBossTypes()
}

async function onSave() {
  saving.value = true
  try {
    await userApi.update(editForm.id, {
      nickname: editForm.nickname,
      real_name: editForm.real_name,
      phone: editForm.phone,
      is_active: editForm.is_active,
      boss_type: editForm.boss_type || null,
      inviter: editForm.inviter || null,
      inviter_commission_rate: editForm.inviter_commission_rate || 0,
      can_login: editForm.can_login,
      can_view: editForm.can_view,
    })
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value.load()
  } finally {
    saving.value = false
  }
}
</script>

<style scoped lang="scss">
.tip {
  font-size: 12px;
  color: var(--muted-foreground);
  margin-left: 12px;
}

/* 编辑弹窗：分区分组 + token 视觉 */
.us-dialog :deep(.el-dialog__body) {
  padding-top: 8px;
}
.us-form-section {
  padding: 4px 0 2px;
}
.us-form-section + .us-form-section {
  margin-top: 8px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}
.us-section-title {
  margin: 0 0 14px;
  padding-left: 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--foreground);
  line-height: 1.2;
  border-left: 3px solid var(--primary);
}
.us-form-section :deep(.el-form-item) {
  margin-bottom: 16px;
}

.us-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 让 CrudTable 内置的外层 padding 归零，融入 us-page 的间距节奏 */
.us-page :deep(.page-container) {
  padding: 0;
}

/* 老板余额三态说明卡 */
.us-balance {
  padding: 20px 24px;
}
.us-balance-head {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 18px;
}
.us-balance-ic {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 44px;
  width: 44px;
  height: 44px;
  border-radius: var(--radius-sm);
  background: var(--secondary);
  color: var(--primary);
  font-size: 22px;
}
.us-balance-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.us-state {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 16px 18px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--background);
}
.us-state-avail {
  border-left: 3px solid var(--success);
}
.us-state-lock {
  border-left: 3px solid var(--chart-3);
}
.us-state-spent {
  border-left: 3px solid var(--primary);
}
.us-state-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--foreground);
  display: flex;
  align-items: center;
  gap: 6px;
}
.us-state-note {
  font-size: 12px;
  color: var(--muted-foreground);
}
.us-code {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--secondary);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--muted-foreground);
}
.us-lock-tag {
  padding: 1px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--chart-3) 16%, var(--card));
  color: var(--chart-3);
  font-size: 11px;
}
.us-balance-cell {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.us-no {
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.02em;
}

@media (max-width: 900px) {
  .us-balance-grid {
    grid-template-columns: 1fr;
  }
}
</style>
