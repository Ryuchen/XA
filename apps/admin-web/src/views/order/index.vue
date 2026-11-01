<template>
  <div class="od-page">
    <!-- 头部操作行 -->
    <div class="ap-page-head">
      <p class="ap-page-intro">管理平台全部陪玩订单与售后</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('order:view')" :icon="Setting" @click="timeoutConfigVisible = true">待接单时限</el-button>
        <el-button v-if="auth.hasPerm('order:view')" :icon="Setting" @click="trafficConfigVisible = true">峰谷限流与监控</el-button>
        <el-button v-if="auth.hasPerm('order:dispatch')" type="primary" :icon="Plus" @click="onDispatch">快捷派单</el-button>
        <el-button v-if="auth.hasPerm(['dispatch_config:view', 'dispatch_config:edit'])" :icon="Setting" @click="dispatchConfigVisible = true">配置</el-button>
      </div>
    </div>

    <el-alert v-if="auth.hasPerm('order:view') && auth.hasPerm('order:dispatch') && staffQueue.status === 'review'"
      title="部分客服待派订单需要核对"
      description="请查看客服派单订单详情并核对订单状态；暂不可派的订单不会显示可用派单操作。"
      type="warning" :closable="false" show-icon />

    <el-alert v-if="transferReview && !transferring" title="转单结果待核对" type="warning" :closable="false" show-icon>
      <p>订单 {{ transferReview.orderNo }} 的转单结果未确认，请查看人员和状态流水。请求可能仍在处理。</p>
      <el-button :loading="transferReviewLoading" @click="viewTransferReview">查看订单记录</el-button>
      <el-button v-if="transferReviewChecked" :disabled="transferReviewLoading" @click="finishTransferReview">已核对，解除限制</el-button>
    </el-alert>
    <el-alert v-if="assignReview && !assigning && !assignVisible && canReadOrders" title="指派结果待核对" type="warning" :closable="false" show-icon>
      <p>订单 {{ assignReview.orderNo }} 的指派结果未确认。请核对陪玩和状态流水；请求可能仍在处理，读取仅显示当前状态。</p>
      <el-button :loading="assignReviewLoading" @click="viewAssignReview">查看订单记录</el-button>
      <el-button v-if="assignReviewChecked" :disabled="assignReviewLoading" @click="finishAssignReview">已核对，解除限制</el-button>
    </el-alert>
    <el-alert v-if="dispatchReview && !dispatchVisible" title="快捷派单结果待核对" type="warning" :closable="false" show-icon>
      <p>{{ dispatchReview.boss }} · {{ dispatchReview.service }} · {{ dispatchReview.time }}。请求可能仍在处理，请核对订单和扣款，避免重复派单。</p>
      <el-button v-if="canReadOrders" @click="viewDispatchReview">查看该老板订单</el-button>
      <span v-else>请联系有订单查看权限的人员核对。</span>
      <el-button @click="finishDispatchReview">已核对，解除限制</el-button>
    </el-alert>
    <el-alert v-if="exactFilterUnsupported && query.order_no_exact" title="精确定位暂不可用" description="当前服务未确认精确筛选，请联系管理员更新服务端，或清除定位后使用普通搜索。" type="error" :closable="false" show-icon />
    <el-alert v-if="customerFilterUnsupported && query.customer_account_id" title="老板订单筛选暂不可用" description="当前服务未确认筛选范围，请联系管理员更新服务端，或清除老板筛选。" type="error" :closable="false" show-icon />
    <el-alert v-if="createdDayUnsupported && query.created_day" title="日期筛选暂不可用" description="当前服务未确认日期范围，请联系管理员更新服务端，或清除日期筛选。" type="error" :closable="false" show-icon />
    <el-alert v-if="canReadOrders && invalidCustomerFilter" title="老板筛选无效" type="error" :closable="false" show-icon><p>请清除筛选，或从老板资料重新进入订单。</p><el-button link type="primary" @click="clearInvalidCustomerFilter">清除无效筛选</el-button></el-alert>
    <el-alert v-if="canReadOrders && invalidCreatedDay" title="订单日期无效" type="error" :closable="false" show-icon><p>请清除日期，或从工作台重新选择。</p><el-button link type="primary" @click="clearInvalidCreatedDay">清除无效日期</el-button></el-alert>

    <crud-table v-if="canReadOrders && !invalidCustomerFilter && !invalidCreatedDay" ref="tableRef" :fetcher="fetchOrders" :query="query" :pagination-disabled="searchInputsPending" :external-error="customerFilterUnsupported || createdDayUnsupported" selectable table-scroll>
      <template #toolbar="{ reload }">
        <div class="od-filter-heading"><strong>筛选订单</strong><span>按订单号、老板信息或订单状态查找</span></div>
        <div v-if="query.customer_account_id" class="od-customer-filter" role="status"><span>老板账号 ID：{{ query.customer_account_id }}</span><el-button link type="primary" @click="query.customer_account_id = ''; reload()">清除老板筛选</el-button></div>
        <div v-if="query.created_day" class="od-customer-filter" role="status"><span>创建日期：{{ query.created_day }} · {{ createdDayTimezone || '时区待确认' }}</span><el-tooltip content="按服务端时区的自然日筛选；列表下单时间按本机时区显示。" :trigger="['hover', 'focus', 'click']" popper-class="od-help-tooltip"><button type="button" class="od-label-help" aria-label="日期和时区说明"><el-icon><InfoFilled /></el-icon></button></el-tooltip><el-button link type="primary" @click="query.created_day = ''; reload()">清除日期</el-button></div>
        <div v-if="query.order_no_exact" class="od-customer-filter" role="status"><span>精确查找订单 {{ query.order_no_exact }}</span><el-button link type="primary" @click="clearExactOrder(reload)">清除定位</el-button></div>
        <div v-if="query.status && !query.actionable_staff && !(query.status === 'PENDING' && (query.payment_status === 'UNPAID' || (query.payment_status === 'PAID' && ['STAFF', 'PUBLIC'].includes(query.dispatch_route))))" class="od-customer-filter" role="status"><span>订单状态：{{ ORDER_STATUS[query.status]?.label || query.status }}</span><el-button link type="primary" @click="query.status = ''; reload()">清除状态</el-button></div>
        <div v-if="query.status === 'PENDING' && query.payment_status === 'UNPAID' && !query.actionable_staff" class="od-customer-filter" role="status"><span>未支付 · 待接单</span><el-button link type="primary" @click="query.status = ''; query.payment_status = ''; reload()">清除状态</el-button></div>
        <el-tooltip content="切换到客服待派会清空当前搜索条件，仅显示已支付且符合待派筛选的订单。具体指派资格仍以订单详情和提交时校验为准。" placement="top" popper-class="od-help-tooltip" :trigger="['hover', 'focus']">
          <el-button :type="query.actionable_staff ? 'primary' : 'default'" :aria-pressed="Boolean(query.actionable_staff)" @click="showStaffPending(reload)">客服待派</el-button>
        </el-tooltip>
        <el-tooltip v-if="query.actionable_staff" content="清除当前搜索和状态条件，返回全部订单。" placement="top" popper-class="od-help-tooltip" :trigger="['hover', 'focus']">
          <el-button @click="clearStaffPending(reload)">查看全部订单</el-button>
        </el-tooltip>
        <span v-if="query.actionable_staff" class="od-filter-context" role="status">已筛选已支付、可派的客服订单 · {{ staffQueueDeadlineOrder ? '截止时间从早到晚，未记录排后' : '排序待确认' }}</span>
        <span v-else-if="query.status === 'PENDING' && query.payment_status === 'PAID' && ['STAFF', 'PUBLIC'].includes(query.dispatch_route)" class="od-filter-context" role="status">已筛选已支付、待接单的{{ query.dispatch_route === 'STAFF' ? '客服派单' : '公开接单' }}订单；不代表均可派单</span>
        <el-checkbox v-model="query.aftersale_open" true-value="1" false-value="" @change="reload">处理中售后</el-checkbox>
        <span v-if="query.aftersale_open && !query.actionable_staff" class="od-filter-context" role="status">处理中售后 · {{ aftersaleQueueOrder ? '按申请时间从早到晚' : '排序待确认' }} · 本机时间</span>
        <el-autocomplete
          v-model="query.order_no"
          :fetch-suggestions="querySuggest('order_no')"
          placeholder="订单号"
          aria-label="按订单号搜索"
          clearable
          value-key="value"
          style="width: 200px"
          @input="changeOrderNumber"
          @select="reload"
          @keyup.enter="reload"
          @clear="clearExactOrder(reload)"
        />
        <el-autocomplete
          v-model="query.customer_username"
          :fetch-suggestions="querySuggest('customer_username')"
          placeholder="老板账号"
          aria-label="按老板账号搜索"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
        <el-button :aria-expanded="advancedFiltersOpen" aria-controls="od-advanced-filters" @click="advancedFiltersOpen = !advancedFiltersOpen">{{ advancedFiltersOpen ? '收起筛选' : '更多筛选' }}<template v-if="advancedFilterCount">（{{ advancedFilterCount }}）</template></el-button>
        <el-button v-if="hasOrderFilters" text @click="clearStaffPending(reload)">清除条件</el-button>
        <span v-if="searchInputsPending" class="od-filter-context" role="status">筛选输入已更改，列表尚未更新；请点击查询</span>
        <div v-show="advancedFiltersOpen" id="od-advanced-filters" class="od-advanced-filters">
        <el-autocomplete
          v-model="query.customer_nickname"
          :fetch-suggestions="querySuggest('customer_nickname')"
          placeholder="老板昵称"
          aria-label="按老板昵称搜索"
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
          aria-label="按老板当前手机号搜索"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.status" placeholder="订单状态" aria-label="订单状态" clearable style="width: 130px" @change="clearActionableFilter(reload)">
          <el-option v-for="(v, k) in ORDER_STATUS" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-select v-model="query.payment_status" placeholder="支付状态" aria-label="支付状态" clearable style="width: 130px" @change="clearActionableFilter(reload)">
          <el-option v-for="(v, k) in PAYMENT_STATUS" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-select v-model="query.dispatch_route" placeholder="接单路径" aria-label="接单路径" clearable style="width: 140px" @change="clearActionableFilter(reload)">
          <el-option label="客服派单" value="STAFF" />
          <el-option label="公开接单" value="PUBLIC" />
        </el-select>
        <span class="od-filter-note">按当前账号资料查询；无业务账号的旧订单使用旧资料。</span>
        </div>
      </template>

      <template #mobile="{ rows, selected, toggle }">
        <div class="od-mobile-list" role="list" aria-label="订单列表">
          <article v-for="row in rows" :key="row.id" class="od-mobile-card" role="listitem">
            <div class="od-mobile-head">
              <el-checkbox :model-value="selected.some((item: any) => item.id === row.id)" :aria-label="`选择订单 ${row.order_no}`" @change="toggle(row, $event)" />
              <strong>{{ row.order_no }}</strong>
              <el-tag :type="ORDER_STATUS[row.status]?.type" effect="light" round>{{ row.status_display }}</el-tag>
            </div>
            <p class="od-mobile-service">{{ row.service_name_snapshot || '服务待核对' }}</p>
            <dl class="od-mobile-values">
              <div><dt><el-tooltip :content="customerIdentityHelp" :trigger="['hover', 'focus', 'click']" popper-class="od-help-tooltip"><button type="button" class="od-label-help">老板 <el-icon><InfoFilled /></el-icon></button></el-tooltip></dt><dd><span>{{ row.customer_name || '待核对' }}</span><small class="od-customer-account">{{ customerAccountLabel(row) }}</small></dd></div>
              <div><dt>陪玩</dt><dd>{{ providerListText(row) }}{{ orderTeamSize(row) === 2 ? ' · 双陪' : '' }}</dd></div>
              <div><dt>金额</dt><dd>{{ amountToXaCoin(row.amount) }} 币 · {{ row.payment_status_display }}</dd></div>
              <div v-if="query.aftersale_open"><dt>售后申请</dt><dd>{{ row.aftersale_open_at ? formatDateTime(row.aftersale_open_at) : '时间待核对' }}</dd></div>
              <div><dt><el-tooltip :content="dispatchRouteHelp" :trigger="['hover', 'focus', 'click']" popper-class="od-help-tooltip"><button type="button" class="od-label-help">接单路径 <el-icon><InfoFilled /></el-icon></button></el-tooltip></dt><dd>{{ dispatchRouteLabel(row) }}<el-tag v-if="isStaffPending(row)" class="od-route-status" size="small" type="warning" effect="light">客服待派</el-tag></dd></div>
              <div v-if="isStaffPendingOrder(row)"><dt>待派截止</dt><dd>{{ row.auto_cancel_at ? formatDateTime(row.auto_cancel_at) : '未记录' }}</dd></div>
            </dl>
            <p class="od-mobile-time">下单 {{ formatDateTime(row.created_at) }}</p>
            <div class="od-mobile-actions">
              <el-button @click="onDetail(row)">查看详情</el-button>
              <el-button v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING' && row.payment_status === 'PAID' && row.dispatch_route !== 'STAFF'" :disabled="row.dispatch_message_allowed !== true" @click="onDispatchMessage(row)">一键发单</el-button>
              <el-button v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING'" type="primary" :disabled="!canAssignOrder(row)" @click="onAssign(row)">派单</el-button>
              <el-button v-if="auth.hasPerm('order:dispatch') && row.status === 'GRABBED'" type="primary" :disabled="orderActionBusy" @click="onStart(row)">开始</el-button>
              <el-button v-if="auth.hasPerm('order:settle') && row.status === 'IN_SERVICE'" type="primary" :disabled="orderActionBusy" @click="onComplete(row)">完成</el-button>
              <el-button v-if="auth.hasPerm('order:dispatch') && row.status === 'IN_SERVICE'" @click="onTransfer(row)">转单</el-button>
              <el-button v-if="auth.hasPerm('order:cancel') && row.status === 'PENDING'" :disabled="orderActionBusy" @click="onCancel(row)">取消订单</el-button>
              <el-button v-if="auth.hasPerm('order:refund') && ['PAID', 'PARTIALLY_REFUNDED'].includes(row.payment_status) && ['GRABBED', 'IN_SERVICE', 'COMPLETED'].includes(row.status)" :disabled="orderActionBusy" @click="onRefund(row)">退款</el-button>
            </div>
            <p v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING' && !canAssignOrder(row)" class="od-assign-hint">暂不可派，请查看详情并核对订单状态</p>
            <p v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING' && row.payment_status === 'PAID' && row.dispatch_route !== 'STAFF' && row.dispatch_message_allowed !== true" class="od-assign-hint">暂不能发单：{{ row.dispatch_message_unavailable_reason || '请核对订单状态' }}</p>
          </article>
        </div>
      </template>

      <el-table-column prop="order_no" label="订单号" width="190" fixed="left" />
      <el-table-column label="订单状态" width="95">
        <template #default="{ row }">
          <el-tag :type="ORDER_STATUS[row.status]?.type" effect="light" round>{{ row.status_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="支付状态" width="95">
        <template #default="{ row }">
          <el-tag :type="PAYMENT_STATUS[row.payment_status]?.type" effect="light" round>{{ row.payment_status_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column v-if="query.aftersale_open" label="售后申请时间" width="160">
        <template #default="{ row }">{{ row.aftersale_open_at ? formatDateTime(row.aftersale_open_at) : '时间待核对' }}</template>
      </el-table-column>
      <el-table-column v-if="query.actionable_staff" key="staff-deadline" label="待派截止时间" width="160">
        <template #default="{ row }">{{ isStaffPendingOrder(row) ? (row.auto_cancel_at ? formatDateTime(row.auto_cancel_at) : '未记录') : '—' }}</template>
      </el-table-column>
      <el-table-column prop="customer_name" width="150" class-name="od-customer-column" :show-overflow-tooltip="false">
        <template #header><el-tooltip :content="customerIdentityHelp" :trigger="['hover', 'focus', 'click']" popper-class="od-help-tooltip"><button type="button" class="od-label-help">老板 <el-icon><InfoFilled /></el-icon></button></el-tooltip></template>
        <template #default="{ row }"><div class="od-customer-identity"><span>{{ row.customer_name || '待核对' }}</span><small class="od-customer-account">{{ customerAccountLabel(row) }}</small></div></template>
      </el-table-column>
      <el-table-column label="金额" width="100">
        <template #default="{ row }">{{ amountToXaCoin(row.amount) }} 币</template>
      </el-table-column>
      <el-table-column prop="provider_name" label="陪玩" width="130">
        <template #default="{ row }">
          <el-tag v-if="orderTeamSize(row) === 2" size="small" type="info" effect="light" round style="margin-right: 4px">双陪</el-tag>
          <span>{{ providerListText(row) }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="service_name_snapshot" label="服务" min-width="130" />
      <el-table-column prop="support_contact_name_snapshot" label="负责客服" width="110">
        <template #default="{ row }">{{ row.support_contact_name_snapshot || '-' }}</template>
      </el-table-column>
      <el-table-column width="150">
        <template #header><el-tooltip :content="dispatchRouteHelp" :trigger="['hover', 'focus', 'click']" popper-class="od-help-tooltip"><button type="button" class="od-label-help">接单路径 <el-icon><InfoFilled /></el-icon></button></el-tooltip></template>
        <template #default="{ row }">
          <span>{{ dispatchRouteLabel(row) }}</span><el-tag v-if="isStaffPending(row)" class="od-route-status" size="small" type="warning" effect="light">客服待派</el-tag>
        </template>
      </el-table-column>
      <el-table-column v-if="!query.actionable_staff" key="general-deadline" label="待派截止时间" width="160">
        <template #default="{ row }">{{ isStaffPendingOrder(row) ? (row.auto_cancel_at ? formatDateTime(row.auto_cancel_at) : '未记录') : '—' }}</template>
      </el-table-column>
      <el-table-column label="下单时间" width="160">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="onDetail(row)">详情</el-button>
          <el-button
            v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING' && row.payment_status === 'PAID' && row.dispatch_route !== 'STAFF'"
            link type="primary" :disabled="row.dispatch_message_allowed !== true" @click="onDispatchMessage(row)"
          >一键发单</el-button>
          <el-button
            v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING'"
            link
            type="primary"
            :disabled="!canAssignOrder(row)"
            :title="assignTitle(row)"
            @click="onAssign(row)"
          >派单</el-button>
          <el-button
            v-if="auth.hasPerm('order:dispatch') && row.status === 'GRABBED'"
            link
            type="primary"
            :disabled="orderActionBusy"
            @click="onStart(row)"
          >开始</el-button>
          <el-button
            v-if="auth.hasPerm('order:settle') && row.status === 'IN_SERVICE'"
            link
            type="success"
            :disabled="orderActionBusy"
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
          <div v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING' && !canAssignOrder(row)" class="od-assign-hint">暂不可派，请查看详情并核对订单状态</div>
          <div v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING' && row.payment_status === 'PAID' && row.dispatch_route !== 'STAFF' && row.dispatch_message_allowed !== true" class="od-assign-hint">暂不能发单：{{ row.dispatch_message_unavailable_reason || '请核对订单状态' }}</div>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-drawer v-model="detailVisible" title="订单详情" size="520px" class="ap-panel">
    <el-skeleton v-if="detailLoading && !current" :rows="8" animated />
    <template v-else>
    <el-alert v-if="detailFailed" :title="current ? '订单更新失败，当前显示上次结果，请重新加载' : '订单详情加载失败，请重试'" type="error" :closable="false" show-icon>
      <el-button text type="primary" :disabled="detailLoading" @click="refreshCurrentOrder">重新加载</el-button>
    </el-alert>
    <template v-if="current">
      <p v-if="detailLoading" class="od-detail-updating" role="status">正在更新订单信息…</p>
      <h3 class="od-detail-heading">订单信息</h3>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="订单号">{{ current.order_no }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ current.status_display }}</el-descriptions-item>
        <el-descriptions-item label="支付状态">{{ current.payment_status_display }}</el-descriptions-item>
        <el-descriptions-item v-if="current.reservation_origin" label="原预约时段">{{ reservationDate(current.reservation_origin.start_time) }} 至 {{ reservationDate(current.reservation_origin.end_time) }}（北京时间）<el-button link type="primary" @click="router.push({ path: '/reservations', query: { find_reservation: current.reservation_origin.id } })">查看预约</el-button></el-descriptions-item>
        <el-descriptions-item><template #label><el-tooltip :content="dispatchRouteHelp" :trigger="['hover', 'focus', 'click']" popper-class="od-help-tooltip"><button type="button" class="od-label-help">接单路径 <el-icon><InfoFilled /></el-icon></button></el-tooltip></template>{{ dispatchRouteLabel(current) }}<el-tag v-if="isStaffPending(current)" class="od-route-status" size="small" type="warning" effect="light">客服待派</el-tag></el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'PENDING' && current.payment_status === 'PAID' && current.dispatch_route === 'PUBLIC' && typeof current.requires_boss_selection === 'boolean'" label="公开接单方式">{{ current.requires_boss_selection ? '老板选择陪玩' : '陪玩直接接单' }}</el-descriptions-item>
      <el-descriptions-item v-if="isStaffPendingOrder(current)" label="待派截止时间">{{ current.auto_cancel_at ? formatDateTime(current.auto_cancel_at) : '未记录' }}</el-descriptions-item>
        <el-descriptions-item label="老板">{{ current.customer_name || '待核对' }}</el-descriptions-item>
        <el-descriptions-item label="老板账号">{{ current.customer_username || '待核对' }}<el-tooltip :content="customerIdentityHelp" :trigger="['hover', 'focus', 'click']" popper-class="od-help-tooltip"><button type="button" class="od-label-help od-identity-help" aria-label="老板资料说明"><el-icon><InfoFilled /></el-icon></button></el-tooltip></el-descriptions-item>
        <el-descriptions-item label="下单手机号">{{ current.customer_phone_snapshot || '未记录' }}</el-descriptions-item>
        <el-descriptions-item label="陪玩选择">{{ ({ ANY: '不限', MALE: '男陪', FEMALE: '女陪' } as any)[current.provider_gender || 'ANY'] }}</el-descriptions-item>
        <el-descriptions-item label="成交陪玩人数">{{ orderTeamSize(current) === 2 ? '2 人（双陪）' : orderTeamSize(current) === 1 ? '1 人（单陪）' : '等待服务端确认' }}</el-descriptions-item>
        <el-descriptions-item label="陪玩">
          <div v-for="(person, index) in currentProviderIdentities(current)" :key="person.id || index" class="od-person-identity">{{ providerIdentityLabel(person) }}</div>
          <span v-if="!currentProviderIdentities(current).length">—</span>
          <el-tooltip v-if="currentProviderIdentities(current).some((person: any) => person.provider_no || person.provider_username)" content="编号与账号为读取时的资料，不是订单创建时快照。" :trigger="['hover', 'focus', 'click']" placement="top"><button type="button" class="od-detail-help" aria-label="陪玩身份说明">?</button></el-tooltip>
        </el-descriptions-item>
        <el-descriptions-item v-if="current.team_progress" :label="current.team_progress.stage === 'ENTRY' ? '入队截图进度' : '结单截图进度'">
          <div>{{ current.team_progress.submitted_count }}/{{ current.team_progress.total_count }} 已提交</div>
          <div v-for="member in current.team_progress.members" :key="member.id">{{ member.nickname }} · {{ member.submitted ? '已提交' : '等待提交' }}</div>
        </el-descriptions-item>
        <el-descriptions-item label="服务">{{ current.service_name_snapshot }}</el-descriptions-item>
        <el-descriptions-item v-if="current.provider_requirement?.label" label="接单范围">{{ current.provider_requirement.label }}</el-descriptions-item>
        <el-descriptions-item v-if="current.gameplay_snapshot?.name" label="玩法规格">{{ current.gameplay_snapshot.name }} · {{ current.gameplay_snapshot.platform_name }} · {{ current.gameplay_snapshot.security_level_name }}</el-descriptions-item>
        <el-descriptions-item v-if="current.roulette_result?.name" label="转盘目标">{{ current.roulette_result.name }}：{{ current.roulette_result.description }}</el-descriptions-item>
        <el-descriptions-item v-if="current.gameplay_snapshot?.rules" label="规则快照">{{ current.gameplay_snapshot.rules }}</el-descriptions-item>
        <el-descriptions-item label="负责客服">{{ current.support_contact_name_snapshot || '-' }}</el-descriptions-item>
      </el-descriptions>
      <h3 class="od-detail-heading">游戏资料</h3>
      <el-descriptions v-if="current.game_region || current.game_nickname || current.game_uid" :column="1" border>
        <el-descriptions-item label="区服"><span class="od-game-value">{{ current.game_region || '未填写' }}</span></el-descriptions-item>
        <el-descriptions-item label="游戏昵称"><span class="od-game-value">{{ current.game_nickname || '未填写' }}</span></el-descriptions-item>
        <el-descriptions-item label="游戏 ID"><span class="od-game-value">{{ current.game_uid || '未填写' }}</span></el-descriptions-item>
      </el-descriptions>
      <p v-else class="od-detail-empty">未填写游戏资料</p>
      <h3 class="od-detail-heading">金额与收益</h3>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="单价">{{ amountToXaCoin(current.unit_price_snapshot) }} 币</el-descriptions-item>
        <el-descriptions-item label="局数">{{ current.game_rounds }}</el-descriptions-item>
        <el-descriptions-item v-if="current.original_amount" label="原价">{{ amountToXaCoin(current.original_amount) }} 币</el-descriptions-item>
        <el-descriptions-item v-if="current.boss_discount" label="老板折扣">-{{ amountToXaCoin(current.boss_discount) }} 币</el-descriptions-item>
        <el-descriptions-item v-if="current.promo_discount" label="活动折扣">-{{ amountToXaCoin(current.promo_discount) }} 币</el-descriptions-item>
        <el-descriptions-item v-if="current.coupon_discount" label="优惠券折扣">-{{ amountToXaCoin(current.coupon_discount) }} 币</el-descriptions-item>
        <el-descriptions-item label="服务成交金额">{{ amountToXaCoin(current.amount) }} 币</el-descriptions-item>
        <el-descriptions-item v-if="current.promotion_title" label="命中活动">{{ current.promotion_title }}</el-descriptions-item>
        <el-descriptions-item v-if="current.commission_type === 'FIXED'" label="整单固定抽成">{{ amountToXaCoin(current.commission_fixed) }} 币 · 整单仅扣一次，由参与陪玩分摊</el-descriptions-item>
        <template v-if="current.providers && current.providers.length">
          <el-descriptions-item
            v-for="(p, i) in current.providers"
            :key="p.id"
            :label="current.providers.length > 1 ? `打手${i + 1}` : '打手'"
          >
            {{ providerIdentityLabel(p) }}{{ p.released_at ? '（已转出）' : '' }} · {{ orderProviderCommissionDesc(p, current.commission_type === 'FIXED') }} · {{ ['REFUNDED', 'PARTIALLY_REFUNDED'].includes(current.payment_status) ? (historicalSettlementRecorded(current, p) ? '原结算收益' : '原预计收益') : current.settlement_status === 'SETTLED' ? '实得' : '预计收益' }} {{ amountToXaCoin(p.provider_income) }} 币
            <span v-if="!['REFUNDED', 'PARTIALLY_REFUNDED'].includes(current.payment_status) && current.settlement_status === 'SETTLED'" class="od-log-op"> · 已结算</span>
          </el-descriptions-item>
        </template>
        <template v-else>
          <el-descriptions-item v-if="current.commission_type !== 'FIXED'" label="平台抽成率">{{ current.commission_rate }}%</el-descriptions-item>
          <el-descriptions-item :label="['REFUNDED', 'PARTIALLY_REFUNDED'].includes(current.payment_status) ? (historicalSettlementRecorded(current) ? '原结算陪玩收益' : '原预计陪玩收益') : current.settlement_status === 'SETTLED' ? '陪玩实得' : '预计陪玩收益'">{{ amountToXaCoin(current.provider_income) }} 币</el-descriptions-item>
        </template>
        <el-descriptions-item label="推荐人">{{ current.inviter_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="推荐人分佣">{{ amountToXaCoin(current.inviter_commission) }} 币</el-descriptions-item>
        <el-descriptions-item :label="['REFUNDED', 'PARTIALLY_REFUNDED'].includes(current.payment_status) ? '原店铺留存快照' : '店铺留存快照'">{{ amountToXaCoin(current.shop_income) }} 币</el-descriptions-item>
        <el-descriptions-item label="收益状态">{{ settlementStatusLabel(current.settlement_status) }}</el-descriptions-item>
        <el-descriptions-item v-if="current.settlement_review_reason" label="结算核对">{{ current.settlement_review_reason }} <router-link v-if="auth.hasPerm('transaction:view') && current.order_no" :to="{ name: 'transactions', query: { order_no: current.order_no } }">查看订单流水</router-link></el-descriptions-item>
        <el-descriptions-item v-if="current.settlement_status === 'SETTLED' && current.settled_at" label="原结算时间">{{ formatDateTime(current.settled_at) }}</el-descriptions-item>
      </el-descriptions>
      <h3 class="od-detail-heading">备注与时间</h3>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="备注">{{ current.remark || '-' }}</el-descriptions-item>
        <el-descriptions-item label="取消原因">{{ current.cancel_reason || '-' }}</el-descriptions-item>
        <el-descriptions-item label="售后截止">{{ formatDateTime(current.aftersale_deadline) }}</el-descriptions-item>
        <el-descriptions-item label="记录的实销确认时间">
          {{ current.consumption_confirmed_at ? formatDateTime(current.consumption_confirmed_at) : '暂无确认记录' }}
          <span v-if="current.consumption_confirmed_at && consumptionReversed(current)"> · 已冲回</span>
          <el-tooltip content="此处显示历史确认时间；退款后的实销冲回以退款明细为准。" :trigger="['hover', 'focus', 'click']" placement="top"><button type="button" class="od-detail-help" aria-label="实销确认时间说明">?</button></el-tooltip>
        </el-descriptions-item>
        <el-descriptions-item v-if="!current.consumption_confirmed_at && current.consumption_ready_at" label="报单计时节点">
          {{ formatDateTime(current.consumption_ready_at) }}
          <el-tooltip content="按有效报单提交时间计算。到达此时间不代表实销已确认，实际仍须满足订单、审核及售后等条件。" :trigger="['hover', 'focus', 'click']" placement="top"><button type="button" class="od-detail-help" aria-label="报单计时节点说明">?</button></el-tooltip>
        </el-descriptions-item>
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
      <h3 class="od-detail-heading">售后</h3>
      <el-alert v-if="aftersaleFailed" :title="aftersales.length ? '售后更新失败，以下为上次结果，请重新加载后处理' : '售后信息加载失败，请重新加载'" type="warning" :closable="false" show-icon><el-button text type="primary" :disabled="detailLoading" @click="refreshCurrentOrder">重新加载</el-button></el-alert>
      <p v-else-if="!aftersales.length && !detailLoading" class="od-detail-empty">暂无售后记录</p>
      <div v-for="ticket in aftersales" :key="ticket.id" class="ap-section"><p>售后 #{{ticket.id}} · {{ticket.status === 'OPEN' ? '处理中' : ticket.status === 'REFUNDED' ? '已退款' : ticket.status === 'PARTIALLY_REFUNDED' ? '部分退款' : '处理完成'}}</p><p>{{ticket.reason}}</p><p v-if="ticket.resolution">处理说明：{{ticket.resolution}}</p><template v-if="ticket.status === 'OPEN'"><p v-if="current.amount === 0">本单实付为 0，无需退款；请处理售后咨询。</p><p v-else-if="current.payment_status === 'REFUNDED'">订单已全额退款，请继续处理售后咨询。</p><el-button v-if="auth.hasPerm('order:settle')" :disabled="orderActionBusy || detailLoading || detailFailed || aftersaleFailed" @click="processAfterSale(ticket,'resolve')">处理完成</el-button><el-button v-if="auth.hasPerm('order:refund') && current.amount !== 0 && current.payment_status !== 'REFUNDED'" type="danger" :disabled="orderActionBusy || detailLoading || detailFailed || aftersaleFailed" @click="processAfterSale(ticket,'refund')">退款结案</el-button></template></div>

      <el-divider>状态流水</el-divider>
      <el-alert v-if="logsFailed" :title="logs.length ? '状态流水更新失败，以下为上次结果' : '状态流水加载失败，请重新加载'" type="warning" :closable="false" show-icon><el-button text type="primary" :disabled="detailLoading" @click="refreshCurrentOrder">重新加载</el-button></el-alert>
      <p v-else-if="!logs.length && !detailLoading" class="od-detail-empty">暂无状态流水</p>
      <el-timeline>
        <el-timeline-item
          v-for="log in logs"
          :key="log.id"
          :timestamp="formatDateTime(log.created_at)"
        >
          {{ log.action_display }}
          <span v-if="log.from_status && log.to_status && log.from_status !== log.to_status">（{{ ORDER_STATUS[log.from_status]?.label || log.from_status }} → {{ ORDER_STATUS[log.to_status]?.label || log.to_status }}）</span>
          <span v-if="log.reason"> · {{ log.reason }}</span>
          <span v-if="log.operator_name" class="od-log-op"> · {{ log.operator_name }}</span>
        </el-timeline-item>
      </el-timeline>
    </template>
    </template>
    <template #footer>
      <div v-if="current && current.status === 'PENDING' && auth.hasPerm('order:dispatch')" class="od-detail-actions">
        <span v-if="detailLoading || detailFailed" class="od-detail-action-note">{{ detailLoading ? '订单更新中' : '请重新加载订单后操作' }}</span>
        <span v-else-if="!canAssignOrder(current)" class="od-detail-action-note">暂不可派，请核对订单状态</span>
        <el-button :disabled="detailLoading" @click="refreshCurrentOrder">刷新</el-button>
        <el-button type="primary" :disabled="detailLoading || detailFailed || !canAssignOrder(current) || assigning" @click="onDetailAssign">指派陪玩</el-button>
      </div>
    </template>
  </el-drawer>

  <el-dialog v-model="refundVisible" :title="refundTarget?.ticketId ? '售后退款 · 资金复核' : '订单退款 · 资金复核'" width="720px" class="ap-modal" :close-on-click-modal="false" :close-on-press-escape="!refundSubmitting" :show-close="!refundSubmitting">
    <el-descriptions v-if="refundLimits" :column="refundCompact ? 1 : 3" border class="od-refund-summary">
      <el-descriptions-item label="原实付">{{ amountToXaCoin(refundLimits.original) }} 币</el-descriptions-item>
      <el-descriptions-item label="累计已退">{{ amountToXaCoin(refundLimits.refunded) }} 币</el-descriptions-item>
      <el-descriptions-item label="剩余可退">{{ amountToXaCoin(refundLimits.remaining) }} 币</el-descriptions-item>
    </el-descriptions>
    <el-form label-position="top" class="od-refund-reason">
      <el-form-item label="本次退款金额（兴安币，最多 1 位小数）">
        <el-input v-model="refundAmountCoins" inputmode="decimal" :disabled="refundSubmitting || refundResultUnknown || !refundLimits" placeholder="请输入本次退款金额">
          <template #append>币</template>
        </el-input>
        <el-button text type="primary" :disabled="refundSubmitting || refundResultUnknown || !refundLimits" @click="useRefundRemainder">填入全部余款</el-button>
      </el-form-item>
    </el-form>
    <el-skeleton v-if="refundLoading" :rows="5" animated />
    <template v-else>
      <el-alert v-if="refundError" :title="refundError" type="warning" :closable="false" show-icon />
      <template v-if="refundPreview">
        <el-descriptions :column="refundCompact ? 1 : 2" border class="od-refund-summary">
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
        <el-input v-model="refundReason" type="textarea" :rows="3" :maxlength="refundTarget?.ticketId ? 1000 : 255" show-word-limit :disabled="refundSubmitting || refundResultUnknown" />
        </el-form-item>
      </el-form>
    </template>
    <template #footer>
      <el-button v-if="refundResultUnknown" @click="reviewUnknownRefund">关闭并查看订单</el-button>
      <el-button v-else :disabled="refundSubmitting" @click="refundVisible = false">取消</el-button>
      <el-button :loading="refundLoading" :disabled="refundSubmitting || refundResultUnknown" @click="loadRefundPreview">刷新试算</el-button>
      <el-button type="danger" :loading="refundSubmitting" :disabled="!refundCanSubmit" @click="submitRefund">{{ refundPreview?.full_refund ? (refundPreview.refunded_amount ? '确认退还余款' : '确认全额退款') : '确认本次退款' }}</el-button>
    </template>
  </el-dialog>

  <DispatchMessageDialog v-model="dispatchMessageVisible" :order-id="dispatchMessageOrderId" />
  <el-dialog v-model="timeoutConfigVisible" title="待接单时限设置" width="560px" class="ap-modal" destroy-on-close :before-close="closeTimeoutConfig" :close-on-click-modal="false" :close-on-press-escape="!timeoutConfigSaving" :show-close="!timeoutConfigSaving"><TimeoutConfig ref="timeoutConfigRef" @busy="timeoutConfigSaving = $event" /></el-dialog>
  <el-dialog v-model="trafficConfigVisible" title="峰谷限流与监控" width="960px" class="ap-modal" destroy-on-close :before-close="closeTrafficConfig" :close-on-click-modal="false" :close-on-press-escape="!trafficConfigSaving" :show-close="!trafficConfigSaving"><TrafficConfig ref="trafficConfigRef" @busy="trafficConfigSaving = $event" /></el-dialog>
  <el-dialog v-model="dispatchConfigVisible" title="KOOK 派单配置" width="1100px" class="ap-modal" destroy-on-close :before-close="closeDispatchConfig" :close-on-click-modal="false">
    <KookDispatchConfig ref="dispatchConfigRef" />
  </el-dialog>
  <el-dialog v-model="dispatchVisible" title="客服快捷派单" width="800px" class="ap-modal od-dispatch-dialog"
    :close-on-click-modal="false" :close-on-press-escape="!dispatching && !dispatchConfirming && !dispatchResultUnknown" :show-close="!dispatching && !dispatchConfirming && !dispatchResultUnknown">
    <el-form class="ap-modal-form ap-form-grid" label-position="top" :disabled="dispatching || dispatchConfirming || dispatchResultUnknown" :inert="dispatchConfirming || undefined">
      <div class="od-form-section">
        <div class="od-dispatch-heading"><p class="od-section-title">下单信息</p></div>
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
                  :label="`${u.nickname || u.username} · 账号 ${u.username || '未记录'}（查询时余额 ${amountToXaCoin(u.wallet_balance)}币）`"
                  :value="u.id"
                />
              </el-select>
              <span v-if="bossSearchError" class="hint">{{ bossSearchError }} <el-button link type="primary" @click="searchBoss(bossSearchKeyword)">重新搜索</el-button></span>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="服务">
              <el-select
                v-model="dispatchForm.service_id"
                filterable
                placeholder="选择服务"
                :loading="serviceLoading"
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
        <div class="od-dispatch-heading"><p class="od-section-title">陪玩与抽成</p></div>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="局数">
              <el-input-number v-model="dispatchForm.game_rounds" :min="1" :step="1" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="套餐陪玩人数" class="ap-field--full">
              <el-tag effect="light">{{ dispatchProviderCount === 2 ? '双陪 · 2 人' : dispatchProviderCount === 1 ? '单陪 · 1 人' : '请先选择服务套餐' }}</el-tag>
              <span class="hint">{{ dispatchProviderCount === 2 ? '须指定两名不同陪玩' : dispatchProviderCount === 1 ? '留空则进入待接单大厅' : '人数由套餐确定' }}</span>
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item v-if="currentService?.service_type === 'COMPANION'" label="陪玩选择"><el-select v-model="dispatchForm.provider_gender" @change="onDispatchGenderChange"><el-option label="不限" value="ANY"/><el-option label="男陪" value="MALE"/><el-option label="女陪" value="FEMALE"/></el-select></el-form-item>
        <el-alert v-if="currentService?.is_gift" type="info" :closable="false" title="礼物沿用独立比例规则，不使用订单自定义抽成。" />
        <el-form-item v-else class="ap-field--full">
          <template #label><el-tooltip :content="dispatchQuote?.commission_locked ? '沿用自动规则；选择百分比或固定金额可覆盖本单抽成' : '百分比按陪玩设置；固定金额按整单设置'" placement="top" :trigger="['hover', 'focus', 'click']"><button type="button" class="od-label-help">本单抽成方式 <el-icon><InfoFilled /></el-icon></button></el-tooltip></template>
          <el-radio-group v-model="dispatchCommissionMode" :disabled="dispatching">
            <el-radio-button value="AUTO">沿用配置</el-radio-button>
            <el-radio-button value="PERCENT">百分比</el-radio-button>
            <el-radio-button value="FIXED">整单固定金额</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="dispatchCommissionMode === 'FIXED' && !currentService?.is_gift" label="整单固定抽成（兴安币）" class="ap-field--full" :error="dispatchCommissionError">
          <el-input-number v-model="dispatchCommissionFixed" :disabled="dispatching" :min="0" :max="214748364.7" :precision="1" :step="0.1" controls-position="right" />
          <span class="hint">整单扣一次，双陪分摊；0 表示不抽成</span>
        </el-form-item>
        <p v-if="dispatchQuote" class="od-provider-tip">{{ dispatchQuote.commission_source_label || '系统试算' }}<template v-if="dispatchFixed"> · 整单固定抽成 {{ amountToXaCoin(dispatchQuote.commission_fixed_total) }} 币（已按折后实付封顶）</template></p>
        <el-alert v-if="dispatchForm.escort_mode === 'DOUBLE' && dispatchForm.providers[0]?.provider_id && dispatchForm.providers[0]?.provider_id === dispatchForm.providers[1]?.provider_id"
          type="warning" :closable="false" show-icon title="双陪需选择两名不同的陪玩" description="请更换其中一名陪玩，再核对金额并派单。" />
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
                    :label="`${assignProviderLabel(p)} · ${p.escort_no || '编号未记录'} · ${(p.qualifications || []).map((q: any) => q.label).join(' / ') || '资格待配置'}`"
                    :value="p.user"
                  />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col v-if="!dispatchFixed" :span="8">
              <el-form-item :label="dispatchPendingPercentage() ? '待接单陪玩抽成比例（%）' : '抽成比例（%）'">
                <el-input-number
                  v-if="!dispatchPendingPercentage()"
                  :key="`provider-rate-${idx}-${dispatchRateDisabled(slot)}`"
                  v-model="slot.commission_rate"
                  :disabled="dispatchRateDisabled(slot)"
                  :min="0"
                  :max="100"
                  :precision="0"
                  :step="1"
                  controls-position="right"
                  style="width: 100%"
                />
                <el-input-number v-else :key="`pending-rate-${dispatchPendingRateDisabled}`" v-model="dispatchPendingRate" :disabled="dispatchPendingRateDisabled" :min="0" :max="100" :precision="0" :step="1" controls-position="right" style="width: 100%" />
              </el-form-item>
            </el-col>
          </el-row>
          <p v-if="slot.suggestion_error && !dispatchFixed && !dispatchQuote?.commission_locked" class="od-provider-tip">{{ slot.suggestion_error }} <el-button link type="primary" @click="onProviderPicked(idx)">重新获取</el-button></p>
          <p v-if="slot.provider_id" class="od-provider-tip">
            <template v-if="dispatchQuote && !slot.suggestion_loading && (dispatchFixed ? dispatchProviderShare(slot) : !commissionError('PERCENT', slot.commission_rate, 0))">预计实得 {{ amountToXaCoin(providerIncome(slot)) }} 币（{{ providerIncomeDesc(slot) }}）</template>
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
        <p v-if="providerSearchError" class="hint">{{ providerSearchError }} <el-button link type="primary" @click="searchProvider(providerSearchKeyword)">重新搜索</el-button></p>

        <el-form-item label="备注" class="ap-field--full">
          <el-input v-model="dispatchForm.remark" type="textarea" :rows="2" placeholder="可选" />
        </el-form-item>
      </div>

    </el-form>
    <template #footer>
      <div class="od-dispatch-footer">
      <el-alert v-if="serviceLoadError" class="od-dispatch-alert" type="error" :closable="false" show-icon :title="serviceLoadError"><el-button link type="primary" :disabled="dispatching || serviceLoading || dispatchResultUnknown" @click="loadDispatchServices">重新加载服务</el-button></el-alert>
      <el-alert v-if="dispatchSubmitNotice" class="od-dispatch-alert" :type="dispatchResultUnknown ? 'warning' : 'info'" :closable="false" show-icon :title="dispatchSubmitNotice">
        <el-button v-if="dispatchResultUnknown" link type="primary" :disabled="dispatching" @click="closeDispatchAndRefresh">关闭并刷新订单</el-button>
      </el-alert>
      <div class="od-summary" :class="{ 'od-summary--danger': insufficient }" aria-live="polite">
        <div class="od-summary-item">
          <el-tooltip content="余额为选中老板时的查询快照，实际扣款前服务端会再次校验。" placement="top" :trigger="['hover', 'focus', 'click']"><button type="button" class="od-summary-label od-label-help">老板余额 <el-icon><InfoFilled /></el-icon></button></el-tooltip>
          <span class="od-summary-value">{{ currentBoss ? `${amountToXaCoin(currentBoss.wallet_balance)}币` : '—' }}</span>
        </div>
        <div class="od-summary-item">
          <el-tooltip content="金额来自服务端当前试算，已考虑适用的折扣。提交时服务端会再次核对；这不是平台利润。" placement="top" popper-class="od-help-tooltip" :trigger="['hover', 'focus', 'click']">
            <button type="button" class="od-summary-label od-label-help">折后预计扣款 <el-icon><InfoFilled /></el-icon></button>
          </el-tooltip>
          <span class="od-summary-value">{{ quoteLoading ? '计算中…' : dispatchQuote ? `${amountToXaCoin(estimateAmount)}币` : '—' }}</span>
        </div>
        <div v-if="insufficient" class="od-summary-warn">余额不足以支付本次派单</div>
        <div v-else-if="!dispatchForm.customer_id || !dispatchForm.service_id" class="od-summary-note">选择老板和服务后开始试算</div>
        <div v-else-if="quoteLoading" class="od-summary-note">正在获取服务端试算</div>
        <div v-else-if="!dispatchQuote" class="od-summary-note">{{ quoteError || '试算未完成，请检查输入' }} <el-button v-if="!dispatchCommissionError" link type="primary" :disabled="dispatching || dispatchResultUnknown" @click="refreshDispatchQuote">重新试算</el-button></div>
      </div>
      <div class="od-dispatch-actions">
        <el-button v-if="!dispatchResultUnknown" :disabled="dispatching || dispatchConfirming" @click="dispatchVisible = false">取消</el-button>
        <el-button type="primary" :loading="dispatching" :disabled="dispatchResultUnknown || dispatchConfirming" @click="onDispatchSave">核对派单</el-button>
      </div>
      </div>
    </template>
  </el-dialog>

  <el-dialog v-model="assignVisible" title="指派待接单订单" width="480px" class="ap-modal"
    :close-on-click-modal="false" :close-on-press-escape="!assigning" :show-close="!assigning">
    <el-form  class="ap-modal-form ap-form-grid" label-position="top">
      <div v-if="assignOrder" class="od-assign-order">
        <div><span>订单号</span><strong>{{ assignOrder.order_no || '待核对' }}</strong></div>
        <div><span>项目</span><strong>{{ assignOrder.service_name_snapshot || '待核对' }}</strong></div>
      </div>
      <div v-if="assignOrder" class="od-assign-context">
        <div><span>老板</span><strong>{{ assignOrder.customer_name || '待核对' }}</strong><small>{{ customerAccountLabel(assignOrder) }}</small></div>
        <div><span>人数</span><strong>{{ orderTeamSize(assignOrder) === 2 ? '双陪 · 2 人' : orderTeamSize(assignOrder) === 1 ? '单陪 · 1 人' : '待核对' }}</strong></div>
      </div>
      <el-form-item v-if="isStaffTeamAssign(assignOrder)" label="陪玩（选择两名）">
        <el-select
          v-model="assignProviderIds"
          multiple
          :multiple-limit="2"
          filterable
          remote
          reserve-keyword
          placeholder="输入昵称或账号，选择两名不同的陪玩"
          :remote-method="searchAssignProvider"
          popper-class="od-assign-options"
          :loading="assignProviderLoading"
          :disabled="assigning"
          style="width: 100%"
        >
          <el-option v-for="p in assignProviderOptions" :key="p.user" :label="assignCandidateLabel(p)" :value="p.user">
            <div class="od-candidate"><div class="od-candidate-main"><strong>{{ p.display_name || p.username || '陪玩' }}</strong><span>{{ p.escort_no || '编号未记录' }} · {{ assignCandidateStatus(p) }}</span></div><div class="od-candidate-account">账号 {{ p.username || '未记录' }}</div></div>
          </el-option>
        </el-select>
        <span class="hint">状态为搜索时的记录，提交时重新核验两人的资格、档期和名额。</span>
      </el-form-item>
      <el-form-item v-else label="陪玩">
        <el-select
          v-model="assignProviderId"
          filterable
          remote
          reserve-keyword
          placeholder="输入昵称或账号搜索陪玩"
          :remote-method="searchAssignProvider"
          popper-class="od-assign-options"
          :loading="assignProviderLoading"
          :disabled="assigning"
          style="width: 100%"
        >
          <el-option
            v-for="p in assignProviderOptions"
            :key="p.user"
            :label="assignCandidateLabel(p)"
            :value="p.user"
          ><div class="od-candidate"><div class="od-candidate-main"><strong>{{ p.display_name || p.username || '陪玩' }}</strong><span>{{ p.escort_no || '编号未记录' }} · {{ assignCandidateStatus(p) }}</span></div><div class="od-candidate-account">账号 {{ p.username || '未记录' }}</div></div></el-option>
        </el-select>
        <span class="hint">状态为搜索时的记录，提交时重新核验资格、档期和名额。</span>
      </el-form-item>
      <section v-if="assignSelectedProviders.length" class="od-assign-selected" aria-label="已选择的陪玩">
        <strong>已选 {{ assignSelectedProviders.length }} 人</strong>
        <div v-for="person in assignSelectedProviders" :key="person.id" class="od-assign-selected-row">
          <span>{{ person.label }}</span>
        </div>
      </section>
      <el-alert v-if="assignSearchError" type="error" :closable="false" show-icon :title="assignSearchError">
        <el-button link type="primary" :disabled="assigning || assignProviderLoading" @click="searchAssignProvider(assignSearchKeyword)">重新搜索</el-button>
      </el-alert>
      <el-alert v-if="assignSubmitError" type="warning" :closable="false" show-icon :title="assignSubmitError">
        <el-button link type="primary" :disabled="assigning" @click="closeAssignAndRefresh">关闭并刷新订单</el-button>
      </el-alert>
      <el-form-item v-if="isStaffPending(assignOrder)" label="待派截止时间">
        <span>{{ assignOrder?.auto_cancel_at ? `${formatDateTime(assignOrder.auto_cancel_at)}（到期后不能派单）` : '截止时间未记录，请核对订单详情' }}</span>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button :disabled="assigning" @click="assignVisible = false">取消</el-button>
      <el-button type="primary" :loading="assigning" :disabled="!!assignSubmitError" @click="onAssignSave">确认派单</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="transferVisible" title="订单转单" width="880px" class="ap-modal" :close-on-click-modal="!transferLocked" :close-on-press-escape="!transferLocked" :show-close="!transferLocked">
    <template v-if="transferOrder">
      <p class="od-transfer-order">{{ transferOrder.order_no }} · {{ transferOrder.service_name_snapshot || '服务待核对' }}</p>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="旧陪玩保留填写的份额，其余转给新陪玩。"
        description="收益入账时间以订单结算模式为准；罚款余额不足时整笔转单失败。"
        style="margin-bottom: 16px"
      />
      <div v-for="row in transferRows" :key="row.old_provider_id" class="od-provider-row">
        <el-checkbox v-model="row.enabled" class="od-transfer-choice" :disabled="transferLocked" style="margin-bottom: 8px">
          转出：{{ row.old_provider_label }}（{{ row.pool === null ? '份额待核对' : `可分配 ${amountToXaCoin(row.pool)} 币` }}）
        </el-checkbox>
        <el-row v-if="row.enabled" :gutter="12">
          <el-col :span="8">
            <el-form-item label="转入陪玩" label-position="top">
              <el-select
                v-model="row.new_provider_id"
                filterable
                remote
                reserve-keyword
                placeholder="搜索可接单陪玩"
                :remote-method="searchTransferProvider"
                popper-class="od-assign-options"
                :loading="transferProviderLoading"
                :disabled="transferLocked"
                style="width: 100%"
              >
                <el-option
                  v-for="p in transferProviderOptions"
                  :key="p.user"
                  :label="assignCandidateLabel(p)"
                  :value="p.user"
                ><div class="od-candidate"><div class="od-candidate-main"><strong>{{ p.display_name || p.username || '陪玩' }}</strong><span>{{ p.escort_no || '编号未记录' }} · {{ assignCandidateStatus(p) }}</span></div><div class="od-candidate-account">账号 {{ p.username || '未记录' }}</div></div></el-option>
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="5">
            <el-form-item label="份额方式" label-position="top">
              <el-select v-model="row.split_type" :disabled="transferLocked" style="width: 100%">
                <el-option label="按比例" value="PERCENT" />
                <el-option label="固定额" value="FIXED" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="5">
            <el-form-item :label="row.split_type === 'FIXED' ? '旧陪玩保留（币）' : '旧陪玩保留（%）'" label-position="top" :error="transferAttempted && (row.split_type === 'FIXED' ? transferCoinRaw(row.split_fixed_yuan) === null : !validTransferPercent(row.split_percent)) ? (row.split_type === 'FIXED' ? '请输入非负金额，最多一位小数' : '请输入 0–100 的整数') : ''">
              <el-input-number
                v-if="row.split_type === 'PERCENT'"
                v-model="row.split_percent"
                :min="0"
                :max="100"
                :step="1"
                :precision="0"
                :disabled="transferLocked"
                controls-position="right"
                style="width: 100%"
              />
              <el-input
                v-else
                v-model="row.split_fixed_yuan"
                :disabled="transferLocked"
                inputmode="decimal"
                maxlength="12"
                style="width: 100%"
              />
              <span v-if="row.split_type === 'PERCENT' && validTransferPercent(row.split_percent)" class="hint">旧陪玩 {{ row.split_percent }}% · 新陪玩 {{ 100 - row.split_percent }}%</span>
              <span v-if="row.split_type === 'FIXED' && row.pool !== null && transferCoinRaw(row.split_fixed_yuan) !== null && transferCoinRaw(row.split_fixed_yuan)! > row.pool" class="hint">超过当前可分配份额，提交时按 {{ amountToXaCoin(row.pool) }} 币上限处理。</span>
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="罚款（币）" label-position="top" :error="transferAttempted && transferCoinRaw(row.penalty_yuan) === null ? '请输入非负金额，最多一位小数' : ''">
              <el-input
                v-model="row.penalty_yuan"
                :disabled="transferLocked"
                inputmode="decimal"
                maxlength="12"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
        </el-row>
      </div>
      <el-form-item label="转单原因" class="ap-field--full">
        <el-input v-model="transferReason" :disabled="transferLocked" type="textarea" :rows="2" maxlength="255" show-word-limit placeholder="可选，将记入订单流水" />
      </el-form-item>
      <p class="hint">搜索结果不代表已保留名额，提交时会重新核验转入陪玩的资格和接单状态。</p>
      <el-alert v-if="transferSearchError" type="error" :closable="false" show-icon :title="transferSearchError">
        <el-button link type="primary" :disabled="transferLocked || transferProviderLoading" @click="searchTransferProvider(transferSearchKeyword)">重新搜索</el-button>
      </el-alert>
      <el-alert v-if="transferSubmitError" type="warning" :closable="false" show-icon :title="transferSubmitError">
        <el-button link type="primary" :disabled="transferLocked" @click="closeTransferAndRefresh">关闭并刷新订单</el-button>
      </el-alert>
    </template>
    <template #footer>
      <el-button :disabled="transferLocked" @click="transferVisible = false">取消</el-button>
      <el-button type="primary" :loading="transferring" :disabled="!!transferSubmitError || transferConfirming" @click="onTransferSave">核对转单</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, h, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { InfoFilled, Plus, Search, Setting } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import DispatchMessageDialog from '@/components/DispatchMessageDialog.vue'
import { orderApi, aftersaleApi, escortApi } from '@/api/modules'
import { request } from '@/api/request'
import { ORDER_STATUS, PAYMENT_STATUS, TX_TYPE } from '@/utils/dict'
import { amountToXaCoin, formatDateTime, xaCoinToAmount } from '@/utils/format'
import { commissionError } from '@/utils/commission'
import { currentProviderIdentities, providerListText } from '@/utils/orderParticipants'
import { useAuthStore } from '@/stores/auth'
import { useStaffDispatchQueue } from '@/stores/staffDispatchQueue'
import { adminWs } from '@/utils/ws'
import { useRoute, useRouter } from 'vue-router'

const auth = useAuthStore()
const staffQueue = useStaffDispatchQueue()
const route = useRoute()
const router = useRouter()
const canReadOrders = computed(() => Boolean(auth.token) && auth.hasPerm('order:view'))
const KookDispatchConfig = defineAsyncComponent(() => import('@/views/kook-dispatch/index.vue'))
const trafficConfigVisible = ref(false)
const trafficConfigSaving = ref(false)
const trafficConfigRef = ref<{ confirmClose: () => Promise<boolean> } | null>(null)
const TrafficConfig = defineAsyncComponent(() => import('./TrafficConfig.vue'))
const timeoutConfigVisible = ref(false)
const timeoutConfigSaving = ref(false)
const timeoutConfigRef = ref<{ confirmClose: () => Promise<boolean> } | null>(null)
const TimeoutConfig = defineAsyncComponent(() => import('./TimeoutConfig.vue'))
const dispatchConfigVisible = ref(false)
const dispatchConfigRef = ref<{ confirmClose: () => Promise<boolean> } | null>(null)
let closingTimeout = false, closingDispatchConfig = false, closingTraffic = false
async function closeTrafficConfig(done: () => void) {
  if (closingTraffic || trafficConfigSaving.value) return
  closingTraffic = true
  try { if (!trafficConfigRef.value || await trafficConfigRef.value.confirmClose()) done() }
  finally { closingTraffic = false }
}
async function closeTimeoutConfig(done: () => void) {
  if (closingTimeout || timeoutConfigSaving.value) return
  closingTimeout = true
  try { if (!timeoutConfigRef.value || await timeoutConfigRef.value.confirmClose()) done() }
  finally { closingTimeout = false }
}
async function closeDispatchConfig(done: () => void) {
  if (closingDispatchConfig) return
  closingDispatchConfig = true
  try { if (!dispatchConfigRef.value || await dispatchConfigRef.value.confirmClose()) done() }
  finally { closingDispatchConfig = false }
}
const dispatchMessageVisible = ref(false)
const dispatchMessageOrderId = ref<number>()
function onDispatchMessage(order: any) {
  if (!auth.hasPerm('order:dispatch') || order?.dispatch_message_allowed !== true) {
    ElMessage.warning(order?.dispatch_message_unavailable_reason || '当前订单不能发单，请核对订单状态')
    return
  }
  dispatchMessageOrderId.value = order.id
  dispatchMessageVisible.value = true
}
const tableRef = ref()
const query = reactive<Record<string, any>>({
  order_no: '',
  order_no_exact: '',
  customer_username: '',
  customer_nickname: '',
  customer_phone: '',
  customer_account_id: '', created_day: '',
  status: '',
  payment_status: '',
  dispatch_route: '',
  actionable_staff: '',
  aftersale_open: '',
})
const advancedFiltersOpen = ref(false)
function validCustomerId(value: unknown): value is string {
  return typeof value === 'string' && /^[1-9]\d{0,15}$/.test(value) && Number.isSafeInteger(Number(value))
}
function validCreatedDay(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const year = Number(value.slice(0, 4))
  const timestamp = Date.parse(`${value}T00:00:00Z`)
  return year >= 1000 && year <= 9998 && Number.isFinite(timestamp) && new Date(timestamp).toISOString().slice(0, 10) === value
}
function hasOrderRouteOverride() {
  const queue = route.query.queue
  const orderNo = route.query.find_order
  const status = route.query.order_status
  return (typeof queue === 'string' && ['staff', 'staff_review', 'paid_pending', 'paid_pending_staff', 'paid_pending_public', 'unpaid_pending', 'open_aftersale'].includes(queue))
    || (typeof orderNo === 'string' && Boolean(orderNo.trim()))
    || (typeof status === 'string' && Object.prototype.hasOwnProperty.call(ORDER_STATUS, status))
}
const invalidCustomerFilter = computed(() => route.query.find_customer !== undefined && !validCustomerId(route.query.find_customer) && route.query.created_day === undefined && !hasOrderRouteOverride())
const invalidCreatedDay = computed(() => route.query.created_day !== undefined && !validCreatedDay(route.query.created_day) && !hasOrderRouteOverride())
function clearInvalidCustomerFilter() {
  const remaining = { ...route.query }
  delete remaining.find_customer
  void router.replace({ query: remaining })
}
function clearInvalidCreatedDay() {
  const remaining = { ...route.query }
  delete remaining.created_day
  void router.replace({ query: remaining })
}
const advancedFilterCount = computed(() => ['customer_nickname', 'customer_phone', 'status', 'payment_status', 'dispatch_route'].filter(key => Boolean(query[key])).length)
const hasOrderFilters = computed(() => Object.values(query).some(value => Boolean(value)))
const exactFilterUnsupported = ref(false)
const customerFilterUnsupported = ref(false)
const createdDayUnsupported = ref(false)
const createdDayTimezone = ref('')
const staffQueueDeadlineOrder = ref(false)
const aftersaleQueueOrder = ref(false)
const textFilterKeys = ['order_no', 'customer_username', 'customer_nickname', 'customer_phone'] as const
const requestedTextFilters = ref<Record<string, string> | null>(null)
const searchInputsPending = computed(() => requestedTextFilters.value !== null && textFilterKeys.some(key => String(query[key] || '') !== requestedTextFilters.value?.[key]))
let orderFetchSequence = 0
let pendingDeadlineTimer: ReturnType<typeof setTimeout> | null = null
function pendingDeadlineDelay(orders: any[], now = Date.now()): number | null {
  const deadlines = orders.filter(order => order?.status === 'PENDING' && order.payment_status === 'PAID'
    && (order.dispatch_assignment_allowed === true || order.dispatch_message_allowed === true))
    .map(order => Date.parse(order.auto_cancel_at || '')).filter(Number.isFinite)
  if (!deadlines.length) return null
  const remaining = Math.min(...deadlines) - now
  return remaining <= 0 ? 60000 : Math.min(Math.max(remaining + 500, 1000), 60000)
}
function schedulePendingDeadline(orders: any[]) {
  if (pendingDeadlineTimer !== null) clearTimeout(pendingDeadlineTimer)
  pendingDeadlineTimer = null
  if (pageDisposed || !canReadOrders.value || document.hidden) return
  const delay = pendingDeadlineDelay(orders)
  if (delay === null) return
  // The local clock schedules a re-read; only the server decides availability.
  pendingDeadlineTimer = setTimeout(() => {
    pendingDeadlineTimer = null
    if (!pageDisposed && !document.hidden) requestOrderRefresh()
  }, delay)
}
async function fetchOrders(params: Record<string, any>) {
  const sequence = ++orderFetchSequence
  requestedTextFilters.value = Object.fromEntries(textFilterKeys.map(key => [key, String(params[key] || '')]))
  const customerId = params.customer_account_id
  const day = params.created_day
  if (query.customer_account_id === customerId) customerFilterUnsupported.value = false
  if (query.created_day === day) { createdDayUnsupported.value = false; createdDayTimezone.value = '' }
  if (query.actionable_staff === params.actionable_staff) staffQueueDeadlineOrder.value = false
  if (query.aftersale_open === params.aftersale_open && query.actionable_staff === params.actionable_staff) aftersaleQueueOrder.value = false
  const response = await orderApi.list(params, true)
  const exact = params.order_no_exact
  if (sequence === orderFetchSequence && query.actionable_staff === params.actionable_staff) {
    staffQueueDeadlineOrder.value = params.actionable_staff === '1' && (response.data as any)?.staff_queue_order === 'deadline_asc'
  }
  if (sequence === orderFetchSequence && query.aftersale_open === params.aftersale_open && query.actionable_staff === params.actionable_staff) {
    aftersaleQueueOrder.value = params.aftersale_open === '1' && params.actionable_staff !== '1' && (response.data as any)?.aftersale_queue_order === 'opened_asc'
  }
  if (day) {
    const applied = (response.data as any)?.created_day_applied
    if (applied?.date !== day || typeof applied?.timezone !== 'string' || !applied.timezone) {
      if (sequence === orderFetchSequence && query.created_day === day) createdDayUnsupported.value = true
      throw new Error('服务端未确认订单日期筛选')
    }
    if (sequence === orderFetchSequence && query.created_day === day) createdDayTimezone.value = applied.timezone
  }
  if (customerId && (response.data as any)?.customer_account_id_applied !== String(customerId)) {
    if (sequence === orderFetchSequence && query.customer_account_id === customerId) customerFilterUnsupported.value = true
    throw new Error('服务端未确认老板订单筛选')
  }
  if (sequence === orderFetchSequence && query.customer_account_id === customerId) customerFilterUnsupported.value = false
  if (exact && (response.data as any)?.order_no_exact_applied !== true) {
    if (sequence === orderFetchSequence && query.order_no_exact === exact) exactFilterUnsupported.value = true
    throw new Error('服务端未确认订单号精确筛选')
  }
  if (sequence === orderFetchSequence && query.order_no_exact === exact) exactFilterUnsupported.value = false
  if (sequence === orderFetchSequence) schedulePendingDeadline((response.data as any)?.list || [])
  return response
}
function changeOrderNumber(value: string | number) {
  if (query.order_no_exact && value !== query.order_no_exact) query.order_no_exact = ''
}
function clearExactOrder(reload: () => void) { query.order_no = ''; query.order_no_exact = ''; reload() }

function isStaffPendingOrder(order: any) {
  return order?.dispatch_route === 'STAFF' && order.status === 'PENDING' && order.payment_status === 'PAID'
}
function isStaffPending(order: any) {
  return isStaffPendingOrder(order) && order.staff_dispatch_actionable === true
}

const dispatchRouteHelp = '接单路径是订单记录的路径，不代表订单当前仍在公开接单，也不能单独判断实际由谁指派；请查看陪玩和状态流水。'
const customerIdentityHelp = '昵称和账号为当前关联资料，可能与下单时不同；订单详情中的手机号为下单时记录。'
function reservationDate(value: string): string {
  const time = new Date(value)
  if (Number.isNaN(time.getTime())) return '—'
  const local = new Date(time.getTime() + 8 * 60 * 60 * 1000)
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${local.getUTCFullYear()}-${pad(local.getUTCMonth() + 1)}-${pad(local.getUTCDate())} ${pad(local.getUTCHours())}:${pad(local.getUTCMinutes())}`
}
function customerAccountLabel(order: any) {
  return order?.customer_username ? `账号 ${order.customer_username}` : '账号待核对'
}

function dispatchRouteLabel(order: any) {
  if (order?.dispatch_route === 'STAFF') return '客服派单'
  if (order?.dispatch_route === 'PUBLIC') return '公开接单'
  return '—'
}

function showStaffPending(reload: () => void) {
  Object.assign(query, {
    order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: '',
    status: 'PENDING', payment_status: 'PAID', dispatch_route: 'STAFF', actionable_staff: '1', aftersale_open: '',
  })
  reload()
}

function clearActionableFilter(reload: () => void) {
  query.actionable_staff = ''
  reload()
}

function clearStaffPending(reload: () => void) {
  Object.assign(query, { order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: '', status: '', payment_status: '', dispatch_route: '', actionable_staff: '', aftersale_open: '' })
  reload()
}

function showStaffReview(reload: () => void) {
  Object.assign(query, {
    order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: '',
    status: 'PENDING', payment_status: 'PAID', dispatch_route: 'STAFF', actionable_staff: '', aftersale_open: '',
  })
  reload()
}

watch(() => route.query.queue, queue => {
  if (queue !== 'staff' && queue !== 'staff_review' && queue !== 'paid_pending' && queue !== 'paid_pending_staff' && queue !== 'paid_pending_public' && queue !== 'unpaid_pending' && queue !== 'open_aftersale') return
  if (queue === 'staff_review') showStaffReview(() => tableRef.value?.reload())
  else if (queue === 'paid_pending' || queue === 'paid_pending_staff' || queue === 'paid_pending_public') {
    const route = queue === 'paid_pending_staff' ? 'STAFF' : queue === 'paid_pending_public' ? 'PUBLIC' : ''
    Object.assign(query, { order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: '', status: 'PENDING', payment_status: 'PAID', dispatch_route: route, actionable_staff: '', aftersale_open: '' })
    tableRef.value?.reload()
  }
  else if (queue === 'unpaid_pending') {
    Object.assign(query, { order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: '', status: 'PENDING', payment_status: 'UNPAID', dispatch_route: '', actionable_staff: '', aftersale_open: '' })
    tableRef.value?.reload()
  }
  else if (queue === 'open_aftersale') {
    Object.assign(query, { order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: '', status: '', payment_status: '', dispatch_route: '', actionable_staff: '', aftersale_open: '1' })
    tableRef.value?.reload()
  }
  else showStaffPending(() => tableRef.value?.reload())
  const remaining = { ...route.query }
  delete remaining.queue
  delete remaining.find_customer
  delete remaining.created_day
  void router.replace({ query: remaining })
}, { immediate: true })

watch(() => route.query.find_order, orderNo => {
  if (typeof orderNo !== 'string' || !orderNo.trim()) return
  Object.assign(query, { order_no: orderNo.trim(), order_no_exact: orderNo.trim(), customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: '', status: '', payment_status: '', dispatch_route: '', actionable_staff: '', aftersale_open: '' })
  tableRef.value?.reload()
  const remaining = { ...route.query }
  delete remaining.find_order
  delete remaining.find_customer
  delete remaining.created_day
  void router.replace({ query: remaining })
}, { immediate: true })

watch(() => route.query.find_customer, customerId => {
  if (route.name !== 'orders' || hasOrderRouteOverride() || route.query.created_day !== undefined) return
  if (!validCustomerId(customerId)) {
    if (query.customer_account_id) { query.customer_account_id = ''; tableRef.value?.reload() }
    return
  }
  if (query.customer_account_id === customerId) return
  Object.assign(query, { order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: customerId, created_day: '', status: '', payment_status: '', dispatch_route: '', actionable_staff: '', aftersale_open: '' })
  tableRef.value?.reload()
}, { immediate: true })

watch([() => query.customer_account_id, () => route.query.created_day], ([customerId]) => {
  if (route.name !== 'orders' || hasOrderRouteOverride() || route.query.created_day !== undefined || route.query.find_customer === (customerId || undefined)) return
  const remaining = { ...route.query }
  if (customerId) remaining.find_customer = String(customerId)
  else delete remaining.find_customer
  void router.replace({ query: remaining })
})

watch(() => route.query.order_status, status => {
  if (typeof status !== 'string' || !Object.prototype.hasOwnProperty.call(ORDER_STATUS, status)) return
  Object.assign(query, { order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: '', status, payment_status: '', dispatch_route: '', actionable_staff: '', aftersale_open: '' })
  tableRef.value?.reload()
  const remaining = { ...route.query }
  delete remaining.order_status
  delete remaining.find_customer
  delete remaining.created_day
  void router.replace({ query: remaining })
}, { immediate: true })

watch(() => route.query.created_day, day => {
  if (route.name !== 'orders' || hasOrderRouteOverride()) return
  if (!validCreatedDay(day)) {
    if (query.created_day) { query.created_day = ''; tableRef.value?.reload() }
    return
  }
  if (query.created_day === day) return
  Object.assign(query, { order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: day, status: '', payment_status: '', dispatch_route: '', actionable_staff: '', aftersale_open: '' })
  tableRef.value?.reload()
  if (route.query.find_customer !== undefined) {
    const remaining = { ...route.query }
    delete remaining.find_customer
    void router.replace({ query: remaining })
  }
}, { immediate: true })

watch(() => query.created_day, day => {
  if (route.name !== 'orders' || hasOrderRouteOverride() || route.query.created_day === (day || undefined)) return
  const remaining = { ...route.query }
  if (day) remaining.created_day = String(day)
  else delete remaining.created_day
  void router.replace({ query: remaining })
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
const aftersaleFailed = ref(false)
const logsFailed = ref(false)
const detailOrderId = ref<number | string | null>(null)
const orderActionBusy = ref(false)
type RefundRecovery = { wallet_id: number; account_id: number | null; role: string; income_amount: number; held_amount: number; deposit_amount: number; available_required: number; available_balance: number; protected_deposit_amount?: number; shortfall: number; original_transaction_ids: number[]; source_safe?: boolean; explanation?: string }
type RefundPreview = { order_id: number; is_settled: boolean; eligible: boolean; original_amount: number; refunded_amount: number; remaining_amount: number; refund_amount: number; remaining_after_refund: number; full_refund: boolean; recovery_total: number; recoveries: RefundRecovery[]; shortfalls: { wallet_id: number; account_id: number | null; required: number; available: number; shortfall: number }[]; plan_token: string }
const refundVisible = ref(false)
const refundCompact = ref(false)
function updateRefundCompact() { refundCompact.value = typeof window !== 'undefined' && window.innerWidth <= 600 }
const refundLoading = ref(false)
const refundSubmitting = ref(false)
const refundResultUnknown = ref(false)
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
const refundCanSubmit = computed(() => Boolean(refundVisible.value && refundOperation?.current() && !refundLoading.value && !refundSubmitting.value && !refundResultUnknown.value
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
const assignProviderIds = ref<number[]>([])
const assignProviderOptions = ref<any[]>([])
const assignSelectedProviders = computed(() => {
  const ids = isStaffTeamAssign(assignOrder.value) ? assignProviderIds.value : (assignProviderId.value ? [assignProviderId.value] : [])
  return ids.map(id => {
    const person = assignProviderOptions.value.find(option => option.user === id)
    return { id, label: person ? assignCandidateLabel(person) : `陪玩账号 #${id} · 资料待核对` }
  })
})
const assignProviderLoading = ref(false)
const assignSearchKeyword = ref('')
const assignSearchError = ref('')
const assignSubmitError = ref('')
const assignReview = ref<{ id: number; orderNo: string } | null>(null)
const assignReviewLoading = ref(false)
const assignReviewChecked = ref(false)
let assignReviewOwnerKey = ''
let assignSearchSequence = 0

function assignReviewKey() {
  const id = auth.profile?.id
  return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_order_assign_review_${id}` : ''
}
function restoreAssignReview() {
  const key = assignReviewKey()
  if (key === assignReviewOwnerKey) return
  assignReviewOwnerKey = key
  assignReview.value = null
  assignReviewChecked.value = false
  assignReviewLoading.value = false
  if (!key) return
  try {
    const marker = JSON.parse(sessionStorage.getItem(key) || 'null')
    if (Number.isSafeInteger(marker?.id) && marker.id > 0 && typeof marker.orderNo === 'string') assignReview.value = marker
  } catch { /* In-page guard remains active when storage is unavailable. */ }
}
function beginAssignReview(id: number, orderNo: string) {
  assignReview.value = { id, orderNo }
  assignReviewChecked.value = false
  const key = assignReviewKey()
  if (key) try { sessionStorage.setItem(key, JSON.stringify({ id, orderNo })) } catch { /* In-page guard remains active. */ }
}
function clearAssignReview() {
  const key = assignReviewKey()
  if (key) try { sessionStorage.removeItem(key) } catch { /* Storage may be unavailable. */ }
  assignReview.value = null
  assignReviewChecked.value = false
}
async function viewAssignReview() {
  const review = assignReview.value
  if (!review || assignReviewLoading.value || assigning.value || !canReadOrders.value) return
  const revision = auth.sessionRevision, version = orderVersions.get(review.id) || 0
  assignReviewChecked.value = false
  assignReviewLoading.value = true
  try {
    await onDetail({ id: review.id })
    if (!pageDisposed && revision === auth.sessionRevision && assignReview.value === review && detailVisible.value
      && detailOrderId.value === review.id && !detailLoading.value && !detailFailed.value && !logsFailed.value
      && version === (orderVersions.get(review.id) || 0)) assignReviewChecked.value = true
  } finally {
    if (revision === auth.sessionRevision) assignReviewLoading.value = false
  }
}
async function finishAssignReview() {
  const review = assignReview.value, revision = auth.sessionRevision
  if (!review || !assignReviewChecked.value || assignReviewLoading.value || assigning.value) return
  try {
    await ElMessageBox.confirm('请确认已核对当前陪玩、状态流水及请求处理情况。请求仍可能在处理中。', '解除指派限制', {
      type: 'warning', confirmButtonText: '已核对，解除限制', cancelButtonText: '继续核对',
    })
  } catch { return }
  if (!pageDisposed && revision === auth.sessionRevision && assignReview.value === review && assignReviewChecked.value) clearAssignReview()
}
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreAssignReview, { immediate: true, flush: 'sync' })

async function onDetail(row: any) {
  if (pageDisposed || !canReadOrders.value) return
  clearOrderDetail()
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
  aftersaleFailed.value = logsFailed.value = false
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
  const [detail, tickets, history] = await Promise.allSettled([
    request({ url: `/orders/${orderId}/`, method: 'get', silent: true }),
    request({ url: '/aftersales/', method: 'get', params: { order: orderId }, silent: true }),
    request({ url: `/orders/${orderId}/logs/`, method: 'get', silent: true }),
  ])
  if (!isCurrentOrderDetail(sequence, revision, orderId)) return
  detailFailed.value = detail.status !== 'fulfilled' || String(detail.value.data?.id) !== String(orderId)
  if (!detailFailed.value && detail.status === 'fulfilled') current.value = detail.value.data
  aftersaleFailed.value = tickets.status !== 'fulfilled' || !Array.isArray(tickets.value.data?.list)
  if (!aftersaleFailed.value && tickets.status === 'fulfilled') aftersales.value = tickets.value.data.list
  logsFailed.value = history.status !== 'fulfilled' || !Array.isArray(history.value.data)
  if (!logsFailed.value && history.status === 'fulfilled') logs.value = history.value.data
  detailLoading.value = false
}

// Keep the table's filters/page; reconcile the drawer through its own HTTP reads.
function requestOrderRefresh(orderId?: number | string) {
  if (pageDisposed || !canReadOrders.value) return
  refreshPending = true
  if (detailVisible.value && detailOrderId.value && (orderId == null || orderId === detailOrderId.value)) {
    ++detailSequence
    detailRefreshPending = true
    detailLoading.value = true
  }
  scheduleOrderRefresh()
}

async function refreshAssignmentEligibility() {
  const snapshot = assignOrder.value
  if (!snapshot || !assignVisible.value || assigning.value || assignSubmitError.value) return
  const revision = auth.sessionRevision
  const stillCurrent = () => !pageDisposed && canReadOrders.value && auth.sessionRevision === revision
    && assignVisible.value && assignOrder.value === snapshot && !assigning.value && !assignSubmitError.value
  try {
    const result = await request({ url: `/orders/${snapshot.id}/`, method: 'get', silent: true })
    if (!stillCurrent()) return
    if (String(result.data?.id) !== String(snapshot.id) || !canAssignOrder(result.data)) {
      assignSubmitError.value = '订单已不可派，请关闭并刷新后核对'
    }
  } catch {
    if (stillCurrent()) assignSubmitError.value = '订单更新失败，请关闭并刷新后核对'
  }
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
      await Promise.allSettled([tableRef.value?.refresh(), ...(refreshDetail ? [refreshCurrentOrder()] : []),
        ...(assignVisible.value && !assigning.value ? [refreshAssignmentEligibility()] : [])])
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
    if (assignReview.value?.id === data.id) assignReviewChecked.value = false
    if (transferReview.value?.id === data.id) transferReviewChecked.value = false
    if (pendingActionOrderId === data.id) {
      invalidateOrderAction()
      ElMessage.warning('订单已变化，请核对最新状态后再操作')
    }
    if (assignVisible.value && assignOrder.value?.id === data.id && !assigning.value && !assignSubmitError.value) {
      assignSubmitError.value = '订单已变化，请关闭并刷新后核对'
    }
    if (transferVisible.value && transferOrder.value?.id === data.id && !transferring.value && !transferSubmitError.value) {
      transferSubmitError.value = '订单已变化，请关闭并刷新后核对'
    }
    requestOrderRefresh(data.id)
  })
  offConnected = adminWs.on('connected', () => {
    // A reconnect cannot tell us whether an in-flight refund committed.
    // Keep its locked review until HTTP resolves or the operator checks it.
    if (!refundSubmitting.value && !refundResultUnknown.value) invalidateOrderAction()
    if (assignVisible.value && !assigning.value && !assignSubmitError.value) assignSubmitError.value = '连接已恢复，请关闭并刷新订单后核对'
    if (transferVisible.value && !transferring.value && !transferSubmitError.value) transferSubmitError.value = '连接已恢复，请关闭并刷新订单后核对'
    assignReviewChecked.value = false
    transferReviewChecked.value = false
    requestOrderRefresh()
  })
  adminWs.connect()
}

function stopOrderRealtime() {
  ++realtimeGeneration
  offOrderWs?.()
  offConnected?.()
  offOrderWs = offConnected = null
  if (refreshTimer !== null) clearTimeout(refreshTimer)
  if (pendingDeadlineTimer !== null) clearTimeout(pendingDeadlineTimer)
  pendingDeadlineTimer = null
  refreshTimer = null
  refreshPending = detailRefreshPending = refreshRunning = false
  orderVersions.clear()
  invalidateOrderAction()
}

async function processAfterSale(ticket: any, action: 'resolve' | 'refund') {
  const orderId = detailOrderId.value
  if (!orderId || !detailVisible.value || detailLoading.value || detailFailed.value || aftersaleFailed.value || ticket.status !== 'OPEN') return
  if (action === 'refund') { await openRefundReview(orderId, ticket.id); return }
  const operation = beginOrderAction(orderId, 'order:settle')
  if (!operation) return
  try {
    let resolution: string
    try {
      const result = await ElMessageBox.prompt(`订单 ${current.value?.order_no || `#${orderId}`} · 售后 #${ticket.id}。确认处理只结束售后，不会自动退款。`, '处理售后', {
        inputType: 'textarea', inputPlaceholder: '填写处理说明（2–1000 字）',
        inputValidator: (value: string) => value?.trim().length >= 2 && value.trim().length <= 1000,
        inputErrorMessage: '处理说明须为 2–1000 字', confirmButtonText: '确认处理', cancelButtonText: '返回',
      })
      resolution = result.value.trim()
    } catch { return }
    if (!operation.current() || detailOrderId.value !== orderId || !detailVisible.value || detailLoading.value || detailFailed.value || aftersaleFailed.value) return
    pendingActionOrderId = null
    try {
      await request({ url: `/aftersales/${ticket.id}/resolve/`, method: 'post', data: { resolution }, silent: true })
      if (!operation.current(true)) return
      ElMessage.success('售后已处理')
    } catch (error: any) {
      if (!operation.current(true)) return
      ElMessage.warning(orderActionFailure(error, '售后处理'))
    }
    if (operation.current(true)) requestOrderRefresh(orderId)
  } finally { operation.finish() }
}

