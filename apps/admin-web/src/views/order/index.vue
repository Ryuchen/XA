<template>
  <div class="od-page">
    <!-- 头部操作行 -->
    <div class="ap-page-head">
      <p class="ap-page-intro">管理平台全部陪玩订单与售后</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('order:view')" :icon="Setting" @click="trafficConfigVisible = true">峰谷限流与监控</el-button>
        <el-button v-if="auth.hasPerm('order:dispatch')" type="primary" :icon="Plus" @click="onDispatch">快捷派单</el-button>
        <el-button v-if="auth.hasPerm(['dispatch_config:view', 'dispatch_config:edit'])" :icon="Setting" @click="dispatchConfigVisible = true">配置</el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="orderApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-autocomplete
          v-model="query.order_no"
          :fetch-suggestions="querySuggest('order_no')"
          placeholder="订单号"
          clearable
          value-key="value"
          style="width: 200px"
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
          style="width: 150px"
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
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.customer_phone"
          :fetch-suggestions="querySuggest('customer_phone')"
          placeholder="手机号"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.status" placeholder="订单状态" clearable style="width: 130px" @change="reload">
          <el-option v-for="(v, k) in ORDER_STATUS" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-select v-model="query.payment_status" placeholder="支付状态" clearable style="width: 130px" @change="reload">
          <el-option v-for="(v, k) in PAYMENT_STATUS" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
      </template>

      <el-table-column prop="order_no" label="订单号" min-width="190" />
      <el-table-column prop="customer_name" label="老板" width="110" />
      <el-table-column prop="provider_name" label="陪玩" width="130">
        <template #default="{ row }">
          <template v-if="row.providers && row.providers.length">
            <el-tag v-if="row.escort_mode === 'DOUBLE'" size="small" type="warning" effect="light" round style="margin-right: 4px">双陪</el-tag>
            <span>{{ row.providers.map((p: any) => p.provider_name).join('、') }}</span>
          </template>
          <span v-else>{{ row.provider_name || '-' }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="service_name_snapshot" label="服务" min-width="130" />
      <el-table-column prop="support_contact_name_snapshot" label="负责客服" width="110">
        <template #default="{ row }">{{ row.support_contact_name_snapshot || '-' }}</template>
      </el-table-column>
      <el-table-column label="金额" width="100">
        <template #default="{ row }">{{ amountToXaCoin(row.amount) }} 币</template>
      </el-table-column>
      <el-table-column label="支付" width="90">
        <template #default="{ row }">
          <el-tag :type="PAYMENT_STATUS[row.payment_status]?.type" effect="light" round>{{ row.payment_status_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="ORDER_STATUS[row.status]?.type" effect="light" round>{{ row.status_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="下单时间" width="160">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="320" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="onDetail(row)">详情</el-button>
          <el-button
            v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING' && row.payment_status === 'PAID'"
            link type="primary" @click="onDispatchMessage(row)"
          >一键发单</el-button>
          <el-button
            v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING'"
            link
            type="primary"
            @click="onAssign(row)"
          >派单</el-button>
          <el-button
            v-if="auth.hasPerm('order:dispatch') && row.status === 'GRABBED'"
            link
            type="primary"
            @click="onStart(row)"
          >开始</el-button>
          <el-button
            v-if="auth.hasPerm('order:settle') && row.status === 'IN_SERVICE'"
            link
            type="success"
            @click="onComplete(row)"
          >完成</el-button>
          <el-button
            v-if="auth.hasPerm('order:dispatch') && row.status === 'IN_SERVICE'"
            link
            type="primary"
            @click="onTransfer(row)"
          >转单</el-button>
          <el-button
            v-if="auth.hasPerm('order:cancel') && row.status === 'PENDING'"
            link
            type="warning"
            @click="onCancel(row)"
          >取消</el-button>
          <el-button
            v-if="auth.hasPerm('order:refund') && (['GRABBED', 'IN_SERVICE'].includes(row.status) || (row.status === 'COMPLETED' && row.settlement_mode === 'REPORT_APPROVAL' && !row.settled_at))"
            link
            type="danger"
            @click="onRefund(row)"
          >退款</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-drawer v-model="detailVisible" title="订单详情" size="520px" class="ap-panel">
    <template v-if="current">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="订单号">{{ current.order_no }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ current.status_display }}</el-descriptions-item>
        <el-descriptions-item label="支付状态">{{ current.payment_status_display }}</el-descriptions-item>
        <el-descriptions-item label="老板">{{ current.customer_name }}（{{ current.customer_phone_snapshot }}）</el-descriptions-item>
        <el-descriptions-item label="陪玩选择">{{ ({ ANY: '不限', MALE: '男陪', FEMALE: '女陪' } as any)[current.provider_gender || 'ANY'] }}</el-descriptions-item>
        <el-descriptions-item label="陪玩模式">{{ current.escort_mode_display }}</el-descriptions-item>
        <el-descriptions-item label="陪玩">{{ providerText(current) }}</el-descriptions-item>
        <el-descriptions-item label="服务">{{ current.service_name_snapshot }}</el-descriptions-item>
        <el-descriptions-item v-if="current.provider_requirement?.label" label="接单范围">{{ current.provider_requirement.label }}</el-descriptions-item>
        <el-descriptions-item v-if="current.gameplay_snapshot?.name" label="玩法规格">{{ current.gameplay_snapshot.name }} · {{ current.gameplay_snapshot.platform_name }} · {{ current.gameplay_snapshot.security_level_name }}</el-descriptions-item>
        <el-descriptions-item v-if="current.roulette_result?.name" label="转盘目标">{{ current.roulette_result.name }}：{{ current.roulette_result.description }}</el-descriptions-item>
        <el-descriptions-item v-if="current.gameplay_snapshot?.rules" label="规则快照">{{ current.gameplay_snapshot.rules }}</el-descriptions-item>
        <el-descriptions-item label="负责客服">{{ current.support_contact_name_snapshot || '-' }}</el-descriptions-item>
        <el-descriptions-item label="单价">{{ amountToXaCoin(current.unit_price_snapshot) }} 币</el-descriptions-item>
        <el-descriptions-item label="局数">{{ current.game_rounds }}</el-descriptions-item>
        <el-descriptions-item v-if="current.original_amount" label="原价">{{ amountToXaCoin(current.original_amount) }} 币</el-descriptions-item>
        <el-descriptions-item v-if="current.boss_discount" label="老板折扣">-{{ amountToXaCoin(current.boss_discount) }} 币</el-descriptions-item>
        <el-descriptions-item v-if="current.promo_discount" label="活动折扣">-{{ amountToXaCoin(current.promo_discount) }} 币</el-descriptions-item>
        <el-descriptions-item v-if="current.coupon_discount" label="优惠券折扣">-{{ amountToXaCoin(current.coupon_discount) }} 币</el-descriptions-item>
        <el-descriptions-item label="实付金额">{{ amountToXaCoin(current.amount) }} 币</el-descriptions-item>
        <el-descriptions-item v-if="current.promotion_title" label="命中活动">{{ current.promotion_title }}</el-descriptions-item>
        <template v-if="current.providers && current.providers.length">
          <el-descriptions-item
            v-for="(p, i) in current.providers"
            :key="p.id"
            :label="current.providers.length > 1 ? `打手${i + 1}` : '打手'"
          >
            {{ p.provider_name }} · {{ orderProviderCommissionDesc(p) }} · 实得 {{ amountToXaCoin(p.provider_income) }} 币
            <span v-if="p.settled_at" class="od-log-op"> · 已结算</span>
          </el-descriptions-item>
        </template>
        <template v-else>
          <el-descriptions-item label="平台抽成率">{{ current.commission_rate }}%</el-descriptions-item>
          <el-descriptions-item label="陪玩实得">{{ amountToXaCoin(current.provider_income) }} 币</el-descriptions-item>
        </template>
        <el-descriptions-item label="推荐人">{{ current.inviter_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="推荐人分佣">{{ amountToXaCoin(current.inviter_commission) }} 币</el-descriptions-item>
        <el-descriptions-item label="店铺留存">{{ amountToXaCoin(current.shop_income) }} 币</el-descriptions-item>
        <el-descriptions-item label="备注">{{ current.remark || '-' }}</el-descriptions-item>
        <el-descriptions-item label="取消原因">{{ current.cancel_reason || '-' }}</el-descriptions-item>
        <el-descriptions-item label="售后截止">{{ formatDateTime(current.aftersale_deadline) }}</el-descriptions-item>
        <el-descriptions-item label="实销确认时间">{{ current.consumption_confirmed_at ? formatDateTime(current.consumption_confirmed_at) : current.consumption_ready_at ? '预计 ' + formatDateTime(current.consumption_ready_at) : '等待全部有效报单' }}</el-descriptions-item>
        <el-descriptions-item label="下单时间">{{ formatDateTime(current.created_at) }}</el-descriptions-item>
      </el-descriptions>
      <div v-for="ticket in aftersales" :key="ticket.id" class="ap-section"><p>售后 #{{ticket.id}} · {{ticket.status === 'OPEN' ? '处理中' : '已处理'}}</p><p>{{ticket.reason}}</p><p v-if="ticket.resolution">处理说明：{{ticket.resolution}}</p><template v-if="ticket.status === 'OPEN'"><el-button v-if="auth.hasPerm('order:settle')" @click="processAfterSale(ticket,'resolve')">处理完成</el-button><el-button v-if="auth.hasPerm('order:refund')" type="danger" @click="processAfterSale(ticket,'refund')">退款结案</el-button></template></div>

      <el-divider>状态流水</el-divider>
      <el-timeline>
        <el-timeline-item
          v-for="log in logs"
          :key="log.id"
          :timestamp="formatDateTime(log.created_at)"
        >
          {{ log.action_display }}
          <span v-if="log.from_status">（{{ log.from_status }} → {{ log.to_status }}）</span>
          <span v-if="log.reason"> · {{ log.reason }}</span>
          <span class="od-log-op"> · {{ log.operator_name }}</span>
        </el-timeline-item>
      </el-timeline>
    </template>
  </el-drawer>

  <DispatchMessageDialog v-model="dispatchMessageVisible" :order-id="dispatchMessageOrderId" />
  <el-dialog v-model="trafficConfigVisible" title="订单峰谷限流与网络监控" width="960px" class="ap-modal" destroy-on-close :close-on-click-modal="false"><TrafficConfig /></el-dialog>
  <el-dialog v-model="dispatchConfigVisible" title="KOOK 派单配置" width="1100px" class="ap-modal" destroy-on-close :close-on-click-modal="false">
    <KookDispatchConfig />
  </el-dialog>
  <el-dialog v-model="dispatchVisible" title="客服快捷派单" width="800px" class="ap-modal">
    <el-form  class="ap-modal-form ap-form-grid" label-position="top">
      <div class="od-form-section">
        <p class="od-section-title">下单信息</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="下单老板">
              <el-select
                v-model="dispatchForm.customer_id"
                filterable
                remote
                reserve-keyword
                placeholder="输入账号/昵称搜索"
                :remote-method="searchBoss"
                :loading="bossLoading"
                style="width: 100%"
                @change="onBossChange"
              >
                <el-option
                  v-for="u in bossOptions"
                  :key="u.id"
                  :label="`${u.nickname || u.username}（余额 ${amountToXaCoin(u.wallet_balance)}币）`"
                  :value="u.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="服务">
              <el-select
                v-model="dispatchForm.service_id"
                filterable
                placeholder="选择服务"
                style="width: 100%"
                @change="onServiceChange"
              >
                <el-option
                  v-for="s in serviceOptions"
                  :key="s.id"
                  :label="`${s.name}（${amountToXaCoin(s.price)}币）`"
                  :value="s.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
      </div>

      <div class="od-form-section">
        <p class="od-section-title">派单设置</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="局数">
              <el-input-number v-model="dispatchForm.game_rounds" :min="1" :step="1" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="陪玩模式" class="ap-field--full">
              <el-radio-group v-model="dispatchForm.escort_mode" @change="onEscortModeChange">
                <el-radio-button label="SINGLE">单陪</el-radio-button>
                <el-radio-button label="DOUBLE">双陪</el-radio-button>
              </el-radio-group>
            </el-form-item>
          </el-col>
        </el-row>

<el-form-item label="陪玩选择"><el-select v-model="dispatchForm.provider_gender"><el-option label="不限" value="ANY"/><el-option label="男陪" value="MALE"/><el-option label="女陪" value="FEMALE"/></el-select></el-form-item>
        <div
          v-for="(slot, idx) in dispatchForm.providers"
          :key="idx"
          class="od-provider-row"
        >
          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item :label="`打手 ${idx + 1}`">
                <el-select
                  v-model="slot.provider_id"
                  filterable
                  remote
                  reserve-keyword
                  clearable
                  :placeholder="dispatchForm.escort_mode === 'SINGLE' ? '留空则进入待接单大厅' : '选择打手'"
                  :remote-method="searchProvider"
                  :loading="providerLoading"
                  style="width: 100%"
                  @change="onProviderPicked(idx)"
                >
                  <el-option
                    v-for="p in providerOptions"
                    :key="p.user"
                    :label="`${p.display_name || p.username}<${p.escort_no || '-'}>${(p.qualifications || []).map((q: any) => q.label).join(' / ') || '资格待配置'}`"
                    :value="p.user"
                  />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="5">
              <el-form-item label="抽成方式">
                <el-select v-model="slot.commission_type" :disabled="!!dispatchQuote?.commission_policy && dispatchQuote.commission_policy !== 'LEGACY'" style="width: 100%">
                  <el-option label="百分比" value="PERCENT" />
                  <el-option label="固定金额" value="FIXED" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="7">
              <el-form-item :label="slot.commission_type === 'FIXED' ? '固定抽成(兴安币)' : '抽成率(%)'">
                <el-input-number
                  v-if="slot.commission_type === 'PERCENT'"
                  v-model="slot.commission_rate"
                  :disabled="!!dispatchQuote?.commission_policy && dispatchQuote.commission_policy !== 'LEGACY'"
                  :min="0"
                  :max="100"
                  :step="1"
                  controls-position="right"
                  style="width: 100%"
                />
                <el-input-number
                  v-else
                  v-model="slot.commission_fixed"
                  :disabled="!!dispatchQuote?.commission_policy && dispatchQuote.commission_policy !== 'LEGACY'"
                  :min="0"
                  :precision="2"
                  :step="1"
                  controls-position="right"
                  style="width: 100%"
                />
              </el-form-item>
            </el-col>
          </el-row>
          <p v-if="slot.provider_id" class="od-provider-tip">
            实得 {{ amountToXaCoin(providerIncome(slot)) }} 币（{{ providerIncomeDesc(slot) }}）
            <template v-if="slot.source_label">
              <el-tag size="small" type="info" effect="plain" round style="margin-left: 8px">建议来源：{{ slot.source_label }}</el-tag>
              <el-button
                v-if="slot.commission_type === 'PERCENT' && typeof slot.suggested_rate === 'number' && slot.commission_rate !== slot.suggested_rate"
                link
                type="primary"
                size="small"
                @click="restoreSuggestedRate(idx)"
              >恢复建议 {{ slot.suggested_rate }}%</el-button>
            </template>
          </p>
        </div>

        <el-form-item label="备注" class="ap-field--full">
          <el-input v-model="dispatchForm.remark" type="textarea" :rows="2" placeholder="可选" />
        </el-form-item>
      </div>

      <div class="od-summary" :class="{ 'od-summary--danger': insufficient }">
        <div class="od-summary-item">
          <span class="od-summary-label">老板余额</span>
          <span class="od-summary-value">{{ currentBoss ? `${amountToXaCoin(currentBoss.wallet_balance)}币` : '—' }}</span>
        </div>
        <div class="od-summary-item">
          <span class="od-summary-label">折后预计扣款</span>
          <span class="od-summary-value">{{ quoteLoading ? '计算中…' : dispatchQuote ? `${amountToXaCoin(estimateAmount)}币` : '—' }}</span>
        </div>
        <div v-if="insufficient" class="od-summary-warn">余额不足以支付本次派单</div>
      </div>
    </el-form>
    <template #footer>
      <el-button @click="dispatchVisible = false">取消</el-button>
      <el-button type="primary" :loading="dispatching" @click="onDispatchSave">确定派单</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="assignVisible" title="指派待接单订单" width="480px" class="ap-modal">
    <el-form  class="ap-modal-form ap-form-grid" label-position="top">
      <el-form-item label="订单">
        <el-input :model-value="assignOrder ? `${assignOrder.order_no} · ${assignOrder.service_name_snapshot}` : ''" disabled />
      </el-form-item>
      <el-form-item label="陪玩">
        <el-select
          v-model="assignProviderId"
          filterable
          remote
          reserve-keyword
          placeholder="输入昵称或账号搜索可接单陪玩"
          :remote-method="searchProvider"
          :loading="providerLoading"
          style="width: 100%"
        >
          <el-option
            v-for="p in providerOptions"
            :key="p.user"
            :label="p.display_name || p.nickname || p.username"
            :value="p.user"
          />
        </el-select>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="assignVisible = false">取消</el-button>
      <el-button type="primary" :loading="assigning" @click="onAssignSave">确认派单</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="transferVisible" title="订单转单" width="880px" class="ap-modal">
    <template v-if="transferOrder">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="填写转出打手的应得份额与罚款，剩余份额转给新打手。新订单的双方收益均需报单审核后结算，罚款从当前可用余额扣除。"
        style="margin-bottom: 16px"
      />
      <div v-for="row in transferRows" :key="row.old_provider_id" class="od-provider-row">
        <el-checkbox v-model="row.enabled" style="margin-bottom: 8px">
          转出：{{ row.old_provider_name }}（应得 {{ amountToXaCoin(row.pool) }} 币）
        </el-checkbox>
        <el-row v-if="row.enabled" :gutter="12">
          <el-col :span="8">
            <el-form-item label="转入新打手" label-position="top">
              <el-select
                v-model="row.new_provider_id"
                filterable
                remote
                reserve-keyword
                placeholder="搜索可接单打手"
                :remote-method="searchProvider"
                :loading="providerLoading"
                style="width: 100%"
              >
                <el-option
                  v-for="p in providerOptions"
                  :key="p.user"
                  :label="p.display_name || p.nickname || p.username"
                  :value="p.user"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="5">
            <el-form-item label="分钱方式" label-position="top">
              <el-select v-model="row.split_type" style="width: 100%">
                <el-option label="按比例" value="PERCENT" />
                <el-option label="固定额" value="FIXED" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="5">
            <el-form-item :label="row.split_type === 'FIXED' ? '旧手分钱(币)' : '旧手比例(%)'" label-position="top">
              <el-input-number
                v-if="row.split_type === 'PERCENT'"
                v-model="row.split_percent"
                :min="0"
                :max="100"
                :step="1"
                controls-position="right"
                style="width: 100%"
              />
              <el-input-number
                v-else
                v-model="row.split_fixed_yuan"
                :min="0"
                :precision="2"
                :step="1"
                controls-position="right"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="罚款(币)" label-position="top">
              <el-input-number
                v-model="row.penalty_yuan"
                :min="0"
                :precision="2"
                :step="1"
                controls-position="right"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
        </el-row>
      </div>
      <el-form-item label="转单原因" class="ap-field--full">
        <el-input v-model="transferReason" type="textarea" :rows="2" placeholder="可选，将记入订单流水与罚款记录" />
      </el-form-item>
    </template>
    <template #footer>
      <el-button @click="transferVisible = false">取消</el-button>
      <el-button type="primary" :loading="transferring" @click="onTransferSave">确认转单</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, Setting } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import DispatchMessageDialog from '@/components/DispatchMessageDialog.vue'
import { orderApi, aftersaleApi, serviceItemApi, userApi, escortApi } from '@/api/modules'
import { request } from '@/api/request'
import { ORDER_STATUS, PAYMENT_STATUS } from '@/utils/dict'
import { amountToXaCoin, formatDateTime, xaCoinToAmount } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { adminWs } from '@/utils/ws'

const auth = useAuthStore()
const KookDispatchConfig = defineAsyncComponent(() => import('@/views/kook-dispatch/index.vue'))
const trafficConfigVisible = ref(false)
const TrafficConfig = defineAsyncComponent(() => import('./TrafficConfig.vue'))
const dispatchConfigVisible = ref(false)
const dispatchMessageVisible = ref(false)
const dispatchMessageOrderId = ref<number>()
function onDispatchMessage(order: any) {
  dispatchMessageOrderId.value = order.id
  dispatchMessageVisible.value = true
}
const tableRef = ref()
const query = reactive<Record<string, any>>({
  order_no: '',
  customer_username: '',
  customer_nickname: '',
  customer_phone: '',
  status: '',
  payment_status: '',
})

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询订单后，
 * 取返回记录对应字段值去重后作为下拉提示。
 */
function querySuggest(field: 'order_no' | 'customer_username' | 'customer_nickname' | 'customer_phone') {
  return async (keyword: string, cb: (items: any[]) => void) => {
    if (!keyword) {
      cb([])
      return
    }
    try {
      const res = (await orderApi.list({ [field]: keyword, page: 1, page_size: 10 })) as any
      const seen = new Set<string>()
      const items: { value: string }[] = []
      for (const o of res.data?.list || []) {
        const value = o[field] || ''
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
const logs = ref<any[]>([])
const aftersales = ref<any[]>([])
const assignVisible = ref(false)
const assigning = ref(false)
const assignOrder = ref<any>(null)
const assignProviderId = ref<number | undefined>()

async function onDetail(row: any) {
  current.value = (await orderApi.retrieve(row.id)).data
  aftersales.value = (await aftersaleApi.list({order:row.id})).data.list
  detailVisible.value = true
  const res = await request({ url: `/orders/${row.id}/logs/`, method: 'get' }) as any
  logs.value = res.data || []
}

async function processAfterSale(ticket: any, action: 'resolve' | 'refund') {
  const resolution = await askReason(action === 'refund' ? '售后退款结案' : '售后处理完成')
  if (!resolution || resolution.trim().length < 2) return
  await aftersaleApi.action(ticket.id, action, { resolution })
  ElMessage.success('售后已处理')
  await onDetail(current.value)
  tableRef.value?.load()
}

async function askReason(title: string): Promise<string | null> {
  try {
    const { value } = await ElMessageBox.prompt('请填写原因', title, {
      inputPlaceholder: '原因（取消可留空，退款必填）',
    })
    return value || ''
  } catch {
    return null
  }
}

async function onCancel(row: any) {
  const reason = await askReason('取消订单')
  if (reason === null) return
  await orderApi.action(row.id, 'cancel', { reason })
  ElMessage.success('已取消')
  tableRef.value.load()
}

async function onRefund(row: any) {
  const reason = await askReason('订单退款')
  if (reason === null) return
  if (!reason) {
    ElMessage.warning('退款必须填写原因')
    return
  }
  await orderApi.action(row.id, 'refund', { reason })
  ElMessage.success('已退款')
  tableRef.value.load()
}

function onAssign(row: any) {
  assignOrder.value = row
  assignProviderId.value = undefined
  providerOptions.value = []
  assignVisible.value = true
}

async function onAssignSave() {
  if (!assignOrder.value || !assignProviderId.value) {
    ElMessage.warning('请选择陪玩')
    return
  }
  assigning.value = true
  try {
    await orderApi.action(assignOrder.value.id, 'assign', { provider_id: assignProviderId.value })
    ElMessage.success('派单成功')
    assignVisible.value = false
    tableRef.value.load()
  } finally {
    assigning.value = false
  }
}

// ---------------- 转单 ----------------
const transferVisible = ref(false)
const transferring = ref(false)
const transferOrder = ref<any>(null)
const transferReason = ref('')
const transferRows = ref<any[]>([])

function onTransfer(row: any) {
  transferOrder.value = row
  transferReason.value = ''
  providerOptions.value = []
  // 未结算的打手行才可转出；无明细行时回落订单级主打手
  const rows = (row.providers && row.providers.length)
    ? row.providers.filter((p: any) => p.provider && !p.settled_at && !p.released_at)
    : (row.provider
      ? [{ provider: row.provider, provider_name: row.provider_name, provider_income: row.provider_income }]
      : [])
  transferRows.value = rows.map((p: any) => ({
    old_provider_id: p.provider,
    old_provider_name: p.provider_name || '打手',
    pool: p.provider_income || 0,
    enabled: rows.length === 1,
    new_provider_id: undefined,
    split_type: 'PERCENT',
    split_percent: 0,
    split_fixed_yuan: 0,
    penalty_yuan: 0,
  }))
  transferVisible.value = true
}

async function onTransferSave() {
  const picked = transferRows.value.filter((r) => r.enabled)
  if (!picked.length) { ElMessage.warning('请至少选择一名要转出的打手'); return }
  for (const r of picked) {
    if (!r.new_provider_id) { ElMessage.warning(`请为「${r.old_provider_name}」选择转入新打手`); return }
    if (r.new_provider_id === r.old_provider_id) { ElMessage.warning('新打手不能与原打手相同'); return }
  }
  const newIds = picked.map((r) => r.new_provider_id)
  if (new Set(newIds).size !== newIds.length) { ElMessage.warning('多个转入新打手不能重复'); return }

  const transfers = picked.map((r) => ({
    old_provider_id: r.old_provider_id,
    new_provider_id: r.new_provider_id,
    split_type: r.split_type,
    split_value: r.split_type === 'FIXED' ? xaCoinToAmount(r.split_fixed_yuan) : r.split_percent,
    penalty_amount: xaCoinToAmount(r.penalty_yuan),
  }))
  transferring.value = true
  try {
    await orderApi.action(transferOrder.value.id, 'transfer', { transfers, reason: transferReason.value })
    ElMessage.success('转单成功')
    transferVisible.value = false
    tableRef.value.load()
  } finally {
    transferring.value = false
  }
}

// ---------------- 客服快捷派单 ----------------
const dispatchVisible = ref(false)
const dispatching = ref(false)
const dispatchRequestId = ref('')
const bossLoading = ref(false)
const providerLoading = ref(false)
const bossOptions = ref<any[]>([])
const providerOptions = ref<any[]>([])
const serviceOptions = ref<any[]>([])
const currentBoss = ref<any>(null)
const currentService = ref<any>(null)
const dispatchForm = reactive<any>({
  customer_id: undefined,
  service_id: undefined,
  game_rounds: 1,
  provider_gender: 'ANY',
  escort_mode: 'SINGLE',
  providers: [{ provider_id: undefined, commission_type: 'PERCENT', commission_rate: 0, commission_fixed: 0 }],
  remark: '',
})

const dispatchQuote = ref<{ amount: number; original_amount: number; boss_discount: number; promo_discount: number; commission_policy?: string; commission_fixed_total?: number | null }>()
const quoteLoading = ref(false)
let quoteSequence = 0
const estimateAmount = computed(() => dispatchQuote.value?.amount || 0)
async function refreshDispatchQuote() {
  const sequence = ++quoteSequence
  dispatchQuote.value = undefined
  quoteLoading.value = false
  if (!dispatchVisible.value || !dispatchForm.customer_id || !dispatchForm.service_id) return
  quoteLoading.value = true
  try {
    const result = await request({ url: '/orders/dispatch-quote/', params: {
      customer_id: dispatchForm.customer_id, service_id: dispatchForm.service_id, game_rounds: dispatchForm.game_rounds,
    } })
    if (sequence === quoteSequence) {
      dispatchQuote.value = result.data
      if (dispatchQuote.value?.commission_policy && dispatchQuote.value.commission_policy !== 'LEGACY') {
        dispatchForm.providers.forEach((slot: any) => { slot.commission_type = 'PERCENT' })
      }
    }
  } catch {
    // 请求层显示失败原因，禁止使用过期报价继续派单。
  } finally {
    if (sequence === quoteSequence) quoteLoading.value = false
  }
}
watch(() => [dispatchVisible.value, dispatchForm.customer_id, dispatchForm.service_id, dispatchForm.game_rounds], refreshDispatchQuote)

function providerBase(slot: any): number {
  const count = dispatchForm.escort_mode === 'DOUBLE' ? 2 : 1
  const index = dispatchForm.providers.indexOf(slot)
  return Math.floor(estimateAmount.value / count) + (index < estimateAmount.value % count ? 1 : 0)
}

function providerIncome(slot: any): number {
  if (dispatchQuote.value?.commission_fixed_total != null) {
    const count = dispatchForm.escort_mode === 'DOUBLE' ? 2 : 1
    const total = dispatchQuote.value.commission_fixed_total
    const index = dispatchForm.providers.indexOf(slot)
    return providerBase(slot) - Math.floor(total / count) - (index < total % count ? 1 : 0)
  }
  if (slot.commission_type === 'FIXED') {
    const fixedRawAmount = Math.max(xaCoinToAmount(slot.commission_fixed), 0)
    return Math.max(providerBase(slot) - Math.min(fixedRawAmount, providerBase(slot)), 0)
  }
  const rate = Math.min(Math.max(Number(slot.commission_rate) || 0, 0), 100)
  return providerBase(slot) - Math.floor((providerBase(slot) * rate) / 100)
}

function providerIncomeDesc(slot: any): string {
  if (dispatchQuote.value?.commission_fixed_total != null) return '整单固定抽成3元，由参与打手均分承担'
  if (slot.commission_type === 'FIXED') {
    const fixedRawAmount = Math.max(xaCoinToAmount(slot.commission_fixed), 0)
    return `本人折后份额 ${amountToXaCoin(providerBase(slot))}币 - 固定抽成 ${amountToXaCoin(fixedRawAmount)}币`
  }
  return `本人折后份额 ${amountToXaCoin(providerBase(slot))}币 × ${100 - (slot.commission_rate || 0)}%`
}

function orderProviderCommissionDesc(provider: any): string {
  if (provider.commission_type === 'FIXED') {
    return `固定抽成 ${amountToXaCoin(provider.commission_fixed)}币`
  }
  return `抽成 ${provider.commission_rate}%`
}

const insufficient = computed(() => {
  if (!currentBoss.value || estimateAmount.value <= 0) return false
  return currentBoss.value.wallet_balance < estimateAmount.value
})

function onEscortModeChange(mode: string | number | boolean | undefined) {
  if (mode === 'DOUBLE') {
    while (dispatchForm.providers.length < 2) {
      dispatchForm.providers.push({ provider_id: undefined, commission_type: 'PERCENT', commission_rate: 0, commission_fixed: 0 })
    }
  } else {
    dispatchForm.providers.splice(1)
  }
}

// 选中打手后带出建议抽成率（客服可再手改）
async function onProviderPicked(idx: number) {
  const slot = dispatchForm.providers[idx]
  if (!slot.provider_id || !dispatchForm.service_id) return
  try {
    const res = await request({
      url: '/orders/suggest-commission/',
      method: 'get',
      params: { service_id: dispatchForm.service_id, provider_id: slot.provider_id },
    }) as any
    if (res.data && typeof res.data.commission_rate === 'number') {
      if (dispatchQuote.value?.commission_policy && dispatchQuote.value.commission_policy !== 'LEGACY') slot.commission_type = 'PERCENT'
      slot.commission_rate = res.data.commission_rate
      slot.suggested_rate = res.data.commission_rate
      slot.source_label = res.data.source_label || ''
    }
  } catch {
    // 建议抽成率获取失败不阻断，保持当前值
  }
}

// 将某打手抽成率恢复为系统建议值
function restoreSuggestedRate(idx: number) {
  const slot = dispatchForm.providers[idx]
  if (typeof slot.suggested_rate === 'number') {
    slot.commission_rate = slot.suggested_rate
  }
}

async function onDispatch() {
  dispatchRequestId.value = `dispatch-${Date.now()}-${Math.random().toString(36).slice(2)}`
  Object.assign(dispatchForm, {
    customer_id: undefined,
    service_id: undefined,
    game_rounds: 1,
    provider_gender: 'ANY',
  escort_mode: 'SINGLE',
    providers: [{ provider_id: undefined, commission_type: 'PERCENT', commission_rate: 0, commission_fixed: 0 }],
    remark: '',
  })
  currentBoss.value = null
  currentService.value = null
  bossOptions.value = []
  providerOptions.value = []
  dispatchVisible.value = true
  // 预加载服务列表
  const res = await serviceItemApi.allOptions({ is_active: 1 }) as any
  serviceOptions.value = res.data?.list || []
}

async function searchBoss(keyword: string) {
  if (!keyword) { bossOptions.value = []; return }
  bossLoading.value = true
  try {
    const res = await userApi.options({ keyword, page_size: 20 }) as any
    bossOptions.value = res.data?.list || []
  } finally {
    bossLoading.value = false
  }
}

function onBossChange(id: number) {
  currentBoss.value = bossOptions.value.find((u) => u.id === id) || null
}

async function onServiceChange(id: number) {
  currentService.value = serviceOptions.value.find((s) => s.id === id) || null
  await Promise.all(
    dispatchForm.providers
      .map((_: any, idx: number) => idx)
      .filter((idx: number) => dispatchForm.providers[idx].provider_id)
      .map((idx: number) => onProviderPicked(idx)),
  )
}

async function searchProvider(keyword: string) {
  if (!keyword) { providerOptions.value = []; return }
  providerLoading.value = true
  try {
    const res = await escortApi.options({ keyword, service_id: dispatchForm.service_id || undefined, page_size: 20 }) as any
    providerOptions.value = (res.data?.list || []).map((p: any) => ({...p,qualifications: (p.qualifications || []).filter((q: any) => !currentService.value?.provider_kind || (q.provider_kind === currentService.value.provider_kind && q.platform === currentService.value.platform && q.security_level === currentService.value.security_level && q.game_category === currentService.value.game_category_id))}))
  } finally {
    providerLoading.value = false
  }
}

async function onDispatchSave() {
  if (dispatching.value) return
  if (!dispatchForm.customer_id) { ElMessage.warning('请选择下单老板'); return }
  if (!dispatchForm.service_id) { ElMessage.warning('请选择服务'); return }
  if (!dispatchForm.game_rounds || dispatchForm.game_rounds < 1) {
    ElMessage.warning('局数至少为 1')
    return
  }
  const picked = dispatchForm.providers.filter((s: any) => s.provider_id)
  if (dispatchForm.escort_mode === 'DOUBLE') {
    if (picked.length !== 2) { ElMessage.warning('双陪需指定 2 名打手'); return }
    const ids = picked.map((s: any) => s.provider_id)
    if (new Set(ids).size !== ids.length) { ElMessage.warning('不可重复指派同一打手'); return }
  }
  if (quoteLoading.value || !dispatchQuote.value) { ElMessage.warning('请等待成交价计算完成后再派单'); return }
  if (insufficient.value) { ElMessage.warning('老板余额不足，无法派单'); return }
  const providersPayload = picked.map((s: any) => (
    s.commission_type === 'FIXED'
      ? {
          provider_id: s.provider_id,
          commission_type: 'FIXED',
          commission_fixed: xaCoinToAmount(s.commission_fixed),
        }
      : {
          provider_id: s.provider_id,
          commission_type: 'PERCENT',
          commission_rate: s.commission_rate,
        }
  ))
  dispatching.value = true
  try {
    const result = await orderApi.collectionAction('dispatch', {
      client_request_id: dispatchRequestId.value,
      customer_id: dispatchForm.customer_id,
      service_id: dispatchForm.service_id,
      game_rounds: dispatchForm.game_rounds,
      expected_amount: dispatchQuote.value.amount,
      escort_mode: dispatchForm.escort_mode,
      provider_gender: dispatchForm.provider_gender,
      providers: providersPayload,
      remark: dispatchForm.remark,
    })
    ElMessage.success('派单成功')
    dispatchVisible.value = false
    tableRef.value.load()
    if (result.data?.status === 'PENDING') onDispatchMessage(result.data)
  } catch (error: any) {
    if ((error?.response?.data || error)?.errorCode === 'PRICE_CHANGED') {
      await refreshDispatchQuote()
    }
  } finally {
    dispatching.value = false
  }
}

function providerText(order: any): string {
  if (order.providers && order.providers.length) {
    return order.providers.map((p: any) => p.provider_name).join('、')
  }
  return order.provider_name || '-'
}

async function onStart(row: any) {
  await orderApi.action(row.id, 'start')
  ElMessage.success('已开始服务')
  tableRef.value.load()
}

async function onComplete(row: any) {
  try {
    await ElMessageBox.confirm('确认服务已经完成？新订单需全体打手报单审核通过后结算。', '完成服务', {
      type: 'warning',
    })
  } catch {
    return
  }
  await orderApi.action(row.id, 'complete')
  ElMessage.success('订单已完成')
  tableRef.value.load()
}

let offOrderWs: (() => void) | null = null
let offConnected: (() => void) | null = null

onMounted(() => {
  adminWs.connect()
  offOrderWs = adminWs.on('order_status_update', () => {
    tableRef.value?.load()
  })
  offConnected = adminWs.on('connected', () => { tableRef.value?.load() })
})

onUnmounted(() => {
  offOrderWs?.()
  offConnected?.()
})
</script>

<style scoped lang="scss">
.od-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 让 CrudTable 内置的外层 padding 归零，融入 od-page 的间距节奏 */
.od-page :deep(.page-container) {
  padding: 0;
}

.tip {
  font-size: 12px;
  color: var(--muted-foreground);
}
.od-log-op {
  color: var(--muted-foreground);
}


.od-provider-row {
  padding: 12px 12px 0;
  margin-bottom: 12px;
  border-radius: 8px;
  background: var(--muted);
  border: 1px solid var(--border);
}
.od-provider-tip {
  margin: -4px 0 12px;
  padding-left: 2px;
  font-size: 12px;
  color: var(--muted-foreground);
}

.od-summary {
  display: flex;
  align-items: center;
  gap: 32px;
  margin-top: 8px;
  padding: 14px 16px;
  border-radius: 8px;
  background: var(--muted);
  border: 1px solid var(--border);
}
.od-summary--danger {
  background: color-mix(in srgb, var(--destructive) 8%, transparent);
  border-color: var(--destructive);
}
.od-summary-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.od-summary-label {
  font-size: 12px;
  color: var(--muted-foreground);
}
.od-summary-value {
  font-size: 18px;
  font-weight: 600;
  font-family: var(--font-mono);
  color: var(--foreground);
}
.od-summary-warn {
  margin-left: auto;
  font-size: 13px;
  font-weight: 600;
  color: var(--destructive);
}
</style>
