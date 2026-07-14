<template>
  <div class="ci-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">配置消费签到、补签卡和月度全勤 KOOK Tag，并维护每天对应的礼物内容</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('checkin:edit')" type="primary" :loading="savingRule" @click="saveRule">
          保存签到规则
        </el-button>
      </div>
    </div>

    <div class="ci-rule-card">
      <div class="ci-card-title">月度签到规则</div>
      <el-form label-width="150px" :model="rule">
        <el-row :gutter="20">
          <el-col :xs="24" :md="8">
            <el-form-item label="当日签到门槛">
              <el-input-number v-model="dailySpendCoin" :min="1" :step="10" :precision="1" />
              <span class="ci-unit">兴安币</span>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="8">
            <el-form-item label="补签卡消费门槛">
              <el-input-number v-model="cardSpendCoin" :min="dailySpendCoin" :step="10" :precision="1" />
              <span class="ci-unit">兴安币</span>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="8">
            <el-form-item label="补签卡库存上限">
              <el-input-number v-model="rule.max_makeup_cards" :min="1" :max="31" />
              <span class="ci-unit">张/月</span>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="8">
            <el-form-item label="全勤奖励名称">
              <el-input v-model="rule.full_attendance_reward_name" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="8">
            <el-form-item label="KOOK Tag 标识">
              <el-input v-model="rule.full_attendance_tag_code" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="8">
            <el-form-item label="全勤奖励说明">
              <el-input v-model="rule.full_attendance_reward_desc" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <el-alert title="签到与补签记录按自然月独立统计；每日消费达到补签门槛时自动发放 1 张，库存不超过配置上限。" type="info" :closable="false" show-icon />
    </div>

    <div class="ci-section-head">
      <div><h3>签到礼物配置</h3><p>按本月累计签到第 N 天匹配，可设置礼物文案、图标和兴安币奖励。</p></div>
      <el-button v-if="auth.hasPerm('checkin:edit')" type="primary" plain @click="onCreateGift">新增礼物</el-button>
    </div>
    <crud-table ref="giftTable" :fetcher="checkinGiftApi.list" :query="{}">
      <el-table-column prop="checkin_day" label="签到第 N 天" width="120" />
      <el-table-column label="图标" width="70"><template #default="{ row }"><span class="ci-emoji">{{ row.icon || '🎁' }}</span></template></el-table-column>
      <el-table-column prop="name" label="礼物名称" min-width="150" />
      <el-table-column prop="description" label="礼物说明" min-width="220" show-overflow-tooltip />
      <el-table-column label="兴安币奖励" width="120"><template #default="{ row }">{{ amountToXaCoin(row.reward_amount) }} 币</template></el-table-column>
      <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? '启用' : '停用' }}</el-tag></template></el-table-column>
      <el-table-column label="操作" width="100" fixed="right"><template #default="{ row }"><el-button v-if="auth.hasPerm('checkin:edit')" link type="primary" @click="onEditGift(row)">编辑</el-button></template></el-table-column>
    </crud-table>

    <div class="ci-section-head"><div><h3>月度签到记录</h3><p>查看老板当月进度、补签卡余额与全勤 Tag 发放结果。</p></div></div>
    <crud-table :fetcher="checkinProgressApi.list" :query="progressQuery">
      <template #toolbar="{ reload }">
        <el-input-number v-model="progressQuery.year" :min="2024" :max="2100" controls-position="right" style="width: 120px" @change="reload" />
        <el-select v-model="progressQuery.month" style="width: 110px" @change="reload"><el-option v-for="m in 12" :key="m" :label="`${m}月`" :value="m" /></el-select>
      </template>
      <el-table-column prop="username" label="老板账号" min-width="130" />
      <el-table-column prop="nickname" label="老板昵称" min-width="120" />
      <el-table-column label="月份" width="100"><template #default="{ row }">{{ row.year }}-{{ String(row.month).padStart(2, '0') }}</template></el-table-column>
      <el-table-column prop="checked_count" label="已签到" width="90" />
      <el-table-column prop="makeup_cards" label="补签卡" width="90" />
      <el-table-column label="全勤奖励" min-width="180"><template #default="{ row }"><el-tag :type="row.full_attendance_awarded ? 'success' : 'info'">{{ row.full_attendance_awarded ? row.full_attendance_reward_name : '未达成' }}</el-tag></template></el-table-column>
      <el-table-column prop="full_attendance_tag_code" label="KOOK Tag" min-width="150" />
    </crud-table>
  </div>

  <el-dialog v-model="giftVisible" :title="giftForm.id ? '编辑签到礼物' : '新增签到礼物'" width="500px">
    <el-form :model="giftForm" label-width="110px">
      <el-form-item label="签到第 N 天"><el-input-number v-model="giftForm.checkin_day" :min="1" :max="31" /></el-form-item>
      <el-form-item label="礼物名称"><el-input v-model="giftForm.name" /></el-form-item>
      <el-form-item label="礼物图标"><el-input v-model="giftForm.icon" placeholder="支持 Emoji，如 🎁" /></el-form-item>
      <el-form-item label="礼物说明"><el-input v-model="giftForm.description" type="textarea" :rows="3" /></el-form-item>
      <el-form-item label="奖励兴安币"><el-input-number v-model="giftCoin" :min="0" :precision="1" :step="1" /></el-form-item>
      <el-form-item label="启用"><el-switch v-model="giftForm.is_active" /></el-form-item>
    </el-form>
    <template #footer><el-button @click="giftVisible = false">取消</el-button><el-button type="primary" :loading="savingGift" @click="saveGift">保存</el-button></template>
  </el-dialog>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import { checkinConfigApi, checkinGiftApi, checkinProgressApi } from '@/api/modules'