function orderActionFailure(error: any, action: string) {
  const body = error?.response?.data || error
  const status = error?.response?.status
  if ((!status || status < 500) && ((typeof body?.code === 'number' && body.code >= 400 && body.code < 500) || (status >= 400 && status < 500))) {
    const reason = typeof body?.msg === 'string' ? body.msg : typeof body?.detail === 'string' ? body.detail : ''
    return reason ? `${action}未完成：${reason}` : `${action}未完成，请刷新并核对状态`
  }
  return `${action}结果未确认，请刷新并核对订单状态后再操作`
}

async function onCancel(row: any) {
  if (row.status !== 'PENDING') return
  const operation = beginOrderAction(row.id, 'order:cancel')
  if (!operation) return
  try {
    let reason: string
    const consequence = row.payment_status === 'UNPAID'
      ? '未支付订单会先核对支付渠道状态。'
      : '余额支付将退回余额；微信支付订单需核对渠道，本入口可能无法处理。'
    try {
      const result = await ElMessageBox.prompt(`订单 ${row.order_no || `#${row.id}`}：${consequence}`, '取消订单', {
        inputType: 'textarea', inputPlaceholder: '取消原因（可选，最多 255 字）',
        inputValidator: (value: string) => (value || '').trim().length <= 255,
        inputErrorMessage: '取消原因最多 255 字', confirmButtonText: '确认取消订单', cancelButtonText: '返回', type: 'warning',
      })
      reason = (result.value || '').trim()
    } catch { return }
    if (!operation.current()) return
    pendingActionOrderId = null
    try {
      await request({ url: `/orders/${row.id}/cancel/`, method: 'post', data: { reason }, silent: true })
      if (!operation.current(true)) return
      ElMessage.success('订单已取消，请查看支付状态')
    } catch (error: any) {
      if (!operation.current(true)) return
      ElMessage.warning(orderActionFailure(error, '取消订单'))
    }
    if (operation.current(true)) requestOrderRefresh(row.id)
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
  refundResultUnknown.value = false
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
  if (!refundLimits.value || refundSubmitting.value || refundResultUnknown.value) return
  refundAmountCoins.value = amountToXaCoin(refundLimits.value.remaining)
}

