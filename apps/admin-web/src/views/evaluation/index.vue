<template>
  <div class="ev-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">集中查看老板对陪玩的服务评价，可回复与管理评价内容</p>
    </div>

    <crud-table ref="tableRef" :fetcher="evaluationApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-autocomplete
          v-model="query.order_no"
          :fetch-suggestions="querySuggest('order_no')"
          placeholder="订单号"
          clearable
          value-key="value"
          style="width: 180px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.customer_username"
          :fetch-suggestions="querySuggest('customer_username')"
          placeholder="老板账号"
          clearable
          value-key="value"
          style="width: 140px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.customer_nickname"
          :fetch-suggestions="querySuggest('customer_nickname')"
          placeholder="老板昵称"
          clearable
          value-key="value"
          style="width: 140px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.provider_username"
          :fetch-suggestions="querySuggest('provider_username')"
          placeholder="陪玩账号"
          clearable
          value-key="value"
          style="width: 140px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.provider_nickname"
          :fetch-suggestions="querySuggest('provider_nickname')"
          placeholder="陪玩昵称"
          clearable
          value-key="value"
          style="width: 140px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.score" placeholder="评分" clearable style="width: 110px" @change="reload">
          <el-option v-for="n in 5" :key="n" :label="`${n} 星`" :value="n" />
        </el-select>
        <el-select v-model="query.only_unreplied" placeholder="回复状态" clearable style="width: 130px" @change="reload">
          <el-option label="未回复" value="1" />
        </el-select>
        <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
      </template>

      <el-table-column prop="order_no" label="订单号" min-width="190" />
      <el-table-column prop="customer_name" label="老板" width="130" />
      <el-table-column prop="provider_name" label="陪玩" width="120">
        <template #default="{ row }">{{ row.provider_name || '-' }}</template>
      </el-table-column>
      <el-table-column prop="service_name" label="服务" min-width="130" />
      <el-table-column label="评分" width="120">
        <template #default="{ row }">
          <el-rate :model-value="row.score" disabled size="small" />
        </template>
      </el-table-column>
      <el-table-column label="三维分" width="150">
        <template #default="{ row }">
          <span class="dim">技 {{ row.skill_score }}</span>
          <span class="dim">态 {{ row.attitude_score }}</span>
          <span class="dim">沟 {{ row.communication_score }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="content" label="评价内容" min-width="200" show-overflow-tooltip />
      <el-table-column label="回复" width="80">
        <template #default="{ row }">
          <el-tag v-if="row.reply_content" type="success" size="small" effect="light" round>已回复</el-tag>
          <el-tag v-else type="info" size="small" effect="light" round>未回复</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="评价时间" width="160">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="onDetail(row)">详情</el-button>
          <el-button
            v-if="auth.hasPerm('evaluation:reply')"
            link
            type="warning"
            @click="onReply(row)"
          >回复</el-button>
          <el-button
            v-if="auth.hasPerm('evaluation:delete')"
            link
            type="danger"
            @click="onDelete(row)"
          >删除</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-drawer v-model="detailVisible" title="评价详情" size="480px">
    <template v-if="current">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="订单号">{{ current.order_no }}</el-descriptions-item>
        <el-descriptions-item label="老板">{{ current.customer_name }}</el-descriptions-item>
        <el-descriptions-item label="陪玩">{{ current.provider_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="服务">{{ current.service_name }}</el-descriptions-item>
        <el-descriptions-item label="评分">
          <el-rate :model-value="current.score" disabled size="small" />
        </el-descriptions-item>
        <el-descriptions-item label="专业技能">{{ current.skill_score }} 分</el-descriptions-item>
        <el-descriptions-item label="服务态度">{{ current.attitude_score }} 分</el-descriptions-item>
        <el-descriptions-item label="沟通配合">{{ current.communication_score }} 分</el-descriptions-item>
        <el-descriptions-item label="综合均分">{{ current.avg_score }} 分</el-descriptions-item>
        <el-descriptions-item label="评价内容">{{ current.content || '-' }}</el-descriptions-item>
        <el-descriptions-item label="匿名">{{ current.is_anonymous ? '是' : '否' }}</el-descriptions-item>
        <el-descriptions-item label="回复">{{ current.reply_content || '-' }}</el-descriptions-item>
        <el-descriptions-item label="回复时间">{{ current.replied_at ? formatDateTime(current.replied_at) : '-' }}</el-descriptions-item>
        <el-descriptions-item label="评价时间">{{ formatDateTime(current.created_at) }}</el-descriptions-item>
      </el-descriptions>
    </template>
  </el-drawer>

  <el-dialog v-model="replyVisible" title="回复评价" width="480px">
    <el-input
      v-model="replyText"
      type="textarea"
      :rows="4"
      maxlength="500"
      show-word-limit
      placeholder="请输入回复内容"
    />
    <template #footer>
      <el-button @click="replyVisible = false">取消</el-button>
      <el-button type="primary" :loading="replying" @click="submitReply">发布回复</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import { evaluationApi } from '@/api/modules'
import { formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({
  order_no: '',
  customer_username: '',
  customer_nickname: '',
  provider_username: '',
  provider_nickname: '',
  score: '',
  only_unreplied: '',
})

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询评价后，
 * 取返回记录对应字段值去重后作为下拉提示。
 */
function querySuggest(
  field: 'order_no' | 'customer_username' | 'customer_nickname' | 'provider_username' | 'provider_nickname',
) {
  return async (keyword: string, cb: (items: any[]) => void) => {
    if (!keyword) {
      cb([])
      return
    }
    try {
      const res = (await evaluationApi.list({ [field]: keyword, page: 1, page_size: 10 })) as any
      const seen = new Set<string>()
      const items: { value: string }[] = []
      for (const e of res.data?.list || []) {
        const value = e[field] || ''
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

const detailVisible = ref(false)
const current = ref<any>(null)

function onDetail(row: any) {
  current.value = row
  detailVisible.value = true
}

const replyVisible = ref(false)
const replyText = ref('')
const replying = ref(false)
const replyTarget = ref<any>(null)

function onReply(row: any) {
  replyTarget.value = row
  replyText.value = row.reply_content || ''
  replyVisible.value = true
}

async function submitReply() {
  const content = replyText.value.trim()
  if (!content) {
    ElMessage.warning('请输入回复内容')
    return
  }
  replying.value = true
  try {
    await evaluationApi.action(replyTarget.value.id, 'reply', { reply_content: content })
    ElMessage.success('已回复')
    replyVisible.value = false
    tableRef.value.load()
  } finally {
    replying.value = false
  }
}

async function onDelete(row: any) {
  try {
    await ElMessageBox.confirm('删除后将回退陪玩的评分统计，确认删除该评价？', '删除评价', {
      type: 'warning',
      confirmButtonText: '确认删除',
    })
  } catch {
    return
  }
  await evaluationApi.remove(row.id)
  ElMessage.success('已删除')
  tableRef.value.load()
}
</script>

<style scoped lang="scss">
.ev-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.ev-page :deep(.page-container) {
  padding: 0;
}
.dim {
  display: inline-block;
  margin-right: 8px;
  font-size: 12px;
  color: var(--muted-foreground);
}
</style>