import { amountToXaCoin, xaCoinToAmount } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const giftTable = ref()
const savingRule = ref(false)
const savingGift = ref(false)
const giftVisible = ref(false)
const dailySpendCoin = ref(188)
const cardSpendCoin = ref(388)
const giftCoin = ref(0)
const now = new Date()
const progressQuery = reactive({ year: now.getFullYear(), month: now.getMonth() + 1 })
const rule = reactive<any>({ max_makeup_cards: 3, full_attendance_reward_name: 'KOOK专属Tag', full_attendance_reward_desc: '', full_attendance_tag_code: '' })
const giftForm = reactive<any>({})

async function loadRule() {
  const res = await checkinConfigApi.get()
  Object.assign(rule, res.data)
  dailySpendCoin.value = Number(amountToXaCoin(rule.daily_spend_required))
  cardSpendCoin.value = Number(amountToXaCoin(rule.makeup_card_spend_required))
}

async function saveRule() {
  savingRule.value = true
  try {
    await checkinConfigApi.update({
      ...rule,
      daily_spend_required: xaCoinToAmount(dailySpendCoin.value),
      makeup_card_spend_required: xaCoinToAmount(cardSpendCoin.value),
    })
    ElMessage.success('签到规则已保存')
    await loadRule()
  } finally { savingRule.value = false }
}

function onCreateGift() {
  Object.assign(giftForm, { id: 0, checkin_day: 1, name: '', description: '', icon: '🎁', is_active: true })
  giftCoin.value = 0
  giftVisible.value = true
}

function onEditGift(row: any) {
  Object.assign(giftForm, row)
  giftCoin.value = Number(amountToXaCoin(row.reward_amount))
  giftVisible.value = true
}

async function saveGift() {
  if (!giftForm.name) return void ElMessage.warning('请填写礼物名称')
  savingGift.value = true
  try {
    const payload = { ...giftForm, reward_amount: xaCoinToAmount(giftCoin.value) }
    if (giftForm.id) await checkinGiftApi.update(giftForm.id, payload)
    else await checkinGiftApi.create(payload)
    ElMessage.success('签到礼物已保存')
    giftVisible.value = false
    giftTable.value.reload()
  } finally { savingGift.value = false }
}

onMounted(loadRule)
</script>

<style scoped lang="scss">
.ci-page { display: flex; flex-direction: column; gap: 20px; }
.ci-page :deep(.page-container) { padding: 0; }
.ci-rule-card { padding: 22px; border: 1px solid var(--border); border-radius: 14px; background: var(--card); }
.ci-card-title { margin-bottom: 20px; font-size: 17px; font-weight: 650; }
.ci-unit { margin-left: 8px; color: var(--muted-foreground); font-size: 12px; }
.ci-section-head { display: flex; align-items: end; justify-content: space-between; margin-top: 8px; }
.ci-section-head h3 { margin: 0 0 5px; font-size: 17px; }
.ci-section-head p { margin: 0; color: var(--muted-foreground); font-size: 13px; }
.ci-emoji { font-size: 24px; }
</style>