function reviewUnknownRefund() {
  const orderId = refundTarget.value?.orderId
  if (!orderId) return
  clearRefundReview()
  if (detailVisible.value && detailOrderId.value === orderId) void refreshCurrentOrder()
  else void onDetail({ id: orderId })
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
  if (!target || !operation?.current() || refundSubmitting.value || refundResultUnknown.value) return
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
  if (target.ticketId && (!detailVisible.value || detailOrderId.value !== target.orderId || detailFailed.value || detailLoading.value || aftersaleFailed.value)) return
  refundSubmitting.value = true
  refundError.value = ''
  try {
    pendingActionOrderId = null
    const reason = refundReason.value.trim()
    const intent = { refund_amount: preview.refund_amount, plan_token: preview.plan_token, client_request_id: refundIntentId(preview.refund_amount, reason) }
    if (target.ticketId) await request({ url: `/aftersales/${target.ticketId}/refund/`, method: 'post', data: { resolution: reason, ...intent }, silent: true })
    else await request({ url: `/orders/${target.orderId}/refund/`, method: 'post', data: { reason, ...intent }, silent: true })
    // Our own committed order notification may arrive before this HTTP result.
    if (!operation.current(true)) return
    ElMessage.success('已退款')
    requestOrderRefresh(target.orderId)
    clearRefundReview()
  } catch (error: any) {
    if (!operation.current(true) || !refundVisible.value) return
    const body = error?.response?.data || error
    const status = error?.response?.status
    const rejected = (!status || status < 500) && ((typeof body?.code === 'number' && body.code >= 400 && body.code < 500) || (status >= 400 && status < 500))
    if (!rejected) {
      refundResultUnknown.value = true
      refundPreview.value = null
      refundError.value = '退款结果未确认。请查看订单退款记录，确认结果后再操作；如仍未更新，请稍后重新核对。'
      requestOrderRefresh(target.orderId)
      return
    }
    if (!operation.current()) {
      invalidateOrderAction()
      requestOrderRefresh(target.orderId)
      ElMessage.warning('订单已变化，请重新打开退款复核')
      return
    }
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
          : typeof body?.msg === 'string' ? `退款未完成：${body.msg}` : typeof body?.detail === 'string' ? `退款未完成：${body.detail}` : '退款未完成，请刷新试算并核对订单状态'
  } finally { if (refundOperation === operation) refundSubmitting.value = false }
}

