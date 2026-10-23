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

    <crud-table v-if="canReadOrders" ref="tableRef" :fetcher="orderApi.list" :query="query">
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
          <el-tag v-if="orderTeamSize(row) === 2" size="small" type="info" effect="light" round style="margin-right: 4px">双陪</el-tag>
          <template v-if="row.providers && row.providers.length">
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
            :disabled="orderTeamSize(row) !== 1"
            :title="orderTeamSize(row) === 2 ? '双陪订单不能使用单人指派' : orderTeamSize(row) === 1 ? '指派一名陪玩' : '订单人数尚未加载，请刷新'"
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
            :disabled="orderActionBusy"
            @click="onCancel(row)"
          >取消</el-button>
          <el-button
            v-if="auth.hasPerm('order:refund') && ['PAID', 'PARTIALLY_REFUNDED'].includes(row.payment_status) && ['GRABBED', 'IN_SERVICE', 'COMPLETED'].includes(row.status)"
            link
            type="danger"
            :disabled="orderActionBusy"
            @click="onRefund(row)"
          >退款</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-drawer v-model="detailVisible" title="订单详情" size="520px" class="ap-panel">
    <el-skeleton v-if="detailLoading" :rows="8" animated />
    <el-alert v-else-if="detailFailed" title="订单详情加载失败，请重试" type="error" :closable="false">
      <el-button text type="primary" @click="refreshCurrentOrder">重新加载</el-button>
    </el-alert>
    <template v-else-if="current">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="订单号">{{ current.order_no }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ current.status_display }}</el-descriptions-item>
        <el-descriptions-item label="支付状态">{{ current.payment_status_display }}</el-descriptions-item>
        <el-descriptions-item label="老板">{{ current.customer_name }}（{{ current.customer_phone_snapshot }}）</el-descriptions-item>
        <el-descriptions-item label="陪玩选择">{{ ({ ANY: '不限', MALE: '男陪', FEMALE: '女陪' } as any)[current.provider_gender || 'ANY'] }}</el-descriptions-item>
        <el-descriptions-item label="陪玩模式">{{ current.escort_mode_display }}</el-descriptions-item>
        <el-descriptions-item label="成交陪玩人数">{{ orderTeamSize(current) === 2 ? '2 人（双陪）' : orderTeamSize(current) === 1 ? '1 人（单陪）' : '等待服务端确认' }}</el-descriptions-item>
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
        <el-descriptions-item v-if="current.commission_type === 'FIXED'" label="整单固定抽成">{{ amountToXaCoin(current.commission_fixed) }} 币 · 整单仅扣一次，由参与陪玩分摊</el-descriptions-item>
        <template v-if="current.providers && current.providers.length">
          <el-descriptions-item
            v-for="(p, i) in current.providers"
            :key="p.id"
            :label="current.providers.length > 1 ? `打手${i + 1}` : '打手'"
          >
            {{ p.provider_name }} · {{ orderProviderCommissionDesc(p, current.commission_type === 'FIXED') }} · {{ ['REFUNDED', 'PARTIALLY_REFUNDED'].includes(current.payment_status) ? (p.settled_at || current.settled_at ? '原结算收益' : '原预计收益') : p.settled_at ? '实得' : '预计收益' }} {{ amountToXaCoin(p.provider_income) }} 币
            <span v-if="p.settled_at" class="od-log-op"> · 已结算</span>
          </el-descriptions-item>
        </template>
        <template v-else>
          <el-descriptions-item v-if="current.commission_type !== 'FIXED'" label="平台抽成率">{{ current.commission_rate }}%</el-descriptions-item>
          <el-descriptions-item :label="['REFUNDED', 'PARTIALLY_REFUNDED'].includes(current.payment_status) ? (current.settled_at ? '原结算陪玩收益' : '原预计陪玩收益') : current.settled_at ? '陪玩实得' : '预计陪玩收益'">{{ amountToXaCoin(current.provider_income) }} 币</el-descriptions-item>
        </template>
        <el-descriptions-item label="推荐人">{{ current.inviter_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="推荐人分佣">{{ amountToXaCoin(current.inviter_commission) }} 币</el-descriptions-item>
        <el-descriptions-item :label="['REFUNDED', 'PARTIALLY_REFUNDED'].includes(current.payment_status) ? (current.settled_at ? '原结算店铺留存' : '原预计店铺留存') : current.settled_at ? '店铺留存' : '预计店铺留存'">{{ amountToXaCoin(current.shop_income) }} 币</el-descriptions-item>
        <el-descriptions-item v-if="current.settled_at" label="原结算时间">{{ formatDateTime(current.settled_at) }}</el-descriptions-item>
        <el-descriptions-item label="备注">{{ current.remark || '-' }}</el-descriptions-item>
        <el-descriptions-item label="取消原因">{{ current.cancel_reason || '-' }}</el-descriptions-item>
        <el-descriptions-item label="售后截止">{{ formatDateTime(current.aftersale_deadline) }}</el-descriptions-item>
        <el-descriptions-item label="实销确认时间">{{ current.consumption_confirmed_at ? formatDateTime(current.consumption_confirmed_at) : current.consumption_ready_at ? '预计 ' + formatDateTime(current.consumption_ready_at) : '等待全部有效报单' }}</el-descriptions-item>
        <el-descriptions-item label="下单时间">{{ formatDateTime(current.created_at) }}</el-descriptions-item>
      </el-descriptions>
      <el-alert v-if="current.payment_status === 'REFUNDED' && !current.settled_at" title="订单已退款，不再结算以上预计收益" type="info" :closable="false" show-icon style="margin-top: 12px" />
      <section v-if="current.refund_summary" class="od-refund-section" aria-label="实际退款与追回结果">
        <h3>实际退款与追回</h3>
        <el-descriptions :column="1" border>
          <el-descriptions-item v-if="current.refund_summary.original_amount !== undefined" label="原实付">{{ amountToXaCoin(current.refund_summary.original_amount) }} 币</el-descriptions-item>
          <el-descriptions-item v-if="current.refund_summary.remaining_amount !== undefined" label="剩余可退">{{ amountToXaCoin(current.refund_summary.remaining_amount) }} 币</el-descriptions-item>
          <el-descriptions-item v-if="current.refund_summary.refund_count !== undefined" label="退款笔数">{{ current.refund_summary.refund_count }} 笔</el-descriptions-item>
          <el-descriptions-item label="累计退回老板">{{ amountToXaCoin(current.refund_summary.amount) }} 币</el-descriptions-item>
          <el-descriptions-item v-if="current.refund_summary.recovered_amount !== undefined" label="累计追回收益">{{ amountToXaCoin(current.refund_summary.recovered_amount) }} 币</el-descriptions-item>
          <el-descriptions-item v-if="!current.refund_summary.records" label="退款时间">{{ formatDateTime(current.refund_summary.created_at) }}</el-descriptions-item>
        </el-descriptions>
        <article v-for="record in (current.refund_summary.records || [current.refund_summary])" :key="record.id" class="od-refund-record">
          <h4>退款 #{{ record.id }} · {{ amountToXaCoin(record.amount) }} 币</h4>
          <p class="od-refund-note">{{ formatDateTime(record.created_at) }} · {{ record.reason || '—' }}</p>
          <p v-if="record.recovered_amount !== undefined" class="od-refund-note">本笔实际追回收益：{{ amountToXaCoin(record.recovered_amount) }} 币</p>
          <p v-if="record.recovery_lines?.length" class="od-refund-note">原结算金额保留为历史记录，以下为该笔退款产生的实际冲正流水。</p>
          <p v-else-if="record.recovery_lines" class="od-refund-note">本笔未产生收益冲正流水。</p>
          <div v-for="line in record.recovery_lines" :key="line.reversal_transaction_id" class="od-refund-line">
            <strong>{{ refundSourceLabel(line.source) }} · {{ TX_TYPE[line.tx_type]?.label || line.tx_type }} {{ amountToXaCoin(line.amount) }} 币</strong>
            <span>钱包 #{{ line.wallet_id }} · {{ line.account_id ? `账号 #${line.account_id}` : '平台账户' }}</span>
            <span>原流水 #{{ line.original_transaction_id }} → 冲正流水 #{{ line.reversal_transaction_id }}</span>
          </div>
          <section v-if="record.benefit_effects" class="od-refund-line" aria-label="消费与权益处理结果">
            <strong>消费与权益处理</strong>
            <span v-if="record.benefit_effects.confirmation_reversed">该单实销确认已冲回，原记录保留备查。</span>
            <span v-if="record.benefit_effects.old_business_cycle">该单属于历史业务周期，当前周期权益不变。</span>
            <span v-if="record.benefit_effects.net_consumption !== undefined">当前周期净实销：{{ amountToXaCoin(record.benefit_effects.net_consumption) }} 币</span>
            <span v-if="record.benefit_effects.previous_boss_type_id !== undefined">会员档位：{{ record.benefit_effects.previous_boss_type_id ? `#${record.benefit_effects.previous_boss_type_id}` : '无' }} → {{ record.benefit_effects.boss_type_id ? `#${record.benefit_effects.boss_type_id}` : '无' }}</span>
            <span v-if="record.benefit_effects.expired_coupon_ids?.length">撤销未用优惠券：#{{ record.benefit_effects.expired_coupon_ids.join('、#') }}</span>
            <span v-if="record.benefit_effects.cancelled_claim_ids?.length">取消未交付权益：#{{ record.benefit_effects.cancelled_claim_ids.join('、#') }}</span>
            <span v-if="record.benefit_effects.deactivated_tag_ids?.length">停用身份标签：#{{ record.benefit_effects.deactivated_tag_ids.join('、#') }}</span>
            <span v-if="record.benefit_effects.consumed_coupon_ids?.length">保留已用优惠券历史：#{{ record.benefit_effects.consumed_coupon_ids.join('、#') }}</span>
            <span v-if="record.benefit_effects.delivered_claim_ids?.length">保留已交付权益历史：#{{ record.benefit_effects.delivered_claim_ids.join('、#') }}</span>
            <span v-if="record.benefit_effects.consumed_benefits_policy">{{ record.benefit_effects.consumed_benefits_policy }}</span>
          </section>
        </article>
      </section>
      <div v-for="ticket in aftersales" :key="ticket.id" class="ap-section"><p>售后 #{{ticket.id}} · {{ticket.status === 'OPEN' ? '处理中' : ticket.status === 'REFUNDED' ? '已退款' : ticket.status === 'PARTIALLY_REFUNDED' ? '部分退款' : '处理完成'}}</p><p>{{ticket.reason}}</p><p v-if="ticket.resolution">处理说明：{{ticket.resolution}}</p><template v-if="ticket.status === 'OPEN'"><el-button v-if="auth.hasPerm('order:settle')" :disabled="orderActionBusy" @click="processAfterSale(ticket,'resolve')">处理完成</el-button><el-button v-if="auth.hasPerm('order:refund')" type="danger" :disabled="orderActionBusy" @click="processAfterSale(ticket,'refund')">退款结案</el-button></template></div>

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

  <el-dialog v-model="refundVisible" :title="refundTarget?.ticketId ? '售后退款 · 资金复核' : '订单退款 · 资金复核'" width="720px" class="ap-modal" :close-on-click-modal="false" :close-on-press-escape="!refundSubmitting" :show-close="!refundSubmitting">
    <el-descriptions v-if="refundLimits" :column="3" border class="od-refund-summary">
      <el-descriptions-item label="原实付">{{ amountToXaCoin(refundLimits.original) }} 币</el-descriptions-item>
      <el-descriptions-item label="累计已退">{{ amountToXaCoin(refundLimits.refunded) }} 币</el-descriptions-item>
      <el-descriptions-item label="剩余可退">{{ amountToXaCoin(refundLimits.remaining) }} 币</el-descriptions-item>
    </el-descriptions>
    <el-form label-position="top" class="od-refund-reason">
      <el-form-item label="本次退款金额（兴安币，最多 1 位小数）">
        <el-input v-model="refundAmountCoins" inputmode="decimal" :disabled="refundSubmitting || !refundLimits" placeholder="请输入本次退款金额">
          <template #append>币</template>
        </el-input>
        <el-button text type="primary" :disabled="refundSubmitting || !refundLimits" @click="useRefundRemainder">填入全部余款</el-button>
      </el-form-item>
    </el-form>
    <el-skeleton v-if="refundLoading" :rows="5" animated />
    <template v-else>
      <el-alert v-if="refundError" :title="refundError" type="warning" :closable="false" show-icon />
      <template v-if="refundPreview">
        <el-descriptions :column="2" border class="od-refund-summary">
          <el-descriptions-item label="订单">#{{ refundPreview.order_id }}</el-descriptions-item>
          <el-descriptions-item label="结算状态">{{ refundPreview.is_settled ? '已结算，须追回收益' : '尚未结算' }}</el-descriptions-item>
          <el-descriptions-item label="本次退回老板">{{ amountToXaCoin(refundPreview.refund_amount) }} 币</el-descriptions-item>
          <el-descriptions-item label="本次需追回收益">{{ amountToXaCoin(refundPreview.recovery_total) }} 币</el-descriptions-item>
          <el-descriptions-item label="本次退款后余款">{{ amountToXaCoin(refundPreview.remaining_after_refund) }} 币</el-descriptions-item>
          <el-descriptions-item label="退款范围">{{ refundPreview.full_refund ? '退还全部剩余款项' : '部分退款，订单保留' }}</el-descriptions-item>
        </el-descriptions>
        <p class="od-refund-note">金额及来源由服务端试算。确认时会重新核对；资金变化后须重新复核。</p>
        <p class="od-refund-note">本次按服务端比例方案追回收益，并按规则调整积分、实销及相应权益。已用优惠券和已完成交付保留历史，处理结果可在订单详情核对。</p>
        <section v-for="recovery in refundPreview.recoveries" :key="recovery.wallet_id" class="od-refund-line">
          <strong>{{ refundRoleLabel(recovery.role) }} · 钱包 #{{ recovery.wallet_id }}{{ recovery.account_id ? ` · 账号 #${recovery.account_id}` : '' }}</strong>
          <dl class="od-refund-amounts">
            <div><dt>本次追回收益</dt><dd>{{ amountToXaCoin(recovery.income_amount) }} 币</dd></div>
            <div><dt>本次从冻结追回</dt><dd>{{ amountToXaCoin(recovery.held_amount) }} 币</dd></div>
            <div><dt>本次从押金追回</dt><dd>{{ amountToXaCoin(recovery.deposit_amount) }} 币</dd></div>
            <div><dt>需扣可用余额</dt><dd>{{ amountToXaCoin(recovery.available_required) }} 币</dd></div>
            <div><dt>可供本次追回</dt><dd>{{ amountToXaCoin(recovery.available_balance) }} 币</dd></div>
            <div v-if="recovery.protected_deposit_amount !== undefined"><dt>平台押金保护额</dt><dd>{{ amountToXaCoin(recovery.protected_deposit_amount) }} 币</dd></div>
            <div><dt>不足金额</dt><dd>{{ amountToXaCoin(recovery.shortfall) }} 币</dd></div>
          </dl>
          <span>原收益流水：{{ recovery.original_transaction_ids.map(id => `#${id}`).join('、') || '无' }}</span>
          <span v-if="recovery.explanation">{{ recovery.explanation }}</span>
        </section>
        <el-alert v-if="!refundPreview.eligible" title="当前不满足退款条件，无法确认退款" type="error" :closable="false" show-icon />
        <ul v-if="refundPreview.shortfalls.length" class="od-refund-shortfalls" aria-label="资金不足明细">
          <li v-for="item in refundPreview.shortfalls" :key="item.wallet_id">钱包 #{{ item.wallet_id }}{{ item.account_id ? `（账号 #${item.account_id}）` : '（平台）' }}：需 {{ amountToXaCoin(item.required) }} 币，可追回 {{ amountToXaCoin(item.available) }} 币，不足 {{ amountToXaCoin(item.shortfall) }} 币</li>
        </ul>
      </template>
      <el-form label-position="top" class="od-refund-reason">
        <el-form-item label="退款原因 / 处理说明（至少 2 个字）">
          <el-input v-model="refundReason" type="textarea" :rows="3" :maxlength="refundTarget?.ticketId ? 1000 : 255" show-word-limit :disabled="refundSubmitting" />
        </el-form-item>
      </el-form>
    </template>
    <template #footer>
      <el-button :disabled="refundSubmitting" @click="refundVisible = false">取消</el-button>
      <el-button :loading="refundLoading" :disabled="refundSubmitting" @click="loadRefundPreview">刷新试算</el-button>
      <el-button type="danger" :loading="refundSubmitting" :disabled="!refundCanSubmit" @click="submitRefund">{{ refundPreview?.full_refund ? (refundPreview.refunded_amount ? '确认退还余款' : '确认全额退款') : '确认本次退款' }}</el-button>
    </template>
  </el-dialog>

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
                  :label="serviceOptionLabel(s)"
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
            <el-form-item label="套餐陪玩人数" class="ap-field--full">
              <el-tag effect="light">{{ dispatchProviderCount === 2 ? '双陪 · 2 人' : dispatchProviderCount === 1 ? '单陪 · 1 人' : '请先选择服务套餐' }}</el-tag>
              <span class="hint">{{ dispatchProviderCount === 2 ? '报价按每人价格 × 2；须指定两名不同陪玩。' : dispatchProviderCount === 1 ? '单陪套餐仅支持一名陪玩，留空则进入待接单大厅。' : '陪玩人数由所选套餐确定，不能单独修改。' }}</span>
            </el-form-item>
          </el-col>
        </el-row>

<el-form-item v-if="currentService?.service_type === 'COMPANION'" label="陪玩选择"><el-select v-model="dispatchForm.provider_gender"><el-option label="不限" value="ANY"/><el-option label="男陪" value="MALE"/><el-option label="女陪" value="FEMALE"/></el-select></el-form-item>
        <el-alert v-if="currentService?.is_gift" type="info" :closable="false" title="礼物沿用独立比例规则，不使用订单自定义抽成。" />
        <el-form-item v-else label="本单抽成方式" class="ap-field--full">
          <el-radio-group v-model="dispatchCommissionMode" :disabled="dispatching">
            <el-radio-button value="AUTO">沿用配置</el-radio-button>
            <el-radio-button value="PERCENT">百分比</el-radio-button>
            <el-radio-button value="FIXED">整单固定金额</el-radio-button>
          </el-radio-group>
          <p class="hint">{{ dispatchQuote?.commission_locked ? '当前沿用自动规则；选择百分比或固定金额可为本单明确覆盖抽成，不改变接单资格和价格。' : '百分比保留各陪玩的原有建议；固定金额按整单设置。' }}</p>
        </el-form-item>
        <el-form-item v-if="dispatchCommissionMode === 'FIXED' && !currentService?.is_gift" label="整单固定抽成（兴安币）" class="ap-field--full" :error="dispatchCommissionError">
          <el-input-number v-model="dispatchCommissionFixed" :disabled="dispatching" :min="0" :max="214748364.7" :precision="1" :step="0.1" controls-position="right" />
          <span class="hint">整笔订单仅扣一次，双陪共同分摊；不随局数增加，0 表示不抽成。</span>
        </el-form-item>
        <p v-if="dispatchQuote" class="od-provider-tip">{{ dispatchQuote.commission_source_label || '系统试算' }}<template v-if="dispatchFixed"> · 整单固定抽成 {{ amountToXaCoin(dispatchQuote.commission_fixed_total) }} 币（已按折后实付封顶）</template></p>
        <div
          v-for="(slot, idx) in dispatchForm.providers"
          :key="idx"
          class="od-provider-row"
        >
          <el-row :gutter="16">
            <el-col :span="dispatchFixed ? 24 : 16">
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
            <el-col v-if="!dispatchFixed" :span="8">
              <el-form-item :label="dispatchPendingPercentage() ? '待接单陪玩抽成比例（%）' : '抽成比例（%）'">
                <el-input-number
                  v-if="!dispatchPendingPercentage()"
                  v-model="slot.commission_rate"
                  :disabled="!slot.provider_id || dispatchQuote?.commission_locked || quoteLoading || slot.suggestion_loading || dispatching"
                  :min="0"
                  :max="100"
                  :precision="0"
                  :step="1"
                  controls-position="right"
                  style="width: 100%"
                />
                <el-input-number v-else v-model="dispatchPendingRate" :disabled="quoteLoading || dispatching" :min="0" :max="100" :precision="0" :step="1" controls-position="right" style="width: 100%" />
              </el-form-item>
            </el-col>
          </el-row>
          <p v-if="slot.provider_id" class="od-provider-tip">
            <template v-if="dispatchQuote && (!dispatchFixed || dispatchProviderShare(slot))">预计实得 {{ amountToXaCoin(providerIncome(slot)) }} 币（{{ providerIncomeDesc(slot) }}）</template>
            <template v-else>{{ quoteLoading ? '正在试算个人分摊…' : '个人分摊与实得以服务端试算为准' }}</template>
            <template v-if="slot.source_label">
              <el-tag size="small" type="info" effect="plain" round style="margin-left: 8px">建议来源：{{ slot.source_label }}</el-tag>
              <el-button
                v-if="!dispatchFixed && !dispatchQuote?.commission_locked && typeof slot.suggested_rate === 'number' && slot.commission_rate !== slot.suggested_rate"
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
import { ORDER_STATUS, PAYMENT_STATUS, TX_TYPE } from '@/utils/dict'
import { amountToXaCoin, formatDateTime, xaCoinToAmount } from '@/utils/format'
import { commissionError } from '@/utils/commission'
import { useAuthStore } from '@/stores/auth'
import { adminWs } from '@/utils/ws'

const auth = useAuthStore()
const canReadOrders = computed(() => Boolean(auth.token) && auth.hasPerm('order:view'))
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
const detailLoading = ref(false)
const detailFailed = ref(false)
const detailOrderId = ref<number | string | null>(null)
const orderActionBusy = ref(false)
type RefundRecovery = { wallet_id: number; account_id: number | null; role: string; income_amount: number; held_amount: number; deposit_amount: number; available_required: number; available_balance: number; protected_deposit_amount?: number; shortfall: number; original_transaction_ids: number[]; source_safe?: boolean; explanation?: string }
type RefundPreview = { order_id: number; is_settled: boolean; eligible: boolean; original_amount: number; refunded_amount: number; remaining_amount: number; refund_amount: number; remaining_after_refund: number; full_refund: boolean; recovery_total: number; recoveries: RefundRecovery[]; shortfalls: { wallet_id: number; account_id: number | null; required: number; available: number; shortfall: number }[]; plan_token: string }
const refundVisible = ref(false)
const refundLoading = ref(false)
const refundSubmitting = ref(false)
const refundTarget = ref<{ orderId: number | string; ticketId?: number } | null>(null)
const refundPreview = ref<RefundPreview | null>(null)
const refundReason = ref('')
const refundError = ref('')
const refundAmountCoins = ref('')
const refundLimits = ref<{ original: number; refunded: number; remaining: number } | null>(null)
const refundRawAmount = computed(() => {
  if (!/^\d+(?:\.\d)?$/.test(refundAmountCoins.value.trim())) return null
  const raw = xaCoinToAmount(refundAmountCoins.value)
  return Number.isSafeInteger(raw) && raw > 0 ? raw : null
})
const refundIntentIds = new Map<string, string>()
let settingRefundAmount = false
let refundPreviewSequence = 0
let refundOperation: ReturnType<typeof beginOrderAction> = null
const refundCanSubmit = computed(() => Boolean(refundVisible.value && refundOperation?.current() && !refundLoading.value && !refundSubmitting.value
  && refundPreview.value?.eligible && !refundPreview.value.shortfalls.length
  && refundPreview.value.plan_token && refundRawAmount.value === refundPreview.value.refund_amount
  && refundReason.value.trim().length >= 2))
let detailSequence = 0
let pageDisposed = false
let pageMounted = false
let realtimeGeneration = 0
let refreshTimer: ReturnType<typeof setTimeout> | null = null
let refreshRunning = false
let refreshPending = false
let detailRefreshPending = false
let offOrderWs: (() => void) | null = null
let offConnected: (() => void) | null = null
let actionSequence = 0
let pendingActionOrderId: number | string | null = null
const orderVersions = new Map<number | string, number>()
const assignVisible = ref(false)
const assigning = ref(false)
const assignOrder = ref<any>(null)
const assignProviderId = ref<number | undefined>()

async function onDetail(row: any) {
  if (pageDisposed || !canReadOrders.value) return
  clearOrderDetail()
  current.value = row
  detailOrderId.value = row.id
  detailVisible.value = true
  await refreshCurrentOrder()
}

function clearOrderDetail() {
  if (refundTarget.value?.ticketId) invalidateOrderAction()
  ++detailSequence
  detailRefreshPending = false
  detailOrderId.value = null
  current.value = null
  aftersales.value = []
  logs.value = []
  detailLoading.value = detailFailed.value = false
}

watch(detailVisible, visible => { if (!visible) clearOrderDetail() }, { flush: 'sync' })

function isCurrentOrderDetail(sequence: number, revision: number, orderId: number | string) {
  return !pageDisposed && canReadOrders.value && revision === auth.sessionRevision
    && sequence === detailSequence && detailVisible.value && detailOrderId.value === orderId
}

async function refreshCurrentOrder() {
  const orderId = detailOrderId.value
  if (pageDisposed || !canReadOrders.value || !detailVisible.value || !orderId) return
  const sequence = ++detailSequence
  const revision = auth.sessionRevision
  detailLoading.value = true
  detailFailed.value = false
  try {
    const [detail, tickets, history] = await Promise.all([
      orderApi.retrieve(orderId),
      aftersaleApi.list({ order: orderId }),
      request({ url: `/orders/${orderId}/logs/`, method: 'get' }),
    ])
    if (!isCurrentOrderDetail(sequence, revision, orderId)) return
    current.value = detail.data
    aftersales.value = tickets.data.list
    logs.value = history.data || []
  } catch {
    if (isCurrentOrderDetail(sequence, revision, orderId)) detailFailed.value = true
  } finally {
    if (isCurrentOrderDetail(sequence, revision, orderId)) detailLoading.value = false
  }
}

// Keep the table's filters/page; reconcile the drawer through its own HTTP reads.
function requestOrderRefresh(orderId?: number | string) {
  if (pageDisposed || !canReadOrders.value) return
  refreshPending = true
  if (detailVisible.value && detailOrderId.value && (orderId == null || orderId === detailOrderId.value)) {
    ++detailSequence
    detailRefreshPending = true
  }
  scheduleOrderRefresh()
}

function scheduleOrderRefresh() {
  if (pageDisposed || !canReadOrders.value || !refreshPending || refreshRunning || refreshTimer !== null) return
  const generation = realtimeGeneration
  refreshTimer = setTimeout(async () => {
    refreshTimer = null
    if (generation !== realtimeGeneration || pageDisposed || !canReadOrders.value) return
    refreshPending = false
    refreshRunning = true
    const refreshDetail = detailRefreshPending
    detailRefreshPending = false
    try {
      await Promise.allSettled([tableRef.value?.load(), ...(refreshDetail ? [refreshCurrentOrder()] : [])])
    } finally {
      if (generation === realtimeGeneration) { refreshRunning = false; scheduleOrderRefresh() }
    }
  }, 300)
}

function invalidateOrderAction() {
  ++actionSequence
  orderActionBusy.value = false
  if (pendingActionOrderId !== null) ElMessageBox.close()
  pendingActionOrderId = null
  clearRefundReview()
}

function beginOrderAction(orderId: number | string, permission: string) {
  if (pageDisposed || !canReadOrders.value || !auth.hasPerm(permission) || orderActionBusy.value) return null
  const sequence = ++actionSequence
  const revision = auth.sessionRevision
  const version = orderVersions.get(orderId) || 0
  orderActionBusy.value = true
  pendingActionOrderId = orderId
  return {
    current: (allowVersionChange = false) => !pageDisposed && canReadOrders.value && auth.hasPerm(permission)
      && revision === auth.sessionRevision && sequence === actionSequence && (allowVersionChange || version === (orderVersions.get(orderId) || 0)),
    finish: () => { if (sequence === actionSequence) { orderActionBusy.value = false; pendingActionOrderId = null } },
  }
}

function startOrderRealtime() {
  if (pageDisposed || !canReadOrders.value) return
  offOrderWs = adminWs.on('order_status_update', data => {
    if (!Number.isSafeInteger(data?.id) || data.id <= 0) return
    orderVersions.set(data.id, (orderVersions.get(data.id) || 0) + 1)
    if (pendingActionOrderId === data.id) invalidateOrderAction()
    requestOrderRefresh(data.id)
  })
  offConnected = adminWs.on('connected', () => { invalidateOrderAction(); requestOrderRefresh() })
  adminWs.connect()
}

function stopOrderRealtime() {
  ++realtimeGeneration
  offOrderWs?.()
  offConnected?.()
  offOrderWs = offConnected = null
  if (refreshTimer !== null) clearTimeout(refreshTimer)
  refreshTimer = null
  refreshPending = detailRefreshPending = refreshRunning = false
  orderVersions.clear()
  invalidateOrderAction()
}

async function processAfterSale(ticket: any, action: 'resolve' | 'refund') {
  const orderId = detailOrderId.value
  if (!orderId || !detailVisible.value || detailLoading.value || detailFailed.value || ticket.status !== 'OPEN') return
  if (action === 'refund') { await openRefundReview(orderId, ticket.id); return }
  const operation = beginOrderAction(orderId, 'order:settle')
  if (!operation) return
  try {
    const resolution = await askReason('售后处理完成')
    if (!resolution || resolution.trim().length < 2 || !operation.current()
      || detailOrderId.value !== orderId || !detailVisible.value) return
    pendingActionOrderId = null
    await aftersaleApi.action(ticket.id, action, { resolution })
    if (!operation.current()) return
    ElMessage.success('售后已处理')
    requestOrderRefresh(orderId)
  } finally { operation.finish() }
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
  const operation = beginOrderAction(row.id, 'order:cancel')
  if (!operation) return
  try {
    const reason = await askReason('取消订单')
    if (reason === null || !operation.current()) return
    pendingActionOrderId = null
    await orderApi.action(row.id, 'cancel', { reason })
    if (!operation.current()) return
    ElMessage.success('已取消')
    requestOrderRefresh(row.id)
  } finally { operation.finish() }
}

async function onRefund(row: any) {
  await openRefundReview(row.id)
}

function refundRoleLabel(role: string) {
  return ({ PROVIDER: '陪玩', INVITER: '推荐人', PLATFORM: '平台', SHOP: '平台' } as Record<string, string>)[role] || role
}

function refundSourceLabel(source: string) {
  return ({ INCOME: '原收益冲正', COMMISSION: '平台佣金冲正', HOLD: '该单冻结收益冲正', DEPOSIT: '该单抵扣押金冲正' } as Record<string, string>)[source] || source
}

function clearRefundReview() {
  ++refundPreviewSequence
  refundOperation?.finish()
  refundOperation = null
  refundVisible.value = refundLoading.value = refundSubmitting.value = false
  refundTarget.value = refundPreview.value = null
  refundReason.value = refundError.value = ''
  refundAmountCoins.value = ''
  refundLimits.value = null
  refundIntentIds.clear()
}

watch(refundVisible, visible => { if (!visible) clearRefundReview() }, { flush: 'sync' })
watch(refundAmountCoins, () => {
  if (settingRefundAmount || !refundVisible.value) return
  ++refundPreviewSequence
  refundLoading.value = false
  refundPreview.value = null
  refundError.value = '退款金额已修改，请重新试算后确认'
}, { flush: 'sync' })

async function openRefundReview(orderId: number | string, ticketId?: number) {
  const operation = beginOrderAction(orderId, 'order:refund')
  if (!operation) return
  refundOperation = operation
  refundTarget.value = { orderId, ticketId }
  refundReason.value = refundError.value = ''
  refundVisible.value = true
  await loadRefundPreview()
}

function applyRefundPreview(preview: RefundPreview, initializeAmount = false) {
  refundLimits.value = { original: preview.original_amount, refunded: preview.refunded_amount, remaining: preview.remaining_amount }
  if (initializeAmount) {
    settingRefundAmount = true
    refundAmountCoins.value = amountToXaCoin(preview.refund_amount)
    settingRefundAmount = false
  }
  refundPreview.value = refundRawAmount.value === preview.refund_amount ? preview : null
}

function useRefundRemainder() {
  if (!refundLimits.value || refundSubmitting.value) return
  refundAmountCoins.value = amountToXaCoin(refundLimits.value.remaining)
}

function refundIntentId(amount: number, reason: string) {
  const key = JSON.stringify([amount, reason])
  let id = refundIntentIds.get(key)
  if (!id) { id = crypto.randomUUID(); refundIntentIds.set(key, id) }
  return id
}

async function refreshRefundLimits(orderId: number | string, operation: NonNullable<ReturnType<typeof beginOrderAction>>, sequence: number) {
  const result = await request<RefundPreview>({ url: `/orders/${orderId}/refund-preview/`, method: 'get' })
  if (!operation.current() || !refundVisible.value || sequence !== refundPreviewSequence) return
  if (String(result.data.order_id) === String(orderId)) applyRefundPreview(result.data)
}

async function loadRefundPreview() {
  const target = refundTarget.value
  const operation = refundOperation
  if (!target || !operation?.current() || refundSubmitting.value) return
  const raw = refundRawAmount.value
  const initializeAmount = !refundAmountCoins.value && !refundLimits.value
  if (!initializeAmount && (raw === null || (refundLimits.value && raw > refundLimits.value.remaining))) {
    refundPreview.value = null
    refundError.value = '请输入大于 0 且不超过剩余可退金额的兴安币，最多 1 位小数'
    return
  }
  const sequence = ++refundPreviewSequence
  refundLoading.value = true
  refundPreview.value = null
  refundError.value = ''
  try {
    const result = await request<RefundPreview>({ url: `/orders/${target.orderId}/refund-preview/`, method: 'get', params: initializeAmount ? undefined : { refund_amount: raw } })
    if (!operation.current() || !refundVisible.value || sequence !== refundPreviewSequence) return
    if (String(result.data.order_id) !== String(target.orderId)) throw new Error('订单试算不匹配')
    if (!initializeAmount && result.data.refund_amount !== raw) throw new Error('退款金额试算不匹配')
    applyRefundPreview(result.data, initializeAmount)
  } catch (error: any) {
    if (!operation.current() || !refundVisible.value || sequence !== refundPreviewSequence) return
    const body = error?.response?.data || error
    if (body?.errorCode === 'REFUND_INVALID_AMOUNT') {
      try { await refreshRefundLimits(target.orderId, operation, sequence) } catch { /* Keep the prior limits until a successful read. */ }
      if (operation.current() && refundVisible.value && sequence === refundPreviewSequence) refundError.value = '可退余额已变化，请核对剩余可退金额后重新试算'
    } else refundError.value = '退款试算加载失败，请重试'
  } finally {
    if (sequence === refundPreviewSequence) refundLoading.value = false
  }
}

async function submitRefund() {
  const target = refundTarget.value
  const operation = refundOperation
  const preview = refundPreview.value
  if (!target || !operation?.current() || !preview || !refundCanSubmit.value) return
  if (target.ticketId && (!detailVisible.value || detailOrderId.value !== target.orderId || detailFailed.value || detailLoading.value)) return
  refundSubmitting.value = true
  refundError.value = ''
  try {
    pendingActionOrderId = null
    const reason = refundReason.value.trim()
    const intent = { refund_amount: preview.refund_amount, plan_token: preview.plan_token, client_request_id: refundIntentId(preview.refund_amount, reason) }
    if (target.ticketId) await aftersaleApi.action(target.ticketId, 'refund', { resolution: reason, ...intent })
    else await orderApi.action(target.orderId, 'refund', { reason, ...intent })
    // Our own committed order notification may arrive before this HTTP result.
    if (!operation.current(true)) return
    ElMessage.success('已退款')
    requestOrderRefresh(target.orderId)
    clearRefundReview()
  } catch (error: any) {
    if (!operation.current(true) || !refundVisible.value) return
    if (!operation.current()) {
      invalidateOrderAction()
      requestOrderRefresh(target.orderId)
      ElMessage.warning('订单已变化，请重新打开退款复核')
      return
    }
    const body = error?.response?.data || error
    const updated = body?.data?.preview || (body?.data?.order_id ? body.data : null)
    if (updated && String(updated.order_id) === String(target.orderId)) applyRefundPreview(updated)
    else refundPreview.value = null
    if (body?.errorCode === 'REFUND_INVALID_AMOUNT') {
      const sequence = refundPreviewSequence
      try { await refreshRefundLimits(target.orderId, operation, sequence) } catch { /* A fresh preview remains retryable. */ }
      if (!operation.current() || !refundVisible.value || sequence !== refundPreviewSequence) return
    }
    refundError.value = body?.errorCode === 'REFUND_PLAN_CHANGED'
      ? '资金状态已变化，试算已更新。请复核后再次确认'
      : body?.errorCode === 'REFUND_RECOVERY_INSUFFICIENT'
        ? '可追回资金不足，本次退款未扣账。请核对以下明细'
        : body?.errorCode === 'REFUND_INVALID_AMOUNT'
          ? '可退余额已变化，请核对剩余可退金额后重新试算'
        : body?.errorCode === 'REFUND_REQUEST_CONFLICT'
          ? '操作凭证与原退款意图冲突，请关闭后重新核对退款记录'
          : '退款未确认完成，请刷新试算并核对订单状态'
  } finally { if (refundOperation === operation) refundSubmitting.value = false }
}

function orderTeamSize(order: any): 1 | 2 | null {
  const count = Number(order?.required_team_size)
  return count === 1 || count === 2 ? count : null
}

function onAssign(row: any) {
  if (orderTeamSize(row) !== 1) {
    ElMessage.warning(orderTeamSize(row) === 2 ? '该订单需两名陪玩，不能使用单人指派' : '订单人数尚未加载，请刷新后再派单')
    return
  }
  assignOrder.value = row
  assignProviderId.value = undefined
  providerOptions.value = []
  assignVisible.value = true
}

async function onAssignSave() {
  if (orderTeamSize(assignOrder.value) !== 1) {
    ElMessage.warning('该订单不能使用单人指派，请刷新并核对订单人数')
    return
  }
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
const dispatchProviderCount = computed(() => serviceProviderCount(currentService.value))
const dispatchForm = reactive<any>({
  customer_id: undefined,
  service_id: undefined,
  game_rounds: 1,
  provider_gender: 'ANY',
  escort_mode: 'SINGLE',
  providers: [{ provider_id: undefined, commission_type: 'PERCENT', commission_rate: 0, commission_fixed: 0 }],
  remark: '',
})

const dispatchCommissionMode = ref<'AUTO' | 'PERCENT' | 'FIXED'>('AUTO')
const dispatchCommissionFixed = ref<number | undefined>(0)
const dispatchPendingRate = ref<number | undefined>()
const dispatchCommissionError = computed(() => dispatchCommissionMode.value === 'FIXED'
  ? commissionError('FIXED', 0, dispatchCommissionFixed.value)
  : dispatchPendingPercentage() && dispatchPendingRate.value != null ? commissionError('PERCENT', dispatchPendingRate.value, 0) : '')
interface DispatchProviderShare { settlement_base: number; commission_fixed: number; provider_income: number }
const dispatchQuote = ref<{ amount: number; original_amount: number; boss_discount: number; promo_discount: number; commission_policy?: string; commission_type?: string; commission_rate?: number; commission_locked?: boolean; commission_source_label?: string; commission_fixed_total?: number | null; provider_shares?: DispatchProviderShare[] | null }>()
const dispatchFixed = computed(() => dispatchCommissionMode.value === 'FIXED' || dispatchQuote.value?.commission_type === 'FIXED'
  || (!dispatchQuote.value?.commission_type && dispatchQuote.value?.commission_fixed_total != null))
const quoteLoading = ref(false)
let quoteSequence = 0
let quotedInputs = ''
let quotedSession = -1
const estimateAmount = computed(() => dispatchQuote.value?.amount || 0)
function dispatchPendingPercentage(): boolean {
  return dispatchCommissionMode.value === 'PERCENT' && dispatchForm.escort_mode === 'SINGLE' && !dispatchForm.providers[0]?.provider_id
}
function dispatchQuoteKey(): string {
  return JSON.stringify([dispatchForm.customer_id, dispatchForm.service_id, dispatchForm.game_rounds,
    dispatchCommissionMode.value, dispatchCommissionMode.value === 'FIXED' ? dispatchCommissionFixed.value : null,
    dispatchPendingPercentage() ? dispatchPendingRate.value : null])
}
function dispatchCommissionParams() {
  return {
    commission_type: dispatchCommissionMode.value,
    ...(dispatchCommissionMode.value === 'FIXED' ? { commission_fixed: xaCoinToAmount(dispatchCommissionFixed.value) } : {}),
    ...(dispatchPendingPercentage() && dispatchPendingRate.value != null ? { commission_rate: dispatchPendingRate.value } : {}),
  }
}
async function refreshDispatchQuote() {
  const sequence = ++quoteSequence
  const revision = auth.sessionRevision
  const inputs = dispatchQuoteKey()
  quotedInputs = ''
  quotedSession = -1
  dispatchQuote.value = undefined
  quoteLoading.value = false
  if (pageDisposed || !auth.hasPerm('order:dispatch') || !dispatchVisible.value || !dispatchForm.customer_id || !dispatchForm.service_id || dispatchCommissionError.value) return
  quoteLoading.value = true
  try {
    const result = await request({ url: '/orders/dispatch-quote/', params: {
      customer_id: dispatchForm.customer_id, service_id: dispatchForm.service_id, game_rounds: dispatchForm.game_rounds,
      ...dispatchCommissionParams(),
    } })
    if (sequence === quoteSequence && inputs === dispatchQuoteKey() && dispatchVisible.value && !pageDisposed && revision === auth.sessionRevision) {
      dispatchQuote.value = result.data
      if (dispatchPendingPercentage() && dispatchPendingRate.value == null) dispatchPendingRate.value = dispatchQuote.value?.commission_rate
      if (dispatchCommissionMode.value === 'AUTO' && !dispatchForm.providers[0]?.provider_id) {
        dispatchForm.providers[0] && (dispatchForm.providers[0].commission_rate = dispatchQuote.value?.commission_rate ?? 0)
      }
      if (dispatchQuote.value?.commission_locked && dispatchQuote.value.commission_type === 'PERCENT') {
        dispatchForm.providers.forEach((slot: any) => { slot.commission_rate = dispatchQuote.value?.commission_rate ?? 0 })
      }
      quotedInputs = dispatchQuoteKey()
      quotedSession = revision
    }
  } catch {
    // 请求层显示失败原因，禁止使用过期报价继续派单。
  } finally {
    if (sequence === quoteSequence) quoteLoading.value = false
  }
}
watch(() => [dispatchVisible.value, dispatchQuoteKey()], refreshDispatchQuote)
watch(dispatchCommissionMode, mode => {
  if (mode === 'PERCENT') dispatchPendingRate.value = undefined
  dispatchForm.providers.forEach((slot: any, idx: number) => { if (slot.provider_id) void onProviderPicked(idx) })
}, { flush: 'sync' })
function closeDispatchReview() {
  dispatchVisible.value = false
  ++quoteSequence
  quotedSession = -1
  quotedInputs = ''
  dispatchQuote.value = undefined
  quoteLoading.value = false
  dispatchForm.providers.forEach((slot: any) => { slot.suggestion_sequence = (slot.suggestion_sequence || 0) + 1 })
}
watch(() => [auth.sessionRevision, auth.hasPerm('order:dispatch')], closeDispatchReview, { flush: 'sync' })

function providerBase(slot: any): number {
  const count = dispatchForm.escort_mode === 'DOUBLE' ? 2 : 1
  const index = dispatchForm.providers.indexOf(slot)
  return Math.floor(estimateAmount.value / count) + (index < estimateAmount.value % count ? 1 : 0)
}

function dispatchProviderShare(slot: any): DispatchProviderShare | undefined {
  return dispatchQuote.value?.provider_shares?.[dispatchForm.providers.indexOf(slot)]
}

function providerIncome(slot: any): number {
  if (dispatchFixed.value) return dispatchProviderShare(slot)?.provider_income ?? 0
  const rate = Math.min(Math.max(Number(slot.commission_rate) || 0, 0), 100)
  return providerBase(slot) - Math.floor((providerBase(slot) * rate) / 100)
}

function providerIncomeDesc(slot: any): string {
  if (dispatchFixed.value) {
    const share = dispatchProviderShare(slot)
    return share ? `本人折后份额 ${amountToXaCoin(share.settlement_base)}币 − 分摊抽成 ${amountToXaCoin(share.commission_fixed)}币` : '等待服务端分摊结果'
  }
  return `本人折后份额 ${amountToXaCoin(providerBase(slot))}币 × ${100 - (slot.commission_rate || 0)}%`
}

function orderProviderCommissionDesc(provider: any, wholeOrder = false): string {
  if (provider.commission_type === 'FIXED') {
    return `${wholeOrder ? '分摊固定抽成' : '固定抽成'} ${amountToXaCoin(provider.commission_fixed)}币`
  }
  return `抽成 ${provider.commission_rate}%`
}

const insufficient = computed(() => {
  if (!currentBoss.value || estimateAmount.value <= 0) return false
  return currentBoss.value.wallet_balance < estimateAmount.value
})

function serviceProviderCount(service: any): 1 | 2 | null {
  const count = Number(service?.billing_provider_count)
  return count === 1 || count === 2 ? count : null
}

function serviceOptionLabel(service: any): string {
  const price = amountToXaCoin(service.price)
  const count = serviceProviderCount(service)
  if (count === 2) return `${service.name}（每人 ${price}币 × 2 / ${service.price_unit || '份'}）`
  if (count === 1) return `${service.name}（单陪 ${price}币 / ${service.price_unit || '份'}）`
  return `${service.name}（套餐人数待确认）`
}

function syncDispatchProviderCount() {
  const count = serviceProviderCount(currentService.value)
  dispatchForm.escort_mode = count === 2 ? 'DOUBLE' : count === 1 ? 'SINGLE' : ''
  dispatchForm.providers = count ? Array.from({ length: count }, (_, index) => dispatchForm.providers[index]
    || { provider_id: undefined, commission_type: 'PERCENT', commission_rate: 0, commission_fixed: 0 }) : []
}

// 选中打手后带出建议抽成率（客服可再手改）
async function onProviderPicked(idx: number) {
  const slot = dispatchForm.providers[idx]
  if (!slot?.provider_id || !dispatchForm.service_id) return
  const sequence = slot.suggestion_sequence = (slot.suggestion_sequence || 0) + 1
  const revision = auth.sessionRevision
  const serviceId = dispatchForm.service_id
  const providerId = slot.provider_id
  const mode = dispatchCommissionMode.value
  slot.suggestion_loading = true
  try {
    const res = await request({
      url: '/orders/suggest-commission/',
      method: 'get',
      params: { service_id: serviceId, provider_id: providerId, ...(dispatchQuote.value ? { amount: dispatchQuote.value.amount } : {}), ...dispatchCommissionParams() },
    }) as any
    if (!dispatchVisible.value || pageDisposed || revision !== auth.sessionRevision || slot.suggestion_sequence !== sequence
        || dispatchCommissionMode.value !== mode || dispatchForm.service_id !== serviceId || dispatchForm.providers[idx] !== slot || slot.provider_id !== providerId) return
    if (res.data && typeof res.data.commission_rate === 'number') {
      const authoritative = dispatchQuote.value?.commission_locked && dispatchQuote.value.commission_type === 'PERCENT'
      slot.commission_rate = authoritative ? dispatchQuote.value?.commission_rate ?? 0 : res.data.commission_rate
      slot.suggested_rate = slot.commission_rate
      slot.source_label = (authoritative && dispatchQuote.value?.commission_source_label) || res.data.commission_source_label || res.data.source_label || ''
    }
  } catch {
    // 建议抽成率获取失败不阻断，保持当前值
  } finally {
    if (slot.suggestion_sequence === sequence) slot.suggestion_loading = false
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
  if (pageDisposed || dispatching.value || !auth.hasPerm('order:dispatch')) return
  dispatchCommissionMode.value = 'AUTO'
  dispatchCommissionFixed.value = 0
  dispatchPendingRate.value = undefined
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
  syncDispatchProviderCount()
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
  dispatchCommissionMode.value = 'AUTO'
  currentService.value = serviceOptions.value.find((s) => s.id === id) || null
  syncDispatchProviderCount()
  if (currentService.value?.service_type !== 'COMPANION') dispatchForm.provider_gender = 'ANY'
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
    const serviceId = dispatchVisible.value ? dispatchForm.service_id || undefined : undefined
    const service = dispatchVisible.value ? currentService.value : null
    const res = await escortApi.options({ keyword, service_id: serviceId, page_size: 20 }) as any
    providerOptions.value = (res.data?.list || []).map((p: any) => ({...p,qualifications: (p.qualifications || []).filter((q: any) => !service?.provider_kind || (q.provider_kind === service.provider_kind && q.platform === service.platform && q.security_level === service.security_level && q.game_category === service.game_category_id))}))
  } finally {
    providerLoading.value = false
  }
}

async function onDispatchSave() {
  if (dispatching.value) return
  if (pageDisposed || !dispatchVisible.value || !auth.hasPerm('order:dispatch')) return
  if (!dispatchForm.customer_id) { ElMessage.warning('请选择下单老板'); return }
  if (!dispatchForm.service_id) { ElMessage.warning('请选择服务'); return }
  const providerCount = serviceProviderCount(currentService.value)
  if (providerCount === null) { ElMessage.warning('套餐人数尚未加载，请重新选择服务'); return }
  if (dispatchForm.escort_mode !== (providerCount === 2 ? 'DOUBLE' : 'SINGLE')
      || dispatchForm.providers.length !== providerCount) {
    ElMessage.warning('陪玩人数与所选套餐不一致，请重新选择服务')
    return
  }
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
  if (dispatchCommissionError.value) { ElMessage.warning(dispatchCommissionError.value); return }
  if (dispatchPendingPercentage() && dispatchPendingRate.value == null) { ElMessage.warning('请等待或填写待接单陪玩的抽成比例'); return }
  if (quoteLoading.value || !dispatchQuote.value || quotedInputs !== dispatchQuoteKey() || quotedSession !== auth.sessionRevision) { ElMessage.warning('请等待成交价及抽成试算完成后再派单'); return }
  if (insufficient.value) { ElMessage.warning('老板余额不足，无法派单'); return }
  if (!dispatchFixed.value && picked.some((slot: any) => slot.suggestion_loading)) { ElMessage.warning('请等待陪玩抽成建议加载完成'); return }
  if (!dispatchFixed.value && picked.some((slot: any) => commissionError('PERCENT', slot.commission_rate, 0))) { ElMessage.warning('陪玩抽成比例须为 0 到 100 的整数'); return }
  const providersPayload = picked.map((slot: any) => dispatchFixed.value || dispatchQuote.value?.commission_locked
    ? { provider_id: slot.provider_id }
    : { provider_id: slot.provider_id, commission_type: 'PERCENT', commission_rate: slot.commission_rate })
  dispatching.value = true
  const revision = auth.sessionRevision
  const requestId = dispatchRequestId.value
  try {
    const result = await orderApi.collectionAction('dispatch', {
      client_request_id: dispatchRequestId.value,
      customer_id: dispatchForm.customer_id,
      service_id: dispatchForm.service_id,
      game_rounds: dispatchForm.game_rounds,
      expected_amount: dispatchQuote.value.amount,
      ...dispatchCommissionParams(),
      escort_mode: dispatchForm.escort_mode,
      provider_gender: dispatchForm.provider_gender,
      providers: providersPayload,
      remark: dispatchForm.remark,
    })
    if (pageDisposed || !dispatchVisible.value || requestId !== dispatchRequestId.value || revision !== auth.sessionRevision || !auth.hasPerm('order:dispatch')) return
    ElMessage.success('派单成功')
    dispatchVisible.value = false
    tableRef.value.load()
    if (result.data?.status === 'PENDING') onDispatchMessage(result.data)
  } catch (error: any) {
    if (pageDisposed || !dispatchVisible.value || requestId !== dispatchRequestId.value || revision !== auth.sessionRevision || !auth.hasPerm('order:dispatch')) return
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

watch(() => [auth.sessionRevision, canReadOrders.value, auth.hasPerm('order:refund'), auth.hasPerm('order:settle'), auth.hasPerm('order:cancel')], () => {
  stopOrderRealtime()
  detailVisible.value = false
  clearOrderDetail()
  if (pageMounted) startOrderRealtime()
}, { flush: 'sync' })

onMounted(() => {
  pageMounted = true
  startOrderRealtime()
})

onUnmounted(() => {
  pageDisposed = true
  closeDispatchReview()
  stopOrderRealtime()
  clearOrderDetail()
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
.od-refund-section { margin-top: 16px; }
.od-refund-record { margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--border); }
.od-refund-record h4 { margin: 0; font-size: 14px; }
.od-refund-section h3 { margin: 0 0 12px; font-size: 16px; }
.od-refund-summary { margin-top: 12px; }
.od-refund-note { color: var(--muted-foreground); font-size: 13px; line-height: 1.6; }
.od-refund-line {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  margin: 12px 0;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow-wrap: anywhere;
}
.od-refund-line > span { color: var(--muted-foreground); font-size: 12px; }
.od-refund-amounts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 20px; margin: 0; }
.od-refund-amounts > div { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 8px; }
.od-refund-amounts dt { color: var(--muted-foreground); }
.od-refund-amounts dd { margin: 0; font-variant-numeric: tabular-nums; }
.od-refund-shortfalls { padding-left: 20px; color: var(--destructive); line-height: 1.7; }
.od-refund-reason { margin-top: 16px; }
@media (max-width: 600px) { .od-refund-amounts { grid-template-columns: minmax(0, 1fr); } }


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
