<template>
  <div>
    <div class="ap-page-head">
      <p class="ap-page-intro">打手使用钱包按日购买通行证，同档续费顺延有效期；购买记录与平台收入同步留存</p>
    </div>
    <el-card shadow="never" class="pass-config" v-loading="loading">
      <template #header>价格与接单优先级</template>
      <el-alert v-if="loadError" title="规则加载失败，请重试" type="error" :closable="false" />
      <el-button v-if="loadError" @click="loadConfig">重新加载</el-button>
      <template v-if="config">
        <el-alert title="价格调整适用于后续购买，已购有效期保留；可见延迟调整立即影响所有打手的公共单池" type="info" :closable="false" />
        <el-table :data="config.products">
          <el-table-column prop="name" label="通行证" />
          <el-table-column label="每天价格（兴安币）">
            <template #default="{ row }"><el-input-number v-model="row.price_coin" :min="0.1" :precision="1" :disabled="!canEdit" /></template>
          </el-table-column>
          <el-table-column label="公共订单可见延迟（秒）">
            <template #default="{ row }"><el-input-number v-model="row.delay_seconds" :min="0" :max="86400" :precision="0" :disabled="!canEdit" /></template>
          </el-table-column>
        </el-table>
        <el-form label-width="190px" class="pass-footer">
          <el-form-item label="无通行证可见延迟（秒）">
            <el-input-number v-model="config.no_pass_delay_seconds" :min="0" :max="86400" :precision="0" :disabled="!canEdit" />
          </el-form-item>
        </el-form>
        <el-button v-if="canEdit" type="primary" :loading="saving" @click="saveConfig">保存规则</el-button>
      </template>
    </el-card>
    <crud-table :fetcher="passPurchaseApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-input v-model="query.keyword" placeholder="打手编号 / 昵称 / 账号 / 流水号" style="width: 280px" clearable @clear="reload" @keyup.enter="reload" />
        <el-select v-model="query.tier" placeholder="通行证" clearable style="width: 150px" @change="reload">
          <el-option v-for="item in config?.products || []" :key="item.tier" :value="item.tier" :label="item.name" />
        </el-select>
        <el-button type="primary" @click="reload">查询购买记录</el-button>
      </template>
      <el-table-column prop="account_no" label="打手编号" width="110" />
      <el-table-column label="打手" min-width="120"><template #default="{ row }">{{ row.nickname || row.username }}</template></el-table-column>
      <el-table-column prop="tier_display" label="通行证" width="120" />
      <el-table-column label="实付" width="120"><template #default="{ row }">{{ amountToXaCoin(row.paid_amount) }} 币</template></el-table-column>
      <el-table-column label="生效时间" width="170"><template #default="{ row }">{{ formatDateTime(row.starts_at) }}</template></el-table-column>
      <el-table-column label="到期时间" width="170"><template #default="{ row }">{{ formatDateTime(row.expires_at) }}</template></el-table-column>
      <el-table-column prop="tx_no" label="扣款流水" min-width="240" />
      <el-table-column label="购买时间" width="170"><template #default="{ row }">{{ formatDateTime(row.created_at) }}</template></el-table-column>
    </crud-table>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import { passPurchaseApi, providerPassConfigApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'
import { amountToXaCoin, xaCoinToAmount, formatDateTime } from '@/utils/format'

const auth = useAuthStore()
const canEdit = computed(() => auth.hasPerm('escort:edit'))
const config = ref<any>(null)
const query = reactive({ keyword: '', tier: '' })
const loading = ref(false)
const saving = ref(false)
const loadError = ref(false)
async function loadConfig() {
  loading.value = true
  loadError.value = false
  try {
    const result = await providerPassConfigApi.get() as any
    config.value = { ...result.data, products: result.data.products.map((item: any) => ({ ...item, price_coin: Number(amountToXaCoin(item.daily_price)) })) }
  } catch { loadError.value = true } finally { loading.value = false }
}
async function saveConfig() {
  if (!config.value || saving.value) return
  saving.value = true
  try {
    await providerPassConfigApi.update({
      products: config.value.products.map((item: any) => ({ tier: item.tier, daily_price: xaCoinToAmount(item.price_coin), delay_seconds: item.delay_seconds })),
      no_pass_delay_seconds: config.value.no_pass_delay_seconds,
    })
    ElMessage.success('通行证规则已更新')
    await loadConfig()
  } finally { saving.value = false }
}
onMounted(loadConfig)
</script>

<style scoped>
.pass-config { margin-bottom: 20px; }
.pass-footer { margin-top: 20px; }
</style>