function orderTeamSize(order: any): 1 | 2 | null {
  const count = Number(order?.required_team_size)
  return count === 1 || count === 2 ? count : null
}

function isStaffTeamAssign(order: any) {
  return order?.dispatch_route === 'STAFF' && orderTeamSize(order) === 2
}

function canAssignOrder(order: any) {
  return order?.dispatch_assignment_allowed === true && order.status === 'PENDING'
    && order.payment_status === 'PAID'
    && (orderTeamSize(order) === 1 || isStaffTeamAssign(order))
}

function assignTitle(order: any) {
  if (!order?.dispatch_assignment_allowed) return '当前订单无法派单，请刷新并核对支付与订单状态'
  if (isStaffTeamAssign(order)) return order.payment_status === 'PAID' ? '同时指派两名陪玩' : '订单未支付，不能派单'
  return orderTeamSize(order) === 2 ? '此订单不能使用单人指派' : orderTeamSize(order) === 1 ? '指派一名陪玩' : '订单人数尚未加载，请刷新'
}

function assignProviderLabel(provider: any) {
  const name = provider.display_name || provider.nickname || provider.username || '陪玩'
  return provider.username && provider.username !== name ? `${name} · 账号 ${provider.username}` : name
}

function providerIdentityLabel(provider: any): string {
  const parts = [provider?.provider_name || '陪玩']
  if (provider?.provider_no) parts.push(`编号 ${provider.provider_no}`)
  if (provider?.provider_username) parts.push(`${provider.provider_identity_source === 'LEGACY' ? '旧账号' : '账号'} ${provider.provider_username}`)
  return parts.join(' · ')
}

function assignCandidateStatus(provider: any) {
  const status: Record<string, string> = { AVAILABLE: '空闲', BUSY: '忙碌', OFFLINE: '离线' }
  return status[provider.status] || '状态未知'
}
function assignCandidateLabel(provider: any) {
  return `${provider.display_name || provider.username || '陪玩'} · ${provider.escort_no || '编号未记录'} · ${assignCandidateStatus(provider)} · 账号 ${provider.username || '未记录'}`
}

function onAssign(row: any) {
  if (pageDisposed || assigning.value || !auth.hasPerm('order:dispatch')) return
  if (assignReview.value) { ElMessage.warning('请先核对上一笔指派结果'); return }
  if (!canAssignOrder(row)) {
    ElMessage.warning('当前订单无法派单，请刷新并核对支付与订单状态')
    return
  }
  assignOrder.value = row
  assignProviderId.value = undefined
  assignProviderIds.value = []
  assignProviderOptions.value = []
  assignSearchKeyword.value = ''
  assignSearchError.value = ''
  assignSubmitError.value = ''
  ++assignSearchSequence
  providerOptions.value = []
  assignVisible.value = true
}

function onDetailAssign() {
  if (!detailVisible.value || detailLoading.value || detailFailed.value || !current.value || !canAssignOrder(current.value)) return
  onAssign(current.value)
}

function closeAssignAndRefresh() {
  const orderId = assignOrder.value?.id
  assignVisible.value = false
  if (orderId) requestOrderRefresh(orderId)
}

function assignRejected(error: any) {
  const body = error?.response?.data || error
  return (!error?.response?.status || error.response.status < 500) && typeof body?.code === 'number'
    && body.code >= 400 && body.code < 500 && typeof body.msg === 'string' && Boolean(body.msg)
}
function assignFailureMessage(error: any) {
  const body = error?.response?.data || error
  if (assignRejected(error)) {
    return `指派未完成：${body.msg}。请刷新订单后核对`
  }
  return '指派结果未确认，请刷新订单并核对人员和状态后再操作。请求可能仍在处理，刷新仅显示当前状态。'
}

async function searchAssignProvider(keyword: string) {
  const sequence = ++assignSearchSequence
  const revision = auth.sessionRevision
  const orderId = assignOrder.value?.id
  assignSearchKeyword.value = keyword.trim()
  assignSearchError.value = ''
  const selectedIds = () => isStaffTeamAssign(assignOrder.value) ? assignProviderIds.value : [assignProviderId.value]
  const selected = assignProviderOptions.value.filter(p => selectedIds().includes(p.user))
  if (!assignSearchKeyword.value) { assignProviderOptions.value = selected; assignProviderLoading.value = false; return }
  assignProviderLoading.value = true
  try {
    const result = await escortApi.options({ keyword: assignSearchKeyword.value, page_size: 20 }) as any
    if (sequence !== assignSearchSequence || revision !== auth.sessionRevision || !assignVisible.value || assignOrder.value?.id !== orderId || !auth.hasPerm('order:dispatch')) return
    const keep = assignProviderOptions.value.filter(p => selectedIds().includes(p.user))
    const byId = new Map([...keep, ...(result.data?.list || [])].map((p: any) => [p.user, p]))
    assignProviderOptions.value = [...byId.values()]
  } catch {
    if (sequence === assignSearchSequence && revision === auth.sessionRevision && assignVisible.value && assignOrder.value?.id === orderId) {
      assignProviderOptions.value = selected
      assignSearchError.value = '陪玩搜索失败，请检查网络后重试'
    }
  } finally {
    if (sequence === assignSearchSequence) assignProviderLoading.value = false
  }
}

async function onAssignSave() {
  if (assigning.value || assignSubmitError.value || pageDisposed || !auth.hasPerm('order:dispatch') || !assignVisible.value || !assignOrder.value) return
  if (isStaffTeamAssign(assignOrder.value)) {
    const selected = [...assignProviderIds.value]
    if (selected.length !== 2 || new Set(selected).size !== 2) { ElMessage.warning('请选择两名不同的陪玩'); return }
    if (!auth.hasPerm('order:dispatch') || !assignVisible.value) return
    const revision = auth.sessionRevision, orderId = assignOrder.value.id
    beginAssignReview(orderId, assignOrder.value.order_no || `#${orderId}`)
    assigning.value = true
    try {
      await orderApi.action(orderId, 'assign', { provider_ids: selected }, true)
      if (pageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('order:dispatch') || !assignVisible.value || assignOrder.value?.id !== orderId) return
      clearAssignReview()
      ElMessage.success('双陪派单成功')
      assignVisible.value = false
      requestOrderRefresh(orderId)
    } catch (error: any) {
      if (!pageDisposed && revision === auth.sessionRevision && auth.hasPerm('order:dispatch') && assignVisible.value && assignOrder.value?.id === orderId) {
        if (assignRejected(error)) clearAssignReview()
        assignSubmitError.value = assignFailureMessage(error)
      }
    } finally { if (revision === auth.sessionRevision) assigning.value = false }
    return
  }
  if (orderTeamSize(assignOrder.value) !== 1) {
    ElMessage.warning('该订单不能使用单人指派，请刷新并核对订单人数')
    return
  }
  if (!assignOrder.value || !assignProviderId.value) {
    ElMessage.warning('请选择陪玩')
    return
  }
  const revision = auth.sessionRevision, orderId = assignOrder.value.id, providerId = assignProviderId.value
  beginAssignReview(orderId, assignOrder.value.order_no || `#${orderId}`)
  assigning.value = true
  try {
    await orderApi.action(orderId, 'assign', { provider_id: providerId }, true)
    if (pageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('order:dispatch') || !assignVisible.value || assignOrder.value?.id !== orderId) return
    clearAssignReview()
    ElMessage.success('派单成功')
    assignVisible.value = false
    requestOrderRefresh(orderId)
  } catch (error: any) {
    if (!pageDisposed && revision === auth.sessionRevision && auth.hasPerm('order:dispatch') && assignVisible.value && assignOrder.value?.id === orderId) {
      if (assignRejected(error)) clearAssignReview()
      assignSubmitError.value = assignFailureMessage(error)
    }
  } finally {
    if (revision === auth.sessionRevision) assigning.value = false
  }
}

// ---------------- 转单 ----------------
const transferVisible = ref(false)
const transferring = ref(false)
const transferConfirming = ref(false)
const transferLocked = computed(() => transferring.value || transferConfirming.value)
const transferOrder = ref<any>(null)
const transferReason = ref('')
const transferRows = ref<any[]>([])
const transferProviderOptions = ref<any[]>([])
const transferProviderLoading = ref(false)
const transferSearchKeyword = ref('')
const transferSearchError = ref('')
const transferSubmitError = ref('')
const transferAttempted = ref(false)
const transferReview = ref<{ id: number; orderNo: string } | null>(null)
const transferReviewLoading = ref(false)
const transferReviewChecked = ref(false)
let transferReviewOwnerKey = ''
let transferSearchSequence = 0
let transferConfirmationSequence = 0

function transferReviewKey() {
  const id = auth.profile?.id
  return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_order_transfer_review_${id}` : ''
}
function restoreTransferReview() {
  const key = transferReviewKey()
  if (key === transferReviewOwnerKey) return
  transferReviewOwnerKey = key
  transferReview.value = null
  transferReviewChecked.value = false
  transferReviewLoading.value = false
  if (!key) return
  try {
    const marker = JSON.parse(sessionStorage.getItem(key) || 'null')
    if (Number.isSafeInteger(marker?.id) && marker.id > 0 && typeof marker.orderNo === 'string') transferReview.value = marker
  } catch { /* Keep the in-page guard if session storage is unavailable. */ }
}
function beginTransferReview(id: number, orderNo: string) {
  transferReview.value = { id, orderNo }
  transferReviewChecked.value = false
  const key = transferReviewKey()
  if (key) try { sessionStorage.setItem(key, JSON.stringify({ id, orderNo })) } catch { /* In-page guard remains active. */ }
}
function clearTransferReview() {
  const key = transferReviewKey()
  if (key) try { sessionStorage.removeItem(key) } catch { /* Storage may be unavailable. */ }
  transferReview.value = null
  transferReviewChecked.value = false
}
async function viewTransferReview() {
  const review = transferReview.value
  if (!review || transferReviewLoading.value || transferring.value || !canReadOrders.value) return
  const revision = auth.sessionRevision
  transferReviewChecked.value = false
  transferReviewLoading.value = true
  try {
    await onDetail({ id: review.id })
    if (!pageDisposed && revision === auth.sessionRevision && transferReview.value === review && detailVisible.value
      && detailOrderId.value === review.id && !detailLoading.value && !detailFailed.value && !logsFailed.value) {
      transferReviewChecked.value = true
    }
  } finally {
    if (revision === auth.sessionRevision) transferReviewLoading.value = false
  }
}
async function finishTransferReview() {
  const review = transferReview.value, revision = auth.sessionRevision
  if (!review || !transferReviewChecked.value || transferReviewLoading.value || transferring.value) return
  try {
    await ElMessageBox.confirm('请确认已核对当前陪玩、转单流水及请求处理情况。请求仍可能在处理中。', '解除转单限制', {
      type: 'warning', confirmButtonText: '已核对，解除限制', cancelButtonText: '继续核对',
    })
  } catch { return }
  if (!pageDisposed && revision === auth.sessionRevision && transferReview.value === review && transferReviewChecked.value) clearTransferReview()
}

watch([() => auth.sessionRevision, () => auth.profile?.id], restoreTransferReview, { immediate: true, flush: 'sync' })

function transferCoinRaw(value: unknown): number | null {
  const text = String(value ?? '').trim()
  if (!/^(?:0|[1-9][0-9]*)(?:\.[0-9])?$/.test(text)) return null
  const raw = xaCoinToAmount(text)
  return Number.isSafeInteger(raw) && raw >= 0 && raw <= 2147483647 ? raw : null
}
function validTransferPercent(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 100
}

async function searchTransferProvider(keyword: string) {
  const sequence = ++transferSearchSequence
  const revision = auth.sessionRevision
  const orderId = transferOrder.value?.id
  transferSearchKeyword.value = keyword.trim()
  transferSearchError.value = ''
  const selected = () => transferProviderOptions.value.filter(p => transferRows.value.some(row => row.new_provider_id === p.user))
  if (!transferSearchKeyword.value) { transferProviderOptions.value = selected(); transferProviderLoading.value = false; return }
  transferProviderLoading.value = true
  try {
    const result = await escortApi.options({ keyword: transferSearchKeyword.value, page_size: 20 }) as any
    if (pageDisposed || sequence !== transferSearchSequence || revision !== auth.sessionRevision || !transferVisible.value || transferOrder.value?.id !== orderId || !auth.hasPerm('order:dispatch')) return
    transferProviderOptions.value = [...new Map([...selected(), ...(result.data?.list || [])].map((p: any) => [p.user, p])).values()]
  } catch {
    if (!pageDisposed && sequence === transferSearchSequence && revision === auth.sessionRevision && transferVisible.value && transferOrder.value?.id === orderId && auth.hasPerm('order:dispatch')) {
      transferProviderOptions.value = selected()
      transferSearchError.value = '陪玩搜索失败，请检查网络后重试'
    }
  } finally {
    if (sequence === transferSearchSequence) transferProviderLoading.value = false
  }
}

function closeTransferAndRefresh() {
  const orderId = transferOrder.value?.id
  transferVisible.value = false
  if (orderId) requestOrderRefresh(orderId)
}

function onTransfer(row: any) {
  if (pageDisposed || !auth.hasPerm('order:dispatch') || transferLocked.value) return
  if (transferReview.value) { ElMessage.warning('请先核对上一笔转单结果'); return }
  if (row.status !== 'IN_SERVICE') { ElMessage.warning('订单状态已变化，请刷新后核对'); return }
  transferOrder.value = row
  transferReason.value = ''
  transferProviderOptions.value = []
  transferSearchKeyword.value = transferSearchError.value = transferSubmitError.value = ''
  transferAttempted.value = false
  ++transferSearchSequence
  transferProviderLoading.value = false
  // 未结算的打手行才可转出；无明细行时回落订单级主打手
  const rows = (row.providers && row.providers.length)
    ? row.providers.filter((p: any) => p.provider && !p.settled_at && !p.released_at)
    : (row.provider
      ? [{ provider: row.provider, provider_name: row.provider_name, provider_no: row.provider_no, provider_username: row.provider_username, provider_identity_source: row.provider_identity_source, provider_income: row.provider_income }]
      : [])
  transferRows.value = rows.map((p: any) => ({
    old_provider_id: p.provider,
    old_provider_name: p.provider_name || '打手',
    old_provider_label: providerIdentityLabel(p),
    pool: Number.isSafeInteger(p.provider_income) && p.provider_income >= 0 ? p.provider_income : null,
    enabled: rows.length === 1,
    new_provider_id: undefined,
    split_type: 'PERCENT',
    split_percent: 0,
    split_fixed_yuan: '0',
    penalty_yuan: '0',
  }))
  transferVisible.value = true
}

async function onTransferSave() {
  if (pageDisposed || transferLocked.value || transferSubmitError.value || transferReview.value || !transferVisible.value || !transferOrder.value || !auth.hasPerm('order:dispatch')) return
  transferAttempted.value = true
  const picked = transferRows.value.filter((r) => r.enabled)
  if (!picked.length) { ElMessage.warning('请至少选择一名要转出的打手'); return }
  for (const r of picked) {
    if (!r.new_provider_id) { ElMessage.warning(`请为「${r.old_provider_name}」选择转入新打手`); return }
    if (r.new_provider_id === r.old_provider_id) { ElMessage.warning('新打手不能与原打手相同'); return }
    if (r.pool === null) { transferSubmitError.value = '可分配份额未确认，请刷新订单后核对'; return }
    if ((r.split_type === 'FIXED' ? transferCoinRaw(r.split_fixed_yuan) === null : !validTransferPercent(r.split_percent)) || transferCoinRaw(r.penalty_yuan) === null) return
  }
  const newIds = picked.map((r) => r.new_provider_id)
  if (new Set(newIds).size !== newIds.length) { ElMessage.warning('多个转入新打手不能重复'); return }

  const transfers = picked.map((r) => ({
    old_provider_id: r.old_provider_id,
    new_provider_id: r.new_provider_id,
    split_type: r.split_type,
    split_value: r.split_type === 'FIXED' ? transferCoinRaw(r.split_fixed_yuan) : r.split_percent,
    penalty_amount: transferCoinRaw(r.penalty_yuan),
  }))
  const revision = auth.sessionRevision, orderId = transferOrder.value.id
  const orderNo = transferOrder.value.order_no || `#${orderId}`
  const version = orderVersions.get(orderId) || 0
  const reason = transferReason.value
  const summary = picked.map((row, index) => {
    const incoming = transferProviderOptions.value.find(person => person.user === row.new_provider_id)
    const oldShare = transfers[index].split_type === 'FIXED'
      ? Math.min(transfers[index].split_value!, row.pool)
      : Math.floor(row.pool * transfers[index].split_value! / 100)
    const capped = transfers[index].split_type === 'FIXED' && transfers[index].split_value! > row.pool
    const splitLabel = transfers[index].split_type === 'FIXED'
      ? `固定额 ${amountToXaCoin(transfers[index].split_value)} 币` : `按比例 ${transfers[index].split_value}%`
    return { old: row.old_provider_label, incoming: incoming ? assignCandidateLabel(incoming) : `陪玩账号 #${row.new_provider_id}`, splitLabel,
      oldShare, newShare: row.pool - oldShare, penalty: transfers[index].penalty_amount!, capped }
  })
  const confirmation = ++transferConfirmationSequence
  transferConfirming.value = true
  let accepted = false
  try {
    await ElMessageBox.confirm(h('div', { class: 'od-transfer-confirm' }, [
      h('p', `订单 ${orderNo}`),
      ...summary.map(item => h('div', { class: 'od-transfer-confirm-item' }, [
        h('strong', `转出：${item.old}`), h('p', `转入：${item.incoming}`),
        h('p', `旧陪玩保留方式：${item.splitLabel}`),
        h('p', `预计旧陪玩 ${amountToXaCoin(item.oldShare)} 币 · 新陪玩 ${amountToXaCoin(item.newShare)} 币${item.capped ? '（已按份额上限计算）' : ''}`),
        h('p', `旧陪玩罚款 ${amountToXaCoin(item.penalty)} 币`),
      ])),
      h('p', `原因：${reason.trim() || '未填写'}`),
      h('p', { class: 'od-transfer-confirm-note' }, '金额按读取时份额估算；提交时按最新份额处理并核验资格，实际以订单记录为准。'),
    ]), '核对转单', { type: 'warning', confirmButtonText: '确认转单', cancelButtonText: '返回修改', closeOnClickModal: false, customClass: 'od-transfer-confirm-box' })
    accepted = true
  } catch { /* Returning to the form keeps the draft. */ }
  finally { if (confirmation === transferConfirmationSequence) transferConfirming.value = false }
  if (!accepted || confirmation !== transferConfirmationSequence || pageDisposed || revision !== auth.sessionRevision
    || !auth.hasPerm('order:dispatch') || !transferVisible.value || transferOrder.value?.id !== orderId || transferSubmitError.value
    || version !== (orderVersions.get(orderId) || 0) || transferReview.value) return
  beginTransferReview(orderId, transferOrder.value.order_no || `#${orderId}`)
  transferring.value = true
  try {
    await orderApi.action(orderId, 'transfer', { transfers, reason }, true)
    if (pageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('order:dispatch') || !transferVisible.value || transferOrder.value?.id !== orderId) return
    clearTransferReview()
    ElMessage.success('转单成功')
    transferVisible.value = false
    requestOrderRefresh(orderId)
  } catch (error: any) {
    if (!pageDisposed && revision === auth.sessionRevision && auth.hasPerm('order:dispatch') && transferVisible.value && transferOrder.value?.id === orderId) {
      const body = error?.response?.data || error
      const status = error?.response?.status
      const rejected = (!status || status < 500) && ((typeof body?.code === 'number' && body.code >= 400 && body.code < 500) || (status >= 400 && status < 500))
      if (rejected) clearTransferReview()
      transferSubmitError.value = rejected
        ? `转单未完成：${typeof body?.msg === 'string' && body.msg ? body.msg : '请求被拒绝'}。请刷新订单后核对`
        : '转单结果未确认，请关闭弹窗并核对订单人员及流水'
    }
  } finally {
    if (revision === auth.sessionRevision) transferring.value = false
  }
}

watch([() => auth.sessionRevision, () => auth.hasPerm('order:dispatch')], () => {
  if (transferConfirming.value) ElMessageBox.close()
  ++transferConfirmationSequence
  transferConfirming.value = false
  transferVisible.value = false
  transferring.value = false
  transferOrder.value = null
  transferRows.value = []
  transferProviderOptions.value = []
  transferProviderLoading.value = false
  transferSearchKeyword.value = transferSearchError.value = transferSubmitError.value = ''
  ++transferSearchSequence
}, { flush: 'sync' })

// ---------------- 客服快捷派单 ----------------
const dispatchVisible = ref(false)
const dispatching = ref(false)
const dispatchConfirming = ref(false)
const dispatchRequestId = ref('')
const bossLoading = ref(false)
const providerLoading = ref(false)
const serviceLoading = ref(false)
const serviceLoadError = ref('')
const bossSearchError = ref('')
const bossSearchKeyword = ref('')
const providerSearchError = ref('')
const providerSearchKeyword = ref('')
const dispatchResultUnknown = ref(false)
const dispatchSubmitNotice = ref('')
type DispatchReview = { customerId: number; boss: string; service: string; time: string; requestId: string }
const dispatchReview = ref<DispatchReview | null>(null)
let dispatchReviewOwnerKey = ''
function dispatchReviewKey() {
  const id = auth.profile?.id
  return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_quick_dispatch_review_${id}` : ''
}
function restoreDispatchReview() {
  const key = dispatchReviewKey()
  if (key === dispatchReviewOwnerKey) return
  dispatchReviewOwnerKey = key
  dispatchReview.value = null
  if (!key) return
  try {
    const marker = JSON.parse(sessionStorage.getItem(key) || 'null')
    if (Number.isSafeInteger(marker?.customerId) && marker.customerId > 0
      && ['boss', 'service', 'time', 'requestId'].every(field => typeof marker[field] === 'string' && marker[field])) dispatchReview.value = marker
  } catch { /* The in-page guard remains when storage is unavailable. */ }
}
function beginDispatchReview() {
  const marker: DispatchReview = {
    customerId: dispatchForm.customer_id,
    boss: `${currentBoss.value?.nickname || currentBoss.value?.username || '老板'} · ${currentBoss.value?.boss_no || currentBoss.value?.username || `#${dispatchForm.customer_id}`}`,
    service: currentService.value?.name || `服务 #${dispatchForm.service_id}`,
    time: new Date().toLocaleString(), requestId: dispatchRequestId.value,
  }
  dispatchReview.value = marker
  const key = dispatchReviewKey()
  if (key) try { sessionStorage.setItem(key, JSON.stringify(marker)) } catch { /* The in-page guard remains. */ }
}
function clearDispatchReviewMarker() {
  const key = dispatchReviewKey()
  if (key) try { sessionStorage.removeItem(key) } catch { /* Storage may be unavailable. */ }
  dispatchReview.value = null
}
function viewDispatchReview() {
  const marker = dispatchReview.value
  if (!marker || !canReadOrders.value) return
  Object.assign(query, { order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: marker.customerId, created_day: '', status: '', payment_status: '', dispatch_route: '', actionable_staff: '', aftersale_open: '' })
  tableRef.value?.reload()
}
async function finishDispatchReview() {
  const marker = dispatchReview.value, revision = auth.sessionRevision
  if (!marker || dispatching.value) return
  try {
    await ElMessageBox.confirm('请确认已核对该老板的订单与扣款，并了解请求仍可能在处理中。解除后可重新发起快捷派单。', '解除快捷派单限制', {
      type: 'warning', confirmButtonText: '已核对，解除限制', cancelButtonText: '继续核对',
    })
  } catch { return }
  if (!pageDisposed && revision === auth.sessionRevision && dispatchReview.value === marker && !dispatching.value) clearDispatchReviewMarker()
}
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreDispatchReview, { immediate: true, flush: 'sync' })
let bossSearchSequence = 0
let providerSearchSequence = 0
let serviceLoadSequence = 0
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
const quoteError = ref('')
// Element Plus 2.14.3 writes aria-disabled at mount but does not refresh it on updates.
// Remount only when effective availability changes so assistive technology sees the real state.
function dispatchRateDisabled(slot: any): boolean {
  return Boolean(!slot.provider_id || dispatchQuote.value?.commission_locked || quoteLoading.value || slot.suggestion_loading
    || dispatching.value || dispatchConfirming.value || dispatchResultUnknown.value)
}
const dispatchPendingRateDisabled = computed(() => quoteLoading.value || dispatching.value || dispatchConfirming.value || dispatchResultUnknown.value)
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
  quoteError.value = ''
  quoteLoading.value = false
  if (pageDisposed || !auth.hasPerm('order:dispatch') || !dispatchVisible.value || !dispatchForm.customer_id || !dispatchForm.service_id || dispatchCommissionError.value) return
  quoteLoading.value = true
  try {
    const result = await request({ url: '/orders/dispatch-quote/', silent: true, params: {
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
  } catch (error: any) {
    if (sequence === quoteSequence && inputs === dispatchQuoteKey() && dispatchVisible.value && !pageDisposed && revision === auth.sessionRevision) {
      const body = error?.response?.data || error
      quoteError.value = typeof body?.msg === 'string' && body.msg ? `试算失败：${body.msg}` : '试算失败，请检查网络后重试'
    }
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
  if (dispatchConfirming.value) ElMessageBox.close()
  dispatchConfirming.value = false
  dispatchVisible.value = false
  dispatching.value = false
  ++quoteSequence
  quotedSession = -1
  quotedInputs = ''
  dispatchQuote.value = undefined
  quoteLoading.value = false
  ++bossSearchSequence
  ++providerSearchSequence
  ++serviceLoadSequence
  bossLoading.value = providerLoading.value = serviceLoading.value = false
  dispatchForm.providers.forEach((slot: any) => { slot.suggestion_sequence = (slot.suggestion_sequence || 0) + 1 })
}
watch([() => auth.sessionRevision, () => auth.hasPerm('order:dispatch')], closeDispatchReview, { flush: 'sync' })

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
  const rate = Math.min(Math.max(Number(slot.commission_rate) || 0, 0), 100)
  const cut = Math.floor(providerBase(slot) * rate / 100)
  return `本人折后份额 ${amountToXaCoin(providerBase(slot))}币 − 抽成 ${amountToXaCoin(cut)}币，抽成按账务单位向下取整`
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
  if (!slot) return
  const sequence = slot.suggestion_sequence = (slot.suggestion_sequence || 0) + 1
  slot.suggestion_loading = false
  slot.suggestion_error = ''
  slot.suggested_rate = undefined
  slot.source_label = ''
  if (!slot.provider_id || !dispatchForm.service_id) return
  const revision = auth.sessionRevision
  const serviceId = dispatchForm.service_id
  const providerId = slot.provider_id
  const mode = dispatchCommissionMode.value
  slot.suggestion_loading = true
  try {
    const res = await request({
      url: '/orders/suggest-commission/',
      method: 'get',
      silent: true,
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
    if (dispatchVisible.value && !pageDisposed && revision === auth.sessionRevision && slot.suggestion_sequence === sequence
        && dispatchCommissionMode.value === mode && dispatchForm.service_id === serviceId && dispatchForm.providers[idx] === slot && slot.provider_id === providerId) {
      if (!dispatchFixed.value && !dispatchQuote.value?.commission_locked) slot.commission_rate = undefined
      slot.suggestion_error = '未能获取建议抽成，请重试或手动填写比例。'
    }
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
  if (dispatchReview.value) { ElMessage.warning('上次快捷派单结果待核对，请先核对订单和扣款'); return }
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
  serviceOptions.value = []
  bossSearchError.value = bossSearchKeyword.value = providerSearchError.value = providerSearchKeyword.value = ''
  dispatchResultUnknown.value = false
  dispatchSubmitNotice.value = ''
  ++bossSearchSequence
  ++providerSearchSequence
  dispatchVisible.value = true
  await loadDispatchServices()
}

watch(() => route.query.quick_dispatch, async value => {
  if (value !== '1') return
  const revision = auth.sessionRevision
  const remaining = { ...route.query }
  delete remaining.quick_dispatch
  await router.replace({ query: remaining })
  if (pageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('order:dispatch')) return
  if (dispatchVisible.value || dispatching.value) { ElMessage.info('请先完成当前派单'); return }
  void onDispatch()
}, { immediate: true })

async function loadDispatchServices() {
  if (!dispatchVisible.value || pageDisposed || !auth.hasPerm('order:dispatch')) return
  const sequence = ++serviceLoadSequence, revision = auth.sessionRevision, requestId = dispatchRequestId.value
  serviceLoading.value = true
  serviceLoadError.value = ''
  try {
    const list: any[] = []
    let page = 1
    while (true) {
      const res = await request({ url: '/options/service-items/', params: { is_active: 1, page, page_size: 100 }, silent: true }) as any
      if (pageDisposed || !dispatchVisible.value || revision !== auth.sessionRevision || requestId !== dispatchRequestId.value || sequence !== serviceLoadSequence || !auth.hasPerm('order:dispatch')) return
      const items = res.data?.list || []
      list.push(...items)
      if (!items.length || list.length >= res.data.total) break
      page += 1
    }
    serviceOptions.value = list
    if (dispatchForm.service_id) {
      currentService.value = list.find(item => item.id === dispatchForm.service_id) || null
      if (!currentService.value) serviceLoadError.value = '所选服务已下架或不可用，请重新选择服务。'
    }
  } catch {
    if (!pageDisposed && dispatchVisible.value && revision === auth.sessionRevision && requestId === dispatchRequestId.value && sequence === serviceLoadSequence && auth.hasPerm('order:dispatch')) serviceLoadError.value = '服务列表加载失败，请检查网络后重新加载。'
  } finally { if (sequence === serviceLoadSequence) serviceLoading.value = false }
}

async function searchBoss(keyword: string) {
  const sequence = ++bossSearchSequence, revision = auth.sessionRevision, requestId = dispatchRequestId.value
  bossSearchKeyword.value = keyword.trim()
  bossSearchError.value = ''
  const selected = () => bossOptions.value.filter(u => u.id === dispatchForm.customer_id)
  if (!bossSearchKeyword.value) { bossOptions.value = selected(); bossLoading.value = false; return }
  bossLoading.value = true
  try {
    const res = await request({ url: '/options/users/', params: { keyword: bossSearchKeyword.value, page_size: 20 }, silent: true }) as any
    if (pageDisposed || !dispatchVisible.value || revision !== auth.sessionRevision || requestId !== dispatchRequestId.value || sequence !== bossSearchSequence || !auth.hasPerm('order:dispatch')) return
    bossOptions.value = [...new Map([...selected(), ...(res.data?.list || [])].map((u: any) => [u.id, u])).values()]
  } catch {
    if (!pageDisposed && dispatchVisible.value && revision === auth.sessionRevision && requestId === dispatchRequestId.value && sequence === bossSearchSequence && auth.hasPerm('order:dispatch')) bossSearchError.value = '老板搜索失败，请检查网络后重试。'
  } finally {
    if (sequence === bossSearchSequence) bossLoading.value = false
  }
}

function onBossChange(id: number) {
  currentBoss.value = bossOptions.value.find((u) => u.id === id) || null
}

async function onServiceChange(id: number) {
  ++providerSearchSequence
  providerLoading.value = false
  providerSearchError.value = ''
  providerOptions.value = providerOptions.value.filter(p => dispatchForm.providers.some((slot: any) => slot.provider_id === p.user))
  dispatchCommissionMode.value = 'AUTO'
  currentService.value = serviceOptions.value.find((s) => s.id === id) || null
  if (currentService.value) serviceLoadError.value = ''
  syncDispatchProviderCount()
  if (currentService.value?.service_type !== 'COMPANION') dispatchForm.provider_gender = 'ANY'
  await Promise.all(
    dispatchForm.providers
      .map((_: any, idx: number) => idx)
      .filter((idx: number) => dispatchForm.providers[idx].provider_id)
      .map((idx: number) => onProviderPicked(idx)),
  )
}

function onDispatchGenderChange() {
  ++providerSearchSequence
  providerLoading.value = false
  const gender = dispatchForm.provider_gender
  let removed = false
  dispatchForm.providers.forEach((slot: any, idx: number) => {
    const selected = providerOptions.value.find(p => p.user === slot.provider_id)
    if (slot.provider_id && gender !== 'ANY' && selected?.gender !== gender) {
      slot.provider_id = undefined
      void onProviderPicked(idx)
      removed = true
    }
  })
  providerOptions.value = providerOptions.value.filter(p => dispatchForm.providers.some((slot: any) => slot.provider_id === p.user))
  if (removed) ElMessage.warning('已移除不符合陪玩选择的人员，请重新选择')
  if (providerSearchKeyword.value) void searchProvider(providerSearchKeyword.value)
}

async function searchProvider(keyword: string) {
  const sequence = ++providerSearchSequence, revision = auth.sessionRevision, requestId = dispatchRequestId.value
  const serviceId = dispatchForm.service_id
  const service = currentService.value
  const providerGender = service?.service_type === 'COMPANION' ? dispatchForm.provider_gender : 'ANY'
  providerSearchKeyword.value = keyword.trim()
  providerSearchError.value = ''
  const selected = () => providerOptions.value.filter(p => dispatchForm.providers.some((slot: any) => slot.provider_id === p.user))
  if (!providerSearchKeyword.value) { providerOptions.value = selected(); providerLoading.value = false; return }
  providerLoading.value = true
  try {
    const res = await request({ url: '/options/escorts/', params: { keyword: providerSearchKeyword.value, service_id: serviceId || undefined, provider_gender: providerGender, page_size: 20 }, silent: true }) as any
    if (pageDisposed || !dispatchVisible.value || revision !== auth.sessionRevision || requestId !== dispatchRequestId.value || sequence !== providerSearchSequence || serviceId !== dispatchForm.service_id || providerGender !== (currentService.value?.service_type === 'COMPANION' ? dispatchForm.provider_gender : 'ANY') || !auth.hasPerm('order:dispatch')) return
    const results = (res.data?.list || []).map((p: any) => ({...p,qualifications: (p.qualifications || []).filter((q: any) => !service?.provider_kind || (q.provider_kind === service.provider_kind && q.platform === service.platform && q.security_level === service.security_level && q.game_category === service.game_category_id))}))
    providerOptions.value = [...new Map([...selected(), ...results].map((p: any) => [p.user, p])).values()]
  } catch {
    if (!pageDisposed && dispatchVisible.value && revision === auth.sessionRevision && requestId === dispatchRequestId.value && sequence === providerSearchSequence && serviceId === dispatchForm.service_id && auth.hasPerm('order:dispatch')) providerSearchError.value = '陪玩搜索失败，请检查网络后重试。'
  } finally {
    if (sequence === providerSearchSequence) providerLoading.value = false
  }
}

async function onDispatchSave() {
  if (dispatching.value || dispatchConfirming.value || dispatchResultUnknown.value || dispatchReview.value) return
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
  const revision = auth.sessionRevision
  const requestId = dispatchRequestId.value
  const confirmationKey = () => JSON.stringify({
    quoteKey: dispatchQuoteKey(), amount: dispatchQuote.value?.amount, quoteSequence,
    customer: dispatchForm.customer_id, service: dispatchForm.service_id,
    rounds: dispatchForm.game_rounds, mode: dispatchForm.escort_mode, gender: dispatchForm.provider_gender,
    remark: dispatchForm.remark, providers: dispatchForm.providers.map((slot: any) => [slot.provider_id, slot.commission_rate, slot.suggestion_loading]),
  })
  const key = confirmationKey()
  const boss = [currentBoss.value?.nickname || currentBoss.value?.username || '老板',
    currentBoss.value?.boss_no ? `编号 ${currentBoss.value.boss_no}` : `#${dispatchForm.customer_id}`,
    currentBoss.value?.username ? `账号 ${currentBoss.value.username}` : ''].filter(Boolean).join(' · ')
  const service = currentService.value?.name || `服务 #${dispatchForm.service_id}`
  const people: Array<{ identity: string; estimate: string; commission: string }> = picked.map((slot: any) => {
    const provider = providerOptions.value.find((item: any) => item.user === slot.provider_id)
    const identity = provider ? `${assignProviderLabel(provider)}${provider.escort_no ? ` · 编号 ${provider.escort_no}` : ''}` : `陪玩账号 #${slot.provider_id}`
    const share = dispatchFixed.value ? dispatchProviderShare(slot) : undefined
    const estimate = dispatchFixed.value && !share ? '个人分摊待服务端确认'
      : `预计实得 ${amountToXaCoin(providerIncome(slot))} 币（${providerIncomeDesc(slot)}）`
    return { identity, estimate, commission: dispatchFixed.value ? '' : `抽成 ${slot.commission_rate}%` }
  })
  const pendingRate = dispatchPendingPercentage() ? dispatchPendingRate.value : dispatchQuote.value.commission_rate
  const commission = dispatchFixed.value
    ? `整单固定抽成 ${amountToXaCoin(dispatchQuote.value.commission_fixed_total)} 币（按试算）`
    : picked.length ? ''
      : typeof pendingRate === 'number' ? `待接单抽成 ${pendingRate}%` : '待接单抽成以服务端结果为准'
  dispatchConfirming.value = true
  let accepted = false
  try {
    await ElMessageBox.confirm(h('div', { class: 'od-transfer-confirm' }, [
      h('p', `老板：${boss}`), h('p', `服务：${service} · ${dispatchForm.game_rounds} 局`),
      h('p', [h('strong', `预计扣款：${amountToXaCoin(dispatchQuote.value.amount)} 兴安币`)]),
      ...(people.length ? people.map((person, index) => h('div', { class: 'od-transfer-confirm-item' }, [
        h('strong', `陪玩 ${index + 1}：${person.identity}`),
        ...(person.commission ? [h('p', person.commission)] : []), h('p', person.estimate),
      ])) : [h('p', '陪玩：留空，进入待接单大厅')]),
      ...(commission ? [h('p', `抽成：${commission}`)] : []),
      ...(currentService.value?.service_type === 'COMPANION' ? [h('p', `陪玩选择：${({ ANY: '不限', MALE: '男陪', FEMALE: '女陪' } as Record<string, string>)[dispatchForm.provider_gender] || '待核对'}`)] : []),
      ...(dispatchForm.remark?.trim() ? [h('p', `备注：${dispatchForm.remark.trim()}`)] : []),
      h('p', { class: 'od-transfer-confirm-note' }, '余额与金额为读取时快照；提交时服务端会重新核验价格、余额和陪玩资格。'),
    ]), '确认快捷派单', { type: 'warning', confirmButtonText: '确认扣款并派单', cancelButtonText: '返回修改', closeOnClickModal: false, customClass: 'od-transfer-confirm-box' })
    accepted = true
  } catch { /* Return to the unchanged form. */ }
  finally { dispatchConfirming.value = false }
  if (!accepted || pageDisposed || !dispatchVisible.value || dispatching.value || dispatchResultUnknown.value || dispatchReview.value
    || revision !== auth.sessionRevision || requestId !== dispatchRequestId.value || !auth.hasPerm('order:dispatch')) return
  if (key !== confirmationKey() || quoteLoading.value || quotedInputs !== dispatchQuoteKey() || quotedSession !== revision) {
    ElMessage.warning('派单信息或试算已变化，请重新核对')
    return
  }
  dispatching.value = true
  dispatchSubmitNotice.value = ''
  beginDispatchReview()
  try {
    const result = await request({ url: '/orders/dispatch/', method: 'post', silent: true, data: {
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
    } })
    if (pageDisposed || !dispatchVisible.value || requestId !== dispatchRequestId.value || revision !== auth.sessionRevision || !auth.hasPerm('order:dispatch')) return
    clearDispatchReviewMarker()
    ElMessage.success('派单成功')
    dispatchVisible.value = false
    tableRef.value.load()
    if (result.data?.status === 'PENDING') onDispatchMessage(result.data)
  } catch (error: any) {
    if (pageDisposed || !dispatchVisible.value || requestId !== dispatchRequestId.value || revision !== auth.sessionRevision || !auth.hasPerm('order:dispatch')) return
    const body = error?.response?.data || error
    if ((!error?.response?.status || error.response.status < 500) && body?.errorCode === 'PRICE_CHANGED') {
      clearDispatchReviewMarker()
      dispatchSubmitNotice.value = '价格或优惠已变化，请核对最新试算金额后重新派单。'
      await Promise.all([loadDispatchServices(), refreshDispatchQuote()])
    } else if ((!error?.response?.status || error.response.status < 500) && typeof body?.code === 'number' && body.code >= 400 && body.code < 500 && body.code !== 409 && typeof body.msg === 'string') {
      clearDispatchReviewMarker()
      dispatchSubmitNotice.value = `派单未完成：${body.msg}。请检查后重新提交。`
    } else {
      dispatchResultUnknown.value = true
      dispatchSubmitNotice.value = '派单结果未确认，请先关闭并刷新订单，核对是否已创建及扣款后再操作。'
    }
  } finally {
    if (revision === auth.sessionRevision && requestId === dispatchRequestId.value) dispatching.value = false
  }
}

function closeDispatchAndRefresh() {
  dispatchVisible.value = false
  requestOrderRefresh()
}


async function onStart(row: any) {
  await confirmServiceAction(row, 'start')
}

function consumptionReversed(order: any): boolean {
  const summary = order?.refund_summary
  return summary?.benefit_effects?.confirmation_reversed === true
    || (Array.isArray(summary?.records) && summary.records.some((record: any) => record?.benefit_effects?.confirmation_reversed === true))
}

function settlementStatusLabel(status: unknown): string {
  if (status === 'SETTLED') return '已结算'
  if (status === 'WAITING') return '待结算'
  return '收益待核对'
}

// A refund can change the current settlement status while the original
// snapshot still describes a previously settled amount.
function historicalSettlementRecorded(order: any, provider?: any): boolean {
  return ['REFUNDED', 'PARTIALLY_REFUNDED'].includes(order?.payment_status)
    && Boolean(provider?.settled_at || order?.settled_at)
}

async function onComplete(row: any) {
  await confirmServiceAction(row, 'complete')
}

async function confirmServiceAction(row: any, action: 'start' | 'complete') {
  const permission = action === 'start' ? 'order:dispatch' : 'order:settle'
  const expectedStatus = action === 'start' ? 'GRABBED' : 'IN_SERVICE'
  if (row.status !== expectedStatus) return
  const operation = beginOrderAction(row.id, permission)
  if (!operation) return
  try {
    const consequence = action === 'start'
      ? '订单将进入服务中。'
      : row.settlement_mode === 'COMPLETION'
        ? '完成后按该订单的旧规则结算，具体结果以流水为准。'
        : row.settlement_mode === 'REPORT_APPROVAL'
          ? '完成后等待报单审核，实际结算以流水为准。'
          : '结算方式待核对，完成结果以订单详情和流水为准。'
    try {
      await ElMessageBox.confirm(`订单 ${row.order_no || `#${row.id}`}：${consequence}`, action === 'start' ? '开始服务' : '完成服务', {
        type: 'warning', confirmButtonText: action === 'start' ? '确认开始' : '确认完成', cancelButtonText: '取消',
      })
    } catch { return }
    if (!operation.current()) return
    // The order's own update may arrive over WebSocket before the HTTP response.
    pendingActionOrderId = null
    try {
      await request({ url: `/orders/${row.id}/${action}/`, method: 'post', silent: true })
      if (!operation.current(true)) return
      ElMessage.success(action === 'start' ? '服务已开始' : '服务已完成，结算状态请查看订单详情')
    } catch (error: any) {
      if (!operation.current(true)) return
      const body = error?.response?.data || error
      const known = (!error?.response?.status || error.response.status < 500) && typeof body?.code === 'number' && body.code >= 400 && body.code < 500 && typeof body.msg === 'string'
      ElMessage.warning(known ? `${action === 'start' ? '开始' : '完成'}未成功：${body.msg}` : '操作结果未确认，请刷新订单并核对状态后再操作')
    }
    if (operation.current(true)) requestOrderRefresh(row.id)
  } finally { operation.finish() }
}

watch([() => auth.sessionRevision, () => auth.hasPerm('order:dispatch')], () => {
  assignVisible.value = false
  assigning.value = false
  assignOrder.value = null
  assignProviderId.value = undefined
  assignProviderIds.value = []
  assignProviderOptions.value = []
  assignProviderLoading.value = false
  assignSearchKeyword.value = ''
  assignSearchError.value = ''
  assignSubmitError.value = ''
  ++assignSearchSequence
}, { flush: 'sync' })

watch([() => auth.sessionRevision, () => canReadOrders.value, () => auth.hasPerm('order:refund'), () => auth.hasPerm('order:settle'), () => auth.hasPerm('order:cancel')], () => {
  stopOrderRealtime()
  detailVisible.value = false
  clearOrderDetail()
  if (pageMounted) startOrderRealtime()
}, { flush: 'sync' })

function refreshVisibleOrders() {
  if (!document.hidden) requestOrderRefresh()
  else if (pendingDeadlineTimer !== null) { clearTimeout(pendingDeadlineTimer); pendingDeadlineTimer = null }
}

onMounted(() => {
  pageMounted = true
  updateRefundCompact()
  if (typeof window !== 'undefined') window.addEventListener('resize', updateRefundCompact)
  document.addEventListener('visibilitychange', refreshVisibleOrders)
  startOrderRealtime()
})

onUnmounted(() => {
  pageDisposed = true
  if (transferConfirming.value) ElMessageBox.close()
  ++transferConfirmationSequence
  transferConfirming.value = false
  if (typeof window !== 'undefined') window.removeEventListener('resize', updateRefundCompact)
  document.removeEventListener('visibilitychange', refreshVisibleOrders)
  closeDispatchReview()
  stopOrderRealtime()
  clearOrderDetail()
})
</script>

<style scoped lang="scss">
.od-detail-actions { display: flex; align-items: center; justify-content: flex-end; gap: 12px; }
.od-detail-action-note { flex: 1; color: var(--muted-foreground); font-size: 12px; line-height: 1.4; }
.od-detail-help { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; margin-left: 4px; padding: 0; border: 0; border-radius: 50%; background: transparent; color: var(--primary); font: inherit; cursor: pointer; }
.od-detail-help:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
.od-person-identity { overflow-wrap: anywhere; }
:global(.od-transfer-confirm-box) { width: min(480px, calc(100vw - 24px)); max-height: calc(100dvh - 24px); }
:global(.od-transfer-confirm) { max-height: min(55dvh, 460px); overflow-y: auto; overflow-wrap: anywhere; }
:global(.od-transfer-confirm p) { margin: 6px 0; }
:global(.od-transfer-confirm-item) { padding: 8px 10px; margin: 8px 0; border: 1px solid var(--border); border-radius: 8px; background: var(--secondary); }
:global(.od-transfer-confirm-note) { color: var(--muted-foreground); font-size: 12px; }
:deep(.od-transfer-choice) { height: auto; align-items: flex-start; white-space: normal; }
:deep(.od-transfer-choice .el-checkbox__label) { white-space: normal; overflow-wrap: anywhere; line-height: 1.5; }
.od-game-value { overflow-wrap: anywhere; }
:global(.od-assign-options.el-popper) { max-width: calc(100vw - 64px); }
:global(.od-assign-options .el-select-dropdown__item) { height: auto; min-height: 44px; padding-top: 4px; padding-bottom: 4px; line-height: 18px; }
.od-candidate { min-width: 0; }
.od-candidate-main { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; }
.od-candidate-main strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.od-candidate-main span { flex: 0 1 auto; max-width: 65%; min-width: 0; overflow-wrap: anywhere; white-space: normal; text-align: right; font-size: 12px; color: var(--muted-foreground); }
.od-candidate-account { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted-foreground); font-size: 12px; }
.od-assign-context { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px 16px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); }
.od-assign-order { display: grid; gap: 6px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); }
.od-assign-order div { display: grid; grid-template-columns: 48px minmax(0, 1fr); gap: 8px; }
.od-assign-order span { color: var(--muted-foreground); font-size: 12px; }
.od-assign-order strong { overflow-wrap: anywhere; font-size: 12px; }
.od-assign-context div { display: grid; gap: 3px; min-width: 0; }
.od-assign-context span, .od-assign-context small { color: var(--muted-foreground); font-size: 12px; overflow-wrap: anywhere; }
.od-assign-context strong { overflow-wrap: anywhere; }
.od-assign-selected { display: grid; gap: 6px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); }
.od-assign-selected > strong { font-size: 12px; }
.od-assign-selected-row { overflow-wrap: anywhere; color: var(--muted-foreground); font-size: 12px; line-height: 1.5; }
:global(.od-help-tooltip) { max-width: min(320px, calc(100vw - 32px)); line-height: 1.5; white-space: normal; }
.od-filter-heading { display: flex; flex: 0 0 100%; align-items: baseline; flex-wrap: wrap; gap: 4px 12px; padding: 0 0 10px; border-bottom: 1px solid var(--border); }
.od-filter-heading strong { color: var(--foreground); font-size: 14px; }
.od-filter-heading span { color: var(--muted-foreground); font-size: 12px; }
.od-filter-context { color: var(--muted-foreground); font-size: 12px; line-height: 1.5; }
.od-advanced-filters { display: flex; flex: 0 0 100%; flex-wrap: wrap; align-items: center; gap: 8px; padding-top: 8px; border-top: 1px solid var(--border); }
.od-filter-note { color: var(--muted-foreground); font-size: 12px; }
.od-customer-filter { display: flex; flex: 0 0 100%; align-items: center; flex-wrap: wrap; gap: 8px; color: var(--primary); font-size: 12px; }
.od-assign-hint {
  margin-top: 4px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.4;
}
:deep(.el-table) .od-assign-hint { flex: 0 0 100%; overflow-wrap: anywhere; }
.od-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.od-mobile-list { display: grid; gap: 12px; }
.od-mobile-card { padding: 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.od-mobile-head { display: flex; align-items: center; gap: 8px; }
.od-mobile-head :deep(.el-checkbox) { min-width: var(--control-height-sm); min-height: var(--control-height-sm); margin-right: 0; }
.od-mobile-head strong { flex: 1; min-width: 0; font-size: 13px; overflow-wrap: anywhere; color: var(--foreground); }
.od-mobile-service { margin: 12px 0; color: var(--foreground); font-weight: 600; overflow-wrap: anywhere; }
.od-mobile-values { display: grid; gap: 8px; margin: 0; padding: 12px 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.od-mobile-values > div { display: flex; justify-content: space-between; gap: 12px; }
.od-mobile-values dt { color: var(--muted-foreground); }
.od-mobile-values dd { margin: 0; text-align: right; overflow-wrap: anywhere; }
.od-customer-identity { min-width: 0; line-height: 1.4; overflow-wrap: anywhere; }
:deep(.od-customer-column .cell) { white-space: normal; }
.od-customer-account { display: block; color: var(--muted-foreground); font-size: 12px; line-height: 1.4; overflow-wrap: anywhere; }
.od-identity-help { margin-left: 6px; }
.od-mobile-time { margin: 10px 0 0; color: var(--muted-foreground); font-size: 12px; }
.od-mobile-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
.od-mobile-actions :deep(.el-button + .el-button) { margin-left: 0; }

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
.od-detail-heading { margin: 24px 0 12px; color: var(--foreground); font-size: 15px; }
.od-detail-heading:first-child { margin-top: 0; }
.od-detail-updating, .od-detail-empty { color: var(--muted-foreground); font-size: 13px; line-height: 1.5; }
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
.od-transfer-order { margin: 0 0 12px; color: var(--foreground); font-weight: 600; overflow-wrap: anywhere; }

.od-dispatch-heading { display: flex; align-items: flex-start; gap: 10px; }
.od-dispatch-footer { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 16px; width: 100%; text-align: left; }
.od-dispatch-alert { flex: 1 0 100%; }
.od-dispatch-actions { display: flex; justify-content: flex-end; gap: 10px; flex: 0 0 auto; }

.od-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 24px;
  flex: 1 1 auto;
  min-width: 0;
  margin: 0;
  padding: 10px 12px;
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
.od-label-help { display: inline-flex; align-items: center; gap: 3px; padding: 0; border: 0; background: transparent; cursor: help; text-align: left; }
:deep(.od-route-status) { margin-left: 6px; }
.od-label-help:focus-visible { outline: 2px solid var(--xa-color-primary); outline-offset: 3px; border-radius: 3px; }
.od-summary-value {
  font-size: 16px;
  font-weight: 600;
  font-family: var(--font-mono);
  color: var(--foreground);
}
.od-summary-warn {
  font-size: 13px;
  font-weight: 600;
  color: var(--destructive);
}
.od-summary-note { color: var(--muted-foreground); font-size: 12px; line-height: 1.4; }
.od-summary-note :deep(.el-button) { min-width: 0; padding: 0; }
@media (max-width: 720px) {
  .od-dispatch-footer { flex-direction: column; align-items: stretch; gap: 10px; }
  .od-dispatch-actions > .el-button { flex: 1 1 0; }
  .od-summary { gap: 6px 18px; padding: 8px 10px; }
  .od-summary-note, .od-summary-warn { flex-basis: 100%; }
}
</style>
