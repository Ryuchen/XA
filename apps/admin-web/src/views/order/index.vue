<template>
  <div class="od-page">
    <!-- 头部操作行 -->
    <div class="ap-page-head">
      <p class="ap-page-intro">管理平台全部陪玩订单与售后</p>
      <div class="ap-page-head-actions">
        <Button v-if="auth.hasPerm('order:view')" variant="outline" @click="timeoutConfigVisible = true">待接单时限</Button>
        <Button v-if="auth.hasPerm('order:view')" variant="outline" @click="trafficConfigVisible = true">峰谷限流与监控</Button>
        <Button v-if="auth.hasPerm('order:dispatch')" @click="onDispatch">快捷派单</Button>
        <Button v-if="auth.hasPerm(['dispatch_config:view', 'dispatch_config:edit'])" variant="outline" @click="dispatchConfigVisible = true">配置</Button>
      </div>
    </div>

    <Alert v-if="auth.hasPerm('order:view') && auth.hasPerm('order:dispatch') && staffQueue.status === 'review'"><AlertTitle>部分客服待派订单需要核对</AlertTitle><AlertDescription>请查看客服派单订单详情并核对订单状态；暂不可派的订单不会显示可用派单操作。</AlertDescription></Alert>
    <Alert v-if="transferReview && !transferring"><AlertTitle>转单结果待核对</AlertTitle><AlertDescription><p>订单 {{ transferReview.orderNo }} 的转单结果未确认，请查看人员和状态流水。请求可能仍在处理。</p><Button variant="outline" :disabled="transferReviewLoading" @click="viewTransferReview">{{ transferReviewLoading ? '读取中…' : '查看订单记录' }}</Button><Button v-if="transferReviewChecked" variant="outline" :disabled="transferReviewLoading" @click="finishTransferReview">已核对，解除限制</Button></AlertDescription></Alert>
    <Alert v-if="assignReview && !assigning && !assignVisible && canReadOrders"><AlertTitle>指派结果待核对</AlertTitle><AlertDescription><p>订单 {{ assignReview.orderNo }} 的指派结果未确认。请核对陪玩和状态流水；请求可能仍在处理，读取仅显示当前状态。</p><Button variant="outline" :disabled="assignReviewLoading" @click="viewAssignReview">{{ assignReviewLoading ? '读取中…' : '查看订单记录' }}</Button><Button v-if="assignReviewChecked" variant="outline" :disabled="assignReviewLoading" @click="finishAssignReview">已核对，解除限制</Button></AlertDescription></Alert>
    <Alert v-if="dispatchReview && !dispatchVisible"><AlertTitle>快捷派单结果待核对</AlertTitle><AlertDescription><p>{{ dispatchReview.boss }} · {{ dispatchReview.service }} · {{ dispatchReview.time }}。请求可能仍在处理，请核对订单和扣款，避免重复派单。</p><Button v-if="canReadOrders" variant="outline" @click="viewDispatchReview">查看该老板订单</Button><span v-else>请联系有订单查看权限的人员核对。</span><Button variant="outline" @click="finishDispatchReview">已核对，解除限制</Button></AlertDescription></Alert>
    <Alert v-if="exactFilterUnsupported && query.order_no_exact" variant="destructive"><AlertTitle>精确定位暂不可用</AlertTitle><AlertDescription>当前服务未确认精确筛选，请联系管理员更新服务端，或清除定位后使用普通搜索。</AlertDescription></Alert>
    <Alert v-if="customerFilterUnsupported && query.customer_account_id" variant="destructive"><AlertTitle>老板订单筛选暂不可用</AlertTitle><AlertDescription>当前服务未确认筛选范围，请联系管理员更新服务端，或清除老板筛选。</AlertDescription></Alert>
    <Alert v-if="createdDayUnsupported && query.created_day" variant="destructive"><AlertTitle>日期筛选暂不可用</AlertTitle><AlertDescription>当前服务未确认日期范围，请联系管理员更新服务端，或清除日期筛选。</AlertDescription></Alert>
    <Alert v-if="canReadOrders && invalidCustomerFilter" variant="destructive"><AlertTitle>老板筛选无效</AlertTitle><AlertDescription>请清除筛选，或从老板资料重新进入订单。 <Button variant="link" @click="clearInvalidCustomerFilter">清除无效筛选</Button></AlertDescription></Alert>
    <Alert v-if="canReadOrders && invalidCreatedDay" variant="destructive"><AlertTitle>订单日期无效</AlertTitle><AlertDescription>请清除日期，或从工作台重新选择。 <Button variant="link" @click="clearInvalidCreatedDay">清除无效日期</Button></AlertDescription></Alert>

    <crud-table v-if="canReadOrders && !invalidCustomerFilter && !invalidCreatedDay" ref="tableRef" :fetcher="fetchOrders" :query="query" :pagination-disabled="searchInputsPending" :external-error="customerFilterUnsupported || createdDayUnsupported" selectable table-scroll clear-on-reload>
      <template #toolbar="{ reload }">
        <div v-if="route.query.exclude_direct_test === '1'" class="od-customer-filter" role="status"><span>工作台口径：不含直接支付测试单</span><Button size="xs" @click="clearDashboardScope" variant="link">查看全部来源</Button></div>
        <div v-if="orderKind" class="od-customer-filter" role="status"><span>订单类型：{{ orderKind === 'GIFT' ? '礼物单' : '服务单' }}</span><Button size="xs" @click="clearOrderKind()" variant="link">清除类型</Button></div>
        <div class="od-filter-heading"><strong>筛选订单</strong><span>按订单号、老板信息或订单状态查找</span></div>
        <div v-if="query.customer_account_id" class="od-customer-filter" role="status"><span>老板账号 ID：{{ query.customer_account_id }}</span><Button size="xs" @click="query.customer_account_id = ''; reload()" variant="link">清除老板筛选</Button></div>
        <div v-if="query.created_day" class="od-customer-filter" role="status"><span>创建日期：{{ query.created_day }} · {{ createdDayTimezone || '时区待确认' }}</span><Popover ><PopoverTrigger as-child><button type="button" class="od-label-help" aria-label="日期和时区说明"><span  aria-hidden="true" class="od-inline-icon"><InfoFilled /></span></button></PopoverTrigger><PopoverContent>{{ "按服务端时区的自然日筛选；列表下单时间按本机时区显示。" }}</PopoverContent></Popover><Button size="xs" @click="query.created_day = ''; reload()" variant="link">清除日期</Button></div>
        <div v-if="query.order_no_exact" class="od-customer-filter" role="status"><span>精确查找订单 {{ query.order_no_exact }}</span><Button size="xs" @click="clearExactOrder(reload)" variant="link">清除定位</Button></div>
        <div v-if="query.overdue_pending" class="od-customer-filter" role="status"><span>已到接单期限 · 等待处理</span><span v-if="!query.aftersale_open && !query.actionable_staff">{{ overdueQueueDeadlineOrder ? '截止时间从早到晚' : '排序待确认' }}</span><Button size="xs" @click="clearStaffPending(reload)" variant="link">清除筛选</Button></div>
        <div v-if="query.legacy_team" class="od-customer-filter" role="status"><span>旧版双陪待核对 · 已接单或服务中</span><Button size="xs" @click="clearStaffPending(reload)" variant="link">清除筛选</Button></div>
        <div v-if="query.status && !query.overdue_pending && !query.actionable_staff && !(query.status === 'PENDING' && (query.payment_status === 'UNPAID' || (query.payment_status === 'PAID' && ['STAFF', 'PUBLIC'].includes(query.dispatch_route))))" class="od-customer-filter" role="status"><span>订单状态：{{ ORDER_STATUS[query.status]?.label || query.status }}</span><Button size="xs" @click="query.status = ''; reload()" variant="link">清除状态</Button></div>
        <div v-if="query.status === 'PENDING' && query.payment_status === 'UNPAID' && !query.actionable_staff" class="od-customer-filter" role="status"><span>未支付 · 待接单</span><Button size="xs" @click="query.status = ''; query.payment_status = ''; reload()" variant="link">清除状态</Button></div>
        <Popover ><PopoverTrigger as-child><Button size="xs" variant="link"><Button size="xs" :aria-pressed="Boolean(query.actionable_staff)" @click="showStaffPending(reload)">客服待派</Button></Button></PopoverTrigger><PopoverContent>{{ "切换到客服待派会清空当前搜索条件，仅显示已支付且符合待派筛选的订单。具体指派资格仍以订单详情和提交时校验为准。" }}</PopoverContent></Popover>
        <Popover v-if="query.actionable_staff"><PopoverTrigger as-child><Button size="xs" variant="link"><Button size="xs" @click="clearStaffPending(reload)">查看全部订单</Button></Button></PopoverTrigger><PopoverContent>{{ "清除当前搜索和状态条件，返回全部订单。" }}</PopoverContent></Popover>
        <span v-if="query.actionable_staff" class="od-filter-context" role="status">已筛选已支付、可派的客服订单 · {{ staffQueueDeadlineOrder ? '截止时间从早到晚，未记录排后' : '排序待确认' }}</span>
        <span v-else-if="query.status === 'PENDING' && query.payment_status === 'PAID' && ['STAFF', 'PUBLIC'].includes(query.dispatch_route)" class="od-filter-context" role="status">已筛选已支付、待接单的{{ query.dispatch_route === 'STAFF' ? '客服派单' : '公开接单' }}订单；不代表均可派单</span>
        <label class="od-filter-check"><Checkbox :model-value="query.aftersale_open === '1'" @update:model-value="value=>{query.aftersale_open=value===true?'1':'';reload()}" />处理中售后</label>
        <span v-if="query.aftersale_open && !query.actionable_staff" class="od-filter-context" role="status">处理中售后 · {{ aftersaleQueueOrder ? '按申请时间从早到晚' : '排序待确认' }} · 本机时间</span>
        <SuggestionInput v-model="query.order_no" :fetch-suggestions="querySuggest('order_no')" placeholder="订单号" label="按订单号搜索" @update:model-value="changeOrderNumber" @select="reload" @keyup.enter="reload" @clear="clearExactOrder(reload)" />
        <SuggestionInput v-model="query.customer_username" :fetch-suggestions="querySuggest('customer_username')" placeholder="老板账号" label="按老板账号搜索" @select="reload" @keyup.enter="reload" @clear="reload" />
        <Button @click="reload">查询</Button><Button variant="outline" :aria-expanded="advancedFiltersOpen" aria-controls="od-advanced-filters" @click="advancedFiltersOpen = !advancedFiltersOpen">{{ advancedFiltersOpen ? '收起筛选' : '更多筛选' }}<template v-if="advancedFilterCount">（{{ advancedFilterCount }}）</template></Button><Button v-if="hasOrderFilters" variant="ghost" @click="clearStaffPending(reload)">清除条件</Button>
        <span v-if="searchInputsPending" class="od-filter-context" role="status">筛选输入已更改，列表尚未更新；请点击查询</span>
        <div v-show="advancedFiltersOpen" id="od-advanced-filters" class="od-advanced-filters">
        <SuggestionInput v-model="query.customer_nickname" :fetch-suggestions="querySuggest('customer_nickname')" placeholder="老板昵称" label="按老板昵称搜索" @select="reload" @keyup.enter="reload" @clear="reload" />
        <SuggestionInput v-model="query.customer_phone" :fetch-suggestions="querySuggest('customer_phone')" placeholder="手机号" label="按老板当前手机号搜索" @select="reload" @keyup.enter="reload" @clear="reload" />
        <NativeSelect v-model="query.status" aria-label="订单状态" @update:model-value="clearActionableFilter(reload)"><NativeSelectOption value="">全部订单状态</NativeSelectOption><NativeSelectOption v-for="(v,k) in ORDER_STATUS" :key="k" :value="k">{{ v.label }}</NativeSelectOption></NativeSelect>
        <NativeSelect v-model="query.payment_status" aria-label="支付状态" @update:model-value="clearActionableFilter(reload)"><NativeSelectOption value="">全部支付状态</NativeSelectOption><NativeSelectOption v-for="(v,k) in PAYMENT_STATUS" :key="k" :value="k">{{ v.label }}</NativeSelectOption></NativeSelect>
        <NativeSelect v-model="query.dispatch_route" aria-label="接单路径" @update:model-value="clearActionableFilter(reload)"><NativeSelectOption value="">全部接单路径</NativeSelectOption><NativeSelectOption value="STAFF">客服派单</NativeSelectOption><NativeSelectOption value="PUBLIC">公开接单</NativeSelectOption></NativeSelect>
        <span class="od-filter-note">按当前账号资料查询；无业务账号的旧订单使用旧资料。</span>
        </div>
      </template>

      <template #mobile="{ rows, selected, toggle }"><div class="od-mobile-list" role="list" aria-label="订单列表"><article v-for="row in rows" :key="row.id" class="od-mobile-card" role="listitem"><div class="od-mobile-head"><Checkbox :model-value="selected.some((item: any) => item.id === row.id)" :aria-label="`选择订单 ${row.order_no}`" @update:model-value="value => toggle(row, value === true)" /><strong>{{ row.order_no }}</strong><Badge variant="outline">{{ row.status_display }}</Badge></div><p class="od-mobile-service">{{ row.service_name_snapshot || '服务待核对' }}</p><dl class="od-mobile-values"><div><dt><Popover><PopoverTrigger as-child><button type="button" class="od-label-help">老板 ⓘ</button></PopoverTrigger><PopoverContent>{{ customerIdentityHelp }}</PopoverContent></Popover></dt><dd><span>{{ row.customer_name || '待核对' }}</span><small class="od-customer-account">{{ customerAccountLabel(row) }}</small></dd></div><div><dt>陪玩</dt><dd>{{ providerListText(row) }}{{ orderTeamSize(row) === 2 ? ' · 双陪' : '' }}</dd></div><div><dt>金额</dt><dd>{{ amountToXaCoin(row.amount) }} 币 · {{ row.payment_status_display }}</dd></div><div v-if="query.aftersale_open"><dt>售后申请</dt><dd>{{ row.aftersale_open_at ? formatDateTime(row.aftersale_open_at) : '时间待核对' }}</dd></div><div><dt><Popover><PopoverTrigger as-child><button type="button" class="od-label-help">接单路径 ⓘ</button></PopoverTrigger><PopoverContent>{{ dispatchRouteHelp }}</PopoverContent></Popover></dt><dd>{{ dispatchRouteLabel(row) }} <Badge v-if="isStaffPending(row)" variant="outline">客服待派</Badge></dd></div><div v-if="isPaidPendingOrder(row)"><dt>接单截止</dt><dd>{{ row.auto_cancel_at ? formatDateTime(row.auto_cancel_at) : '未记录' }} <Badge v-if="row.pending_deadline_expired === true" variant="outline">已到期</Badge></dd></div></dl><p class="od-mobile-time">下单 {{ formatDateTime(row.created_at) }}</p><div class="od-mobile-actions"><Button variant="outline" @click="onDetail(row)">查看详情</Button><Button v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING' && row.payment_status === 'PAID' && row.dispatch_route !== 'STAFF'" variant="outline" :disabled="row.dispatch_message_allowed !== true" @click="onDispatchMessage(row)">一键发单</Button><Button v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING'" :disabled="!canAssignOrder(row)" @click="onAssign(row)">派单</Button><Button v-if="auth.hasPerm('order:dispatch') && row.status === 'GRABBED'" :disabled="orderActionBusy" @click="onStart(row)">开始</Button><Button v-if="auth.hasPerm('order:settle') && row.status === 'IN_SERVICE'" :disabled="orderActionBusy" @click="onComplete(row)">完成</Button><Button v-if="auth.hasPerm('order:dispatch') && row.status === 'IN_SERVICE'" variant="outline" @click="onTransfer(row)">转单</Button><Button v-if="auth.hasPerm('order:cancel') && row.status === 'PENDING'" variant="outline" :disabled="orderActionBusy" @click="onCancel(row)">取消订单</Button><Button v-if="auth.hasPerm('order:refund') && ['PAID', 'PARTIALLY_REFUNDED'].includes(row.payment_status) && ['GRABBED', 'IN_SERVICE', 'COMPLETED'].includes(row.status)" variant="outline" :disabled="orderActionBusy" @click="onRefund(row)">退款</Button></div><p v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING' && !canAssignOrder(row)" class="od-assign-hint">暂不可派，请查看详情并核对订单状态</p><p v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING' && row.payment_status === 'PAID' && row.dispatch_route !== 'STAFF' && row.dispatch_message_allowed !== true" class="od-assign-hint">暂不能发单：{{ row.dispatch_message_unavailable_reason || '请核对订单状态' }}</p></article></div></template>
      <template #desktop="{ rows, selected, toggle, toggleAll, allSelected, someSelected }"><div class="od-table-width"><Table><TableHeader><TableRow><TableHead><Checkbox :model-value="someSelected ? 'indeterminate' : allSelected" aria-label="选择本页订单" @update:model-value="value => toggleAll(value === true)" /></TableHead><TableHead>订单号</TableHead><TableHead>订单状态</TableHead><TableHead>支付状态</TableHead><TableHead v-if="query.aftersale_open">售后申请时间</TableHead><TableHead v-if="query.actionable_staff">接单截止时间</TableHead><TableHead><Popover><PopoverTrigger as-child><button type="button" class="od-label-help">老板 ⓘ</button></PopoverTrigger><PopoverContent>{{ customerIdentityHelp }}</PopoverContent></Popover></TableHead><TableHead>金额</TableHead><TableHead>陪玩</TableHead><TableHead>服务</TableHead><TableHead>负责客服</TableHead><TableHead><Popover><PopoverTrigger as-child><button type="button" class="od-label-help">接单路径 ⓘ</button></PopoverTrigger><PopoverContent>{{ dispatchRouteHelp }}</PopoverContent></Popover></TableHead><TableHead v-if="!query.actionable_staff">接单截止时间</TableHead><TableHead>下单时间</TableHead><TableHead>操作</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in rows" :key="row.id" :data-state="selected.some((item: any) => item.id === row.id) ? 'selected' : undefined"><TableCell><Checkbox :model-value="selected.some((item: any) => item.id === row.id)" :aria-label="`选择订单 ${row.order_no}`" @update:model-value="value => toggle(row, value === true)" /></TableCell><TableCell>{{ row.order_no }}</TableCell><TableCell><Badge variant="outline">{{ row.status_display }}</Badge></TableCell><TableCell><Badge variant="outline">{{ row.payment_status_display }}</Badge></TableCell><TableCell v-if="query.aftersale_open">{{ row.aftersale_open_at ? formatDateTime(row.aftersale_open_at) : '时间待核对' }}</TableCell><TableCell v-if="query.actionable_staff">{{ isPaidPendingOrder(row) ? (row.auto_cancel_at ? formatDateTime(row.auto_cancel_at) : '未记录') : '—' }}</TableCell><TableCell><div class="od-customer-identity"><span>{{ row.customer_name || '待核对' }}</span><small class="od-customer-account">{{ customerAccountLabel(row) }}</small></div></TableCell><TableCell>{{ amountToXaCoin(row.amount) }} 币</TableCell><TableCell>{{ providerListText(row) }}{{ orderTeamSize(row) === 2 ? ' · 双陪' : '' }}</TableCell><TableCell>{{ row.service_name_snapshot || '—' }}</TableCell><TableCell>{{ row.support_contact_name_snapshot || '—' }}</TableCell><TableCell>{{ dispatchRouteLabel(row) }} <Badge v-if="isStaffPending(row)" variant="outline">客服待派</Badge></TableCell><TableCell v-if="!query.actionable_staff">{{ isPaidPendingOrder(row) ? (row.auto_cancel_at ? formatDateTime(row.auto_cancel_at) : '未记录') : '—' }} <Badge v-if="row.pending_deadline_expired === true" variant="outline">已到期</Badge></TableCell><TableCell>{{ formatDateTime(row.created_at) }}</TableCell><TableCell><div class="od-row-actions"><Button variant="link" @click="onDetail(row)">详情</Button><Button v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING' && row.payment_status === 'PAID' && row.dispatch_route !== 'STAFF'" variant="link" :disabled="row.dispatch_message_allowed !== true" @click="onDispatchMessage(row)">一键发单</Button><Button v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING'" variant="link" :disabled="!canAssignOrder(row)" :title="assignTitle(row)" @click="onAssign(row)">派单</Button><Button v-if="auth.hasPerm('order:dispatch') && row.status === 'GRABBED'" variant="link" :disabled="orderActionBusy" @click="onStart(row)">开始</Button><Button v-if="auth.hasPerm('order:settle') && row.status === 'IN_SERVICE'" variant="link" :disabled="orderActionBusy" @click="onComplete(row)">完成</Button><Button v-if="auth.hasPerm('order:dispatch') && row.status === 'IN_SERVICE'" variant="link" @click="onTransfer(row)">转单</Button><Button v-if="auth.hasPerm('order:cancel') && row.status === 'PENDING'" variant="link" :disabled="orderActionBusy" @click="onCancel(row)">取消</Button><Button v-if="auth.hasPerm('order:refund') && ['PAID', 'PARTIALLY_REFUNDED'].includes(row.payment_status) && ['GRABBED', 'IN_SERVICE', 'COMPLETED'].includes(row.status)" variant="link" :disabled="orderActionBusy" @click="onRefund(row)">退款</Button></div><p v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING' && !canAssignOrder(row)" class="od-assign-hint">暂不可派，请查看详情并核对订单状态</p><p v-if="auth.hasPerm('order:dispatch') && row.status === 'PENDING' && row.payment_status === 'PAID' && row.dispatch_route !== 'STAFF' && row.dispatch_message_allowed !== true" class="od-assign-hint">暂不能发单：{{ row.dispatch_message_unavailable_reason || '请核对订单状态' }}</p></TableCell></TableRow></TableBody></Table></div></template>
    </crud-table>
  </div>

  <Dialog v-model:open="detailVisible"><DialogContent class="od-detail-sheet"><DialogHeader><DialogTitle>订单详情</DialogTitle><DialogDescription>查看订单、结算和履约记录。</DialogDescription></DialogHeader>
    <p v-if="detailLoading && !current" role="status">正在读取订单详情…</p>
    <template v-else>
    <Alert v-if="detailFailed" variant="destructive"><AlertDescription>{{ current ? '订单更新失败，当前显示上次结果，请重新加载' : '订单详情加载失败，请重试' }} <Button variant="link" :disabled="detailLoading" @click="refreshCurrentOrder">重新加载</Button></AlertDescription></Alert>
    <template v-if="current">
      <p v-if="detailLoading" class="od-detail-updating" role="status">正在更新订单信息…</p>
      <Button class="od-detail-refresh" variant="link" :disabled="detailLoading" @click="refreshCurrentOrder">刷新订单</Button>
      <Alert v-if="current.escort_mode === 'DOUBLE' && current.requires_boss_selection === false && ['GRABBED', 'IN_SERVICE'].includes(current.status)"><AlertTitle>此双陪单使用旧版履约流程</AlertTitle><AlertDescription>
        <p>订单可能由一名陪玩的截图推进；请与双方核对入队、结单凭证和服务进度。</p>
        <p>服务结束后，两位陪玩分别在报单中补齐入队、结单和战绩截图，再由客服审核。</p>
        <Button v-if="current.status === 'GRABBED' && current.payment_status === 'PAID' && auth.hasPerm('order:dispatch')" variant="outline" :disabled="orderActionBusy || detailLoading || detailFailed" @click="enableTeamEvidence">核实后启用双方凭证</Button></AlertDescription></Alert>
      <h3 class="od-detail-heading">订单信息</h3>
      <DetailList>
        <DetailItem label="订单号">{{ current.order_no }}</DetailItem>
        <DetailItem label="状态">{{ current.status_display }}</DetailItem>
        <DetailItem label="支付状态">{{ current.payment_status_display }}</DetailItem>
        <DetailItem v-if="current.reservation_origin" label="原预约时段">{{ reservationDate(current.reservation_origin.start_time) }} 至 {{ reservationDate(current.reservation_origin.end_time) }}（北京时间）<Button variant="link" @click="router.push({ path: '/reservations', query: { find_reservation: current.reservation_origin.id } })">查看预约</Button></DetailItem>
        <DetailItem><template #label><Popover><PopoverTrigger as-child><button type="button" class="od-label-help">接单路径 ⓘ</button></PopoverTrigger><PopoverContent>{{ dispatchRouteHelp }}</PopoverContent></Popover></template>{{ dispatchRouteLabel(current) }}<Badge variant="outline" v-if="isStaffPending(current)">客服待派</Badge></DetailItem>
        <DetailItem v-if="current.status === 'PENDING' && current.payment_status === 'PAID' && current.dispatch_route === 'PUBLIC' && typeof current.requires_boss_selection === 'boolean'" label="公开接单方式">{{ current.requires_boss_selection ? '老板选择陪玩' : '陪玩直接接单' }}</DetailItem>
        <DetailItem v-if="current.application_progress" label="报名进度">
          {{ current.application_progress.total }} 组报名 · {{ current.application_progress.waiting_partner }} 组等待队友确认
          <Popover><PopoverTrigger as-child><button type="button" class="od-detail-help" aria-label="报名进度说明">?</button></PopoverTrigger><PopoverContent>仅统计当前报名；队友确认后仍需以老板端的资格校验和选择结果为准。</PopoverContent></Popover>
        </DetailItem>
      <DetailItem v-if="isPaidPendingOrder(current)" label="接单截止时间">{{ current.auto_cancel_at ? formatDateTime(current.auto_cancel_at) : '未记录' }}<Badge variant="outline" v-if="current.pending_deadline_expired === true">已到期，等待处理</Badge></DetailItem>
        <DetailItem label="老板">{{ current.customer_name || '待核对' }}</DetailItem>
        <DetailItem label="老板账号">{{ current.customer_username || '待核对' }}<Popover><PopoverTrigger as-child><button type="button" class="od-label-help od-identity-help" aria-label="老板资料说明">ⓘ</button></PopoverTrigger><PopoverContent>{{ customerIdentityHelp }}</PopoverContent></Popover></DetailItem>
        <DetailItem label="下单手机号">{{ current.customer_phone_snapshot || '未记录' }}</DetailItem>
        <DetailItem label="陪玩选择">{{ ({ ANY: '不限', MALE: '男陪', FEMALE: '女陪' } as any)[current.provider_gender || 'ANY'] }}</DetailItem>
        <DetailItem label="成交陪玩人数">{{ orderTeamSize(current) === 2 ? '2 人（双陪）' : orderTeamSize(current) === 1 ? '1 人（单陪）' : '等待服务端确认' }}</DetailItem>
        <DetailItem label="陪玩">
          <div v-for="(person, index) in currentProviderIdentities(current)" :key="person.id || index" class="od-person-identity">{{ providerIdentityLabel(person) }}</div>
          <span v-if="!currentProviderIdentities(current).length">—</span>
          <Popover v-if="currentProviderIdentities(current).some((person: any) => person.provider_no || person.provider_username)"><PopoverTrigger as-child><button type="button" class="od-detail-help" aria-label="陪玩身份说明">?</button></PopoverTrigger><PopoverContent>编号与账号为读取时的资料，不是订单创建时快照。</PopoverContent></Popover>
        </DetailItem>
        <DetailItem v-if="current.team_progress" :label="current.team_progress.stage === 'ENTRY' ? '入队截图进度' : '结单截图进度'">
          <div>{{ current.team_progress.submitted_count }}/{{ current.team_progress.total_count }} 已提交</div>
          <div v-for="member in current.team_progress.members" :key="member.id">{{ member.nickname }} · {{ member.submitted ? '已提交' : '等待提交' }}</div>
        </DetailItem>
        <DetailItem label="服务">{{ current.service_name_snapshot }}</DetailItem>
        <DetailItem v-if="current.is_gift_snapshot" label="赠礼来源">{{ current.source_order_no ? `服务订单 ${current.source_order_no}` : '直接赠送' }}</DetailItem>
        <DetailItem v-if="current.provider_requirement?.label" label="接单范围">{{ current.provider_requirement.label }}</DetailItem>
        <DetailItem v-if="current.gameplay_snapshot?.name" label="玩法规格">{{ current.gameplay_snapshot.name }} · {{ current.gameplay_snapshot.platform_name }} · {{ current.gameplay_snapshot.security_level_name }}</DetailItem>
        <DetailItem v-if="current.roulette_result?.name" label="转盘目标">{{ current.roulette_result.name }}：{{ current.roulette_result.description }}</DetailItem>
        <DetailItem v-if="current.gameplay_snapshot?.rules" label="规则快照">{{ current.gameplay_snapshot.rules }}</DetailItem>
        <DetailItem label="负责客服">{{ current.support_contact_name_snapshot || '-' }}</DetailItem>
      </DetailList>
      <h3 class="od-detail-heading">游戏资料</h3>
      <DetailList v-if="current.game_region || current.game_nickname || current.game_uid">
        <DetailItem label="区服"><span class="od-game-value">{{ current.game_region || '未填写' }}</span></DetailItem>
        <DetailItem label="游戏昵称"><span class="od-game-value">{{ current.game_nickname || '未填写' }}</span></DetailItem>
        <DetailItem label="游戏 ID"><span class="od-game-value">{{ current.game_uid || '未填写' }}</span></DetailItem>
      </DetailList>
      <p v-else class="od-detail-empty">未填写游戏资料</p>
      <h3 class="od-detail-heading">金额与收益</h3>
      <DetailList>
        <DetailItem label="单价">{{ amountToXaCoin(current.unit_price_snapshot) }} 币</DetailItem>
        <DetailItem label="局数">{{ current.game_rounds }}</DetailItem>
        <DetailItem v-if="current.original_amount" label="原价">{{ amountToXaCoin(current.original_amount) }} 币</DetailItem>
        <DetailItem v-if="current.boss_discount" label="老板折扣">-{{ amountToXaCoin(current.boss_discount) }} 币</DetailItem>
        <DetailItem v-if="current.promo_discount" label="活动折扣">-{{ amountToXaCoin(current.promo_discount) }} 币</DetailItem>
        <DetailItem v-if="current.coupon_discount" label="优惠券折扣">-{{ amountToXaCoin(current.coupon_discount) }} 币</DetailItem>
        <DetailItem label="服务成交金额">{{ amountToXaCoin(current.amount) }} 币</DetailItem>
        <DetailItem v-if="current.promotion_title" label="命中活动">{{ current.promotion_title }}</DetailItem>
        <DetailItem v-if="current.commission_type === 'FIXED'" label="整单固定抽成">{{ amountToXaCoin(current.commission_fixed) }} 币 · 整单仅扣一次，由参与陪玩分摊</DetailItem>
        <template v-if="current.providers && current.providers.length">
          <DetailItem
            v-for="(p, i) in current.providers"
            :key="p.id"
            :label="current.providers.length > 1 ? `打手${i + 1}` : '打手'"
          >
            {{ providerIdentityLabel(p) }}{{ p.released_at ? '（已转出）' : '' }} · {{ orderProviderCommissionDesc(p, current.commission_type === 'FIXED') }} · {{ ['REFUNDED', 'PARTIALLY_REFUNDED'].includes(current.payment_status) ? (historicalSettlementRecorded(current, p) ? '原结算收益' : '原预计收益') : current.settlement_status === 'SETTLED' ? '实得' : '预计收益' }} {{ amountToXaCoin(p.provider_income) }} 币
            <span v-if="!['REFUNDED', 'PARTIALLY_REFUNDED'].includes(current.payment_status) && current.settlement_status === 'SETTLED'" class="od-log-op"> · 已结算</span>
          </DetailItem>
        </template>
        <template v-else>
          <DetailItem v-if="current.commission_type !== 'FIXED'" label="平台抽成率">{{ current.commission_rate }}%</DetailItem>
          <DetailItem :label="['REFUNDED', 'PARTIALLY_REFUNDED'].includes(current.payment_status) ? (historicalSettlementRecorded(current) ? '原结算陪玩收益' : '原预计陪玩收益') : current.settlement_status === 'SETTLED' ? '陪玩实得' : '预计陪玩收益'">{{ amountToXaCoin(current.provider_income) }} 币</DetailItem>
        </template>
        <DetailItem label="推荐人">{{ current.inviter_name || '-' }}</DetailItem>
        <DetailItem label="推荐人分佣">{{ amountToXaCoin(current.inviter_commission) }} 币</DetailItem>
        <DetailItem :label="['REFUNDED', 'PARTIALLY_REFUNDED'].includes(current.payment_status) ? '原店铺留存快照' : '店铺留存快照'">{{ amountToXaCoin(current.shop_income) }} 币</DetailItem>
        <DetailItem label="收益状态">{{ settlementStatusLabel(current.settlement_status) }}</DetailItem>
        <DetailItem v-if="current.settlement_review_reason" label="结算核对">{{ current.settlement_review_reason }} <router-link v-if="auth.hasPerm('transaction:view') && current.order_no" :to="{ name: 'transactions', query: { order_no: current.order_no } }">查看订单流水</router-link></DetailItem>
        <DetailItem v-if="current.settlement_status === 'SETTLED' && current.settled_at" label="原结算时间">{{ formatDateTime(current.settled_at) }}</DetailItem>
      </DetailList>
      <h3 class="od-detail-heading">备注与时间</h3>
      <DetailList>
        <DetailItem label="备注">{{ current.remark || '-' }}</DetailItem>
        <DetailItem label="取消原因">{{ current.cancel_reason || '-' }}</DetailItem>
        <DetailItem label="售后截止">{{ formatDateTime(current.aftersale_deadline) }}</DetailItem>
        <DetailItem label="记录的实销确认时间">
          {{ current.consumption_confirmed_at ? formatDateTime(current.consumption_confirmed_at) : '暂无确认记录' }}
          <span v-if="current.consumption_confirmed_at && consumptionReversed(current)"> · 已冲回</span>
          <Popover><PopoverTrigger as-child><button type="button" class="od-detail-help" aria-label="实销确认时间说明">?</button></PopoverTrigger><PopoverContent>此处显示历史确认时间；退款后的实销冲回以退款明细为准。</PopoverContent></Popover>
        </DetailItem>
        <DetailItem v-if="!current.consumption_confirmed_at && current.consumption_ready_at" label="报单计时节点">
          {{ formatDateTime(current.consumption_ready_at) }}
          <Popover><PopoverTrigger as-child><button type="button" class="od-detail-help" aria-label="报单计时节点说明">?</button></PopoverTrigger><PopoverContent>按有效报单提交时间计算。到达此时间不代表实销已确认，实际仍须满足订单、审核及售后等条件。</PopoverContent></Popover>
        </DetailItem>
        <DetailItem label="下单时间">{{ formatDateTime(current.created_at) }}</DetailItem>
      </DetailList>
      <Alert v-if="current.payment_status === 'REFUNDED' && !current.settled_at"><AlertDescription>订单已退款，不再结算以上预计收益</AlertDescription></Alert>
      <section v-if="current.refund_summary" class="od-refund-section" aria-label="实际退款与追回结果">
        <h3>实际退款与追回</h3>
        <DetailList>
          <DetailItem v-if="current.refund_summary.original_amount !== undefined" label="原实付">{{ amountToXaCoin(current.refund_summary.original_amount) }} 币</DetailItem>
          <DetailItem v-if="current.refund_summary.remaining_amount !== undefined" :label="current.status === 'CANCELLED' ? '订单保留金额' : '剩余可退'">{{ amountToXaCoin(current.refund_summary.remaining_amount) }} 币</DetailItem>
          <DetailItem v-if="current.refund_summary.refund_count !== undefined" label="退款笔数">{{ current.refund_summary.refund_count }} 笔</DetailItem>
          <DetailItem label="累计退回老板">{{ amountToXaCoin(current.refund_summary.amount) }} 币</DetailItem>
          <DetailItem v-if="current.refund_summary.recovered_amount !== undefined" label="累计追回收益">{{ amountToXaCoin(current.refund_summary.recovered_amount) }} 币</DetailItem>
          <DetailItem v-if="!current.refund_summary.records" label="退款时间">{{ formatDateTime(current.refund_summary.created_at) }}</DetailItem>
        </DetailList>
        <article v-for="record in (current.refund_summary.records || [current.refund_summary])" :key="record.id" class="od-refund-record">
          <h4>退款 #{{ record.id }} · {{ amountToXaCoin(record.amount) }} 币</h4>
          <p class="od-refund-note">{{ formatDateTime(record.created_at) }} · {{ record.reason || '—' }}</p>
          <p v-if="record.recovered_amount !== undefined" class="od-refund-note">本笔实际追回收益：{{ amountToXaCoin(record.recovered_amount) }} 币</p>
          <div v-for="share in record.negotiated_allocation || []" :key="share.key" class="od-refund-line">
            <strong>{{ share.name }}{{ share.account_id ? ` · 账号 #${share.account_id}` : '' }} · {{ share.settled ? '原已结算' : '原待结算' }}</strong>
            <span>原份额 {{ amountToXaCoin(share.entitlement) }} 币 · 协商承担 {{ amountToXaCoin(share.amount) }} 币 · 剩余 {{ amountToXaCoin(share.retained) }} 币</span>
          </div>
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
            <span v-if="record.benefit_effects.gift_naming">{{ record.benefit_effects.gift_naming.remaining_paid_grants ? `本次处理后冠名截止：${formatDateTime(record.benefit_effects.gift_naming.expires_at)}` : '本次处理后该冠名无剩余赠礼时长' }}</span>
            <span v-if="record.benefit_effects.consumed_coupon_ids?.length">保留已用优惠券历史：#{{ record.benefit_effects.consumed_coupon_ids.join('、#') }}</span>
            <span v-if="record.benefit_effects.delivered_claim_ids?.length">保留已交付权益历史：#{{ record.benefit_effects.delivered_claim_ids.join('、#') }}</span>
            <span v-if="record.benefit_effects.consumed_benefits_policy">{{ record.benefit_effects.consumed_benefits_policy }}</span>
          </section>
        </article>
      </section>
      <h3 class="od-detail-heading">售后</h3>
      <Alert v-if="aftersaleFailed"><AlertDescription>{{ aftersales.length ? '售后更新失败，以下为上次结果，请重新加载后处理' : '售后信息加载失败，请重新加载' }} <Button variant="link" :disabled="detailLoading" @click="refreshCurrentOrder">重新加载</Button></AlertDescription></Alert>
      <p v-else-if="!aftersales.length && !detailLoading" class="od-detail-empty">暂无售后记录</p>
      <div v-for="ticket in aftersales" :key="ticket.id" class="ap-section">
        <p>售后 #{{ ticket.id }} · {{ aftersaleStatusText(ticket.status) }}</p>
        <p>申请说明：{{ ticket.reason }}</p>
        <p v-if="ticket.resolution">处理说明：{{ ticket.resolution }}</p>
        <template v-if="ticket.status === 'OPEN'"><p v-if="current.amount === 0">本单实付为 0，无需退款；请处理售后咨询。</p><p v-else-if="current.payment_status === 'REFUNDED'">订单已全额退款，请继续处理售后咨询。</p><Button v-if="auth.hasPerm('order:settle')" variant="outline" :disabled="orderActionBusy || detailLoading || detailFailed || aftersaleFailed" @click="processAfterSale(ticket,'resolve')">处理完成</Button><Button v-if="auth.hasPerm('order:refund') && current.amount !== 0 && current.payment_status !== 'REFUNDED'" variant="destructive" :disabled="orderActionBusy || detailLoading || detailFailed || aftersaleFailed" @click="processAfterSale(ticket,'refund')">退款结案</Button></template>
        <details v-if="pastAftersales(ticket).length" class="od-aftersale-history">
          <summary>历史处理记录（{{ pastAftersales(ticket).length }}）</summary>
          <div v-for="(item, index) in pastAftersales(ticket)" :key="index" class="od-aftersale-history-item">
            <p>{{ aftersaleStatusText(item.status) }} · {{ formatDateTime(item.resolved_at || item.created_at) }}</p>
            <p>申请说明：{{ item.reason }}</p>
            <p v-if="item.resolution">处理说明：{{ item.resolution }}</p>
          </div>
        </details>
      </div>

      <h3 class="od-detail-heading">状态流水</h3>
      <Alert v-if="logsFailed"><AlertDescription>{{ logs.length ? '状态流水更新失败，以下为上次结果' : '状态流水加载失败，请重新加载' }} <Button variant="link" :disabled="detailLoading" @click="refreshCurrentOrder">重新加载</Button></AlertDescription></Alert>
      <p v-else-if="!logs.length && !detailLoading" class="od-detail-empty">暂无状态流水</p>
      <ol class="od-logs">
        <li v-for="log in logs" :key="log.id">
          {{ log.action_display }}
          <span v-if="log.from_status && log.to_status && log.from_status !== log.to_status">（{{ ORDER_STATUS[log.from_status]?.label || log.from_status }} → {{ ORDER_STATUS[log.to_status]?.label || log.to_status }}）</span>
          <span v-if="log.reason"> · {{ log.reason }}</span>
          <span v-if="log.operator_name" class="od-log-op"> · {{ log.operator_name }}</span>
        <time>{{ formatDateTime(log.created_at) }}</time></li>
      </ol>
    </template>
    </template>
    <div v-if="current && current.status === 'PENDING' && auth.hasPerm('order:dispatch')" class="od-detail-actions">
        <span v-if="detailLoading || detailFailed" class="od-detail-action-note">{{ detailLoading ? '订单更新中' : '请重新加载订单后操作' }}</span>
        <span v-else-if="!canAssignOrder(current)" class="od-detail-action-note">暂不可派，请核对订单状态</span>
        <Button variant="outline" :disabled="detailLoading" @click="refreshCurrentOrder">刷新</Button>
        <Button :disabled="detailLoading || detailFailed || !canAssignOrder(current) || assigning" @click="onDetailAssign">指派陪玩</Button>
      </div>
  </DialogContent></Dialog>

  <Dialog :open="afterSalePromptOpen" @update:open="value => { if (!value) settleAfterSalePrompt(null) }"><DialogContent><DialogHeader><DialogTitle>处理售后</DialogTitle><DialogDescription>订单 {{ afterSalePromptOrder }} · 售后 #{{ afterSalePromptTicket }}。确认处理只结束售后，不会自动退款。</DialogDescription></DialogHeader><label class="od-aftersale-resolution">处理说明<Textarea v-model="afterSaleResolution" maxlength="1000" :rows="4" placeholder="填写处理说明（2–1000 字）" /></label><p v-if="afterSalePromptError" role="alert">{{ afterSalePromptError }}</p><DialogFooter><Button variant="outline" @click="settleAfterSalePrompt(null)">返回</Button><Button @click="confirmAfterSalePrompt">确认处理</Button></DialogFooter></DialogContent></Dialog>

  <Dialog :open="refundVisible" @update:open="value => { if (!value && !refundSubmitting) refundVisible = false }"><DialogContent class="od-refund-dialog" :show-close-button="!refundSubmitting" @interact-outside="event => event.preventDefault()" @escape-key-down="event => { if (refundSubmitting) event.preventDefault() }"><DialogHeader><DialogTitle>{{ refundTarget?.ticketId ? '售后退款 · 资金复核' : '订单退款 · 资金复核' }}</DialogTitle><DialogDescription>先核对金额与收益追回，再确认退款。</DialogDescription></DialogHeader>
    <DetailList v-if="refundLimits">
      <DetailItem label="原实付">{{ amountToXaCoin(refundLimits.original) }} 币</DetailItem>
      <DetailItem label="累计已退">{{ amountToXaCoin(refundLimits.refunded) }} 币</DetailItem>
      <DetailItem label="剩余可退">{{ amountToXaCoin(refundLimits.remaining) }} 币</DetailItem>
    </DetailList>
    <label class="od-refund-input">本次退款金额（兴安币，最多 1 位小数）<Input v-model="refundAmountCoins" inputmode="decimal" :disabled="refundSubmitting || refundResultUnknown || !refundLimits" placeholder="请输入本次退款金额" /></label><Button variant="link" :disabled="refundSubmitting || refundResultUnknown || !refundLimits" @click="useRefundRemainder">填入全部余款</Button>
    <div v-if="negotiatedRows.length" class="od-refund-line">
      <strong>服务中转单协商退款</strong>
      <p>订单将终止。请先核对协商资料，再逐项填写承担金额，合计须等于退款金额；待结算份额扣除承担后按现有规则结算。本次协商终止不强制结单截图。</p>
      <label v-for="row in negotiatedRows" :key="row.key" class="od-refund-input">{{ row.name }}{{ row.account_id ? ` · 账号 #${row.account_id}` : '' }} · {{ row.settled ? '已结算' : '待结算' }} · 原份额 {{ amountToXaCoin(row.entitlement) }} 币
        <Input v-model="row.input" inputmode="decimal" :disabled="refundSubmitting || refundResultUnknown" placeholder="填写承担金额（可填 0）" />
      </label>
      <label><Checkbox :model-value="negotiatedReviewed" :disabled="refundSubmitting || refundResultUnknown" @update:model-value="value => negotiatedReviewed = value === true" /> 我已核对协商资料和各方承担金额</label>
    </div>
    <p v-if="refundLoading" role="status">正在试算退款…</p>
    <template v-else>
      <Alert v-if="refundError"><AlertDescription>{{ refundError }}</AlertDescription></Alert>
      <template v-if="refundPreview">
        <DetailList>
          <DetailItem label="订单">#{{ refundPreview.order_id }}</DetailItem>
          <DetailItem label="结算状态">{{ refundPreview.is_settled ? '已结算，须追回收益' : '尚未结算' }}</DetailItem>
          <DetailItem label="本次退回老板">{{ amountToXaCoin(refundPreview.refund_amount) }} 币</DetailItem>
          <DetailItem label="本次需追回收益">{{ amountToXaCoin(refundPreview.recovery_total) }} 币</DetailItem>
          <DetailItem :label="negotiatedRows.length ? '本次退款后保留金额' : '本次退款后余款'">{{ amountToXaCoin(refundPreview.remaining_after_refund) }} 币</DetailItem>
          <DetailItem label="退款范围">{{ negotiatedRows.length ? '协商退款，订单终止' : refundPreview.full_refund ? '退还全部剩余款项' : '部分退款，订单保留' }}</DetailItem>
        </DetailList>
        <p class="od-refund-note">金额及来源由服务端试算。确认时会重新核对；资金变化后须重新复核。</p>
        <p class="od-refund-note">{{ negotiatedRows.length ? '按上述协商结果分摊并追回已入账收益。' : '本次按服务端比例方案追回收益。' }}按规则调整积分、实销及相应权益，处理结果可在订单详情核对。</p>
        <p v-if="refundPreview.evidence_error" role="alert">{{ refundPreview.evidence_error }}</p>
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
        <Alert v-if="!refundPreview.eligible" variant="destructive"><AlertDescription>当前不满足退款条件，无法确认退款</AlertDescription></Alert>
        <ul v-if="refundPreview.shortfalls.length" class="od-refund-shortfalls" aria-label="资金不足明细">
          <li v-for="item in refundPreview.shortfalls" :key="item.wallet_id">钱包 #{{ item.wallet_id }}{{ item.account_id ? `（账号 #${item.account_id}）` : '（平台）' }}：需 {{ amountToXaCoin(item.required) }} 币，可追回 {{ amountToXaCoin(item.available) }} 币，不足 {{ amountToXaCoin(item.shortfall) }} 币</li>
        </ul>
      </template>
      <label class="od-refund-input">退款原因 / 处理说明（至少 2 个字）<Textarea v-model="refundReason" :rows="3" :maxlength="refundTarget?.ticketId ? 1000 : 255" :disabled="refundSubmitting || refundResultUnknown" /></label>
    </template>
    <DialogFooter><Button v-if="refundResultUnknown" variant="outline" @click="reviewUnknownRefund">关闭并查看订单</Button><Button v-else variant="outline" :disabled="refundSubmitting" @click="refundVisible = false">取消</Button><Button variant="outline" :disabled="refundLoading || refundSubmitting || refundResultUnknown" @click="loadRefundPreview">{{ refundLoading ? '试算中…' : '刷新试算' }}</Button><Button variant="destructive" :disabled="!refundCanSubmit" @click="submitRefund">{{ refundSubmitting ? '处理中…' : refundPreview?.full_refund ? (refundPreview.refunded_amount ? '确认退还余款' : '确认全额退款') : '确认本次退款' }}</Button></DialogFooter></DialogContent></Dialog>

  <DispatchMessageDialog v-model="dispatchMessageVisible" :order-id="dispatchMessageOrderId" />
  <Dialog :open="timeoutConfigVisible" @update:open="value => { if (!value) closeTimeoutConfig(() => { timeoutConfigVisible = false }) }"><DialogContent class="od-config-dialog od-config-dialog--small" :show-close-button="!timeoutConfigSaving" @interact-outside="event => event.preventDefault()" @escape-key-down="event => { if (timeoutConfigSaving) event.preventDefault() }"><DialogHeader><DialogTitle>待接单时限设置</DialogTitle><DialogDescription>设置待接单订单的处理时限。</DialogDescription></DialogHeader><TimeoutConfig v-if="timeoutConfigVisible" ref="timeoutConfigRef" @busy="timeoutConfigSaving = $event" /></DialogContent></Dialog>
  <Dialog :open="trafficConfigVisible" @update:open="value => { if (!value) closeTrafficConfig(() => { trafficConfigVisible = false }) }"><DialogContent class="od-config-dialog od-config-dialog--medium" :show-close-button="!trafficConfigSaving" @interact-outside="event => event.preventDefault()" @escape-key-down="event => { if (trafficConfigSaving) event.preventDefault() }"><DialogHeader><DialogTitle>峰谷限流与监控</DialogTitle><DialogDescription>查看并调整订单流量配置。</DialogDescription></DialogHeader><TrafficConfig v-if="trafficConfigVisible" ref="trafficConfigRef" @busy="trafficConfigSaving = $event" /></DialogContent></Dialog>
  <Dialog :open="dispatchConfigVisible" @update:open="value => { if (!value) closeDispatchConfig(() => { dispatchConfigVisible = false }) }"><DialogContent class="od-config-dialog od-config-dialog--large" @interact-outside="event => event.preventDefault()"><DialogHeader><DialogTitle>KOOK 派单配置</DialogTitle><DialogDescription>管理 KOOK 派单相关设置。</DialogDescription></DialogHeader><KookDispatchConfig v-if="dispatchConfigVisible" ref="dispatchConfigRef" /></DialogContent></Dialog>
  <Dialog :open="dispatchVisible" @update:open="value=>{if(!dispatching&&!dispatchConfirming&&!dispatchResultUnknown)dispatchVisible=value}"><DialogContent class="od-dispatch-dialog" :show-close-button="!dispatching && !dispatchConfirming && !dispatchResultUnknown" @interact-outside.prevent @escape-key-down="event=>{if(dispatching||dispatchConfirming||dispatchResultUnknown)event.preventDefault()}"><DialogHeader><DialogTitle>客服快捷派单</DialogTitle><DialogDescription>核对老板、服务、陪玩和预计扣款后派单。</DialogDescription></DialogHeader><fieldset :disabled="dispatching || dispatchConfirming || dispatchResultUnknown" :inert="dispatchConfirming || undefined" class="od-dispatch-form"><div class="od-form-section">
        <div class="od-dispatch-heading"><p class="od-section-title">下单信息</p></div>
        <div class="od-input-grid"><div  class="od-input-col" :style="{gridColumn:'span '+(12)}"><div  class="od-dispatch-field "><span>{{ "下单老板" }}</span><SearchSelect v-model="dispatchForm.customer_id" placeholder="输入账号/昵称搜索" :loading="bossLoading" :options="bossOptions.map((u:any)=>({value:u.id,label:`${u.nickname || u.username} · 账号 ${u.username || '未记录'}（查询时余额 ${amountToXaCoin(u.wallet_balance)}币）`}))" label="下单老板"  remote @search="searchBoss" @change="value=>onBossChange(Number(value))" /><span v-if="bossSearchError" class="hint">{{ bossSearchError }} <Button size="xs" @click="searchBoss(bossSearchKeyword)" variant="link">重新搜索</Button></span></div></div><div  class="od-input-col" :style="{gridColumn:'span '+(12)}"><div  class="od-dispatch-field "><span>{{ "服务" }}</span><SearchSelect v-model="dispatchForm.service_id" placeholder="选择服务" :loading="serviceLoading" :options="serviceOptions.map((s:any)=>({value:s.id,label:serviceOptionLabel(s)}))" label="服务"  @change="value=>onServiceChange(Number(value))" /></div></div></div>
      </div><div class="od-form-section">
        <div class="od-dispatch-heading"><p class="od-section-title">陪玩与抽成</p></div>
        <div class="od-input-grid"><div  class="od-input-col" :style="{gridColumn:'span '+(12)}"><div  class="od-dispatch-field "><span>{{ "局数" }}</span><NumberField :step-snapping="false" v-model="dispatchForm.game_rounds" :min="1" :step="1"><NumberFieldContent><NumberFieldDecrement /><NumberFieldInput aria-label="局数" /><NumberFieldIncrement /></NumberFieldContent></NumberField></div></div><div  class="od-input-col" :style="{gridColumn:'span '+(12)}"><div  class="od-dispatch-field ap-field--full"><span>{{ "套餐陪玩人数" }}</span><Badge  variant="outline">{{ dispatchProviderCount === 2 ? '双陪 · 2 人' : dispatchProviderCount === 1 ? '单陪 · 1 人' : '请先选择服务套餐' }}</Badge><span class="hint">{{ dispatchProviderCount === 2 ? '须指定两名不同陪玩' : dispatchProviderCount === 1 ? '留空则进入待接单大厅' : '人数由套餐确定' }}</span></div></div></div>

        <div v-if="currentService?.service_type === 'COMPANION'" class="od-dispatch-field "><span>{{ "陪玩选择" }}</span><NativeSelect v-model="dispatchForm.provider_gender" @change="onDispatchGenderChange" aria-label="陪玩选择"><NativeSelectOption value="ANY">{{ "不限" }}</NativeSelectOption><NativeSelectOption value="MALE">{{ "男陪" }}</NativeSelectOption><NativeSelectOption value="FEMALE">{{ "女陪" }}</NativeSelectOption></NativeSelect></div>
        <Alert v-if="currentService?.is_gift"><AlertDescription><strong>{{ "礼物沿用独立比例规则，不使用订单自定义抽成。" }}</strong></AlertDescription></Alert>
        <div v-else class="od-dispatch-field ap-field--full"><Popover ><PopoverTrigger as-child><button type="button" class="od-label-help">本单抽成方式 <span  aria-hidden="true" class="od-inline-icon"><InfoFilled /></span></button></PopoverTrigger><PopoverContent>{{ dispatchQuote?.commission_locked ? '沿用自动规则；选择百分比或固定金额可覆盖本单抽成' : '百分比按陪玩设置；固定金额按整单设置' }}</PopoverContent></Popover><RadioGroup v-model="dispatchCommissionMode" :disabled="dispatching" class="od-commission-choices"><label class="od-commission-option"><RadioGroupItem value="AUTO" />沿用配置</label><label class="od-commission-option"><RadioGroupItem value="PERCENT" />百分比</label><label class="od-commission-option"><RadioGroupItem value="FIXED" />整单固定金额</label></RadioGroup></div>
        <div v-if="dispatchCommissionMode === 'FIXED' && !currentService?.is_gift" class="od-dispatch-field ap-field--full"><span>{{ "整单固定抽成（兴安币）" }}</span><NumberField :step-snapping="false" v-model="dispatchCommissionFixed" :disabled="dispatching" :min="0" :max="214748364.7" :step="0.1" :format-options="{minimumFractionDigits:1,maximumFractionDigits:1}"><NumberFieldContent><NumberFieldDecrement /><NumberFieldInput aria-label="整单固定抽成（兴安币）" /><NumberFieldIncrement /></NumberFieldContent></NumberField><span class="hint">整单扣一次，双陪分摊；0 表示不抽成</span><p v-if="dispatchCommissionError" role="alert" class="od-field-error">{{ dispatchCommissionError }}</p></div>
        <p v-if="dispatchQuote" class="od-provider-tip">{{ dispatchQuote.commission_source_label || '系统试算' }}<template v-if="dispatchFixed"> · 整单固定抽成 {{ amountToXaCoin(dispatchQuote.commission_fixed_total) }} 币（已按折后实付封顶）</template></p>
        <Alert v-if="dispatchForm.escort_mode === 'DOUBLE' && dispatchForm.providers[0]?.provider_id && dispatchForm.providers[0]?.provider_id === dispatchForm.providers[1]?.provider_id"><AlertDescription><strong>{{ "双陪需选择两名不同的陪玩" }}</strong><p>{{ "请更换其中一名陪玩，再核对金额并派单。" }}</p></AlertDescription></Alert>
        <div
          v-for="(slot, idx) in dispatchForm.providers"
          :key="idx"
          class="od-provider-row"
        >
          <div class="od-input-grid"><div  class="od-input-col" :style="{gridColumn:'span '+(dispatchFixed ? 24 : 16)}"><div  class="od-dispatch-field "><span>{{ `打手 ${idx + 1}` }}</span><SearchSelect v-model="slot.provider_id" :placeholder="dispatchForm.escort_mode === 'SINGLE' ? '留空则进入待接单大厅' : '选择打手'" :loading="providerLoading" :options="providerOptions.map((p:any)=>({value:p.user,label:`${assignProviderLabel(p)} · ${p.escort_no || '编号未记录'} · ${(p.qualifications || []).map((q: any) => q.label).join(' / ') || '资格待配置'}`}))" :label="`打手 ${idx + 1}`"  remote @search="searchProvider" clearable @change="onProviderPicked(idx)" /></div></div><div v-if="!dispatchFixed" class="od-input-col" :style="{gridColumn:'span '+(8)}"><div  class="od-dispatch-field "><span>{{ dispatchPendingPercentage() ? '待接单陪玩抽成比例（%）' : '抽成比例（%）' }}</span><NumberField :step-snapping="false" v-if="!dispatchPendingPercentage()" :key="`provider-rate-${idx}-${dispatchRateDisabled(slot)}`" v-model="slot.commission_rate" :disabled="dispatchRateDisabled(slot)" :min="0" :max="100" :step="1" :format-options="{minimumFractionDigits:0,maximumFractionDigits:0}"><NumberFieldContent><NumberFieldDecrement /><NumberFieldInput :aria-label="dispatchPendingPercentage() ? '待接单陪玩抽成比例（%）' : '抽成比例（%）'" /><NumberFieldIncrement /></NumberFieldContent></NumberField><NumberField :step-snapping="false" v-else :key="`pending-rate-${dispatchPendingRateDisabled}`" v-model="dispatchPendingRate" :disabled="dispatchPendingRateDisabled" :min="0" :max="100" :step="1" :format-options="{minimumFractionDigits:0,maximumFractionDigits:0}"><NumberFieldContent><NumberFieldDecrement /><NumberFieldInput :aria-label="dispatchPendingPercentage() ? '待接单陪玩抽成比例（%）' : '抽成比例（%）'" /><NumberFieldIncrement /></NumberFieldContent></NumberField></div></div></div>
          <p v-if="slot.suggestion_error && !dispatchFixed && !dispatchQuote?.commission_locked" class="od-provider-tip">{{ slot.suggestion_error }} <Button size="xs" @click="onProviderPicked(idx)" variant="link">重新获取</Button></p>
          <p v-if="slot.provider_id" class="od-provider-tip">
            <template v-if="dispatchQuote && !slot.suggestion_loading && (dispatchFixed ? dispatchProviderShare(slot) : !commissionError('PERCENT', slot.commission_rate, 0))">预计实得 {{ amountToXaCoin(providerIncome(slot)) }} 币（{{ providerIncomeDesc(slot) }}）</template>
            <template v-else>{{ quoteLoading ? '正在试算个人分摊…' : '个人分摊与实得以服务端试算为准' }}</template>
            <template v-if="slot.source_label">
              <Badge style="margin-left: 8px" variant="outline">建议来源：{{ slot.source_label }}</Badge>
              <Button size="xs" v-if="!dispatchFixed && !dispatchQuote?.commission_locked && typeof slot.suggested_rate === 'number' && slot.commission_rate !== slot.suggested_rate" @click="restoreSuggestedRate(idx)" variant="link">恢复建议 {{ slot.suggested_rate }}%</Button>
            </template>
          </p>
        </div>
        <p v-if="providerSearchError" class="hint">{{ providerSearchError }} <Button size="xs" @click="searchProvider(providerSearchKeyword)" variant="link">重新搜索</Button></p>

        <div  class="od-dispatch-field ap-field--full"><span>{{ "备注" }}</span><Textarea v-model="dispatchForm.remark" :rows="2" placeholder="可选" aria-label="备注" /></div>
      </div></fieldset><div class="od-dispatch-footer-slot">
      <div class="od-dispatch-footer">
      <Alert v-if="serviceLoadError" class="od-dispatch-alert" variant="destructive"><AlertDescription><strong>{{ serviceLoadError }}</strong><Button size="xs" @click="loadDispatchServices" variant="link" :disabled="(dispatching || serviceLoading || dispatchResultUnknown)">重新加载服务</Button></AlertDescription></Alert>
      <Alert v-if="dispatchSubmitNotice" class="od-dispatch-alert"><AlertDescription><strong>{{ dispatchSubmitNotice }}</strong><Button size="xs" v-if="dispatchResultUnknown" @click="closeDispatchAndRefresh" variant="link" :disabled="(dispatching)">关闭并刷新订单</Button></AlertDescription></Alert>
      <div class="od-summary" :class="{ 'od-summary--danger': insufficient }" aria-live="polite">
        <div class="od-summary-item">
          <Popover ><PopoverTrigger as-child><button type="button" class="od-summary-label od-label-help">老板余额 <span  aria-hidden="true" class="od-inline-icon"><InfoFilled /></span></button></PopoverTrigger><PopoverContent>{{ "余额为选中老板时的查询快照，实际扣款前服务端会再次校验。" }}</PopoverContent></Popover>
          <span class="od-summary-value">{{ currentBoss ? `${amountToXaCoin(currentBoss.wallet_balance)}币` : '—' }}</span>
        </div>
        <div class="od-summary-item">
          <Popover ><PopoverTrigger as-child><button type="button" class="od-summary-label od-label-help">折后预计扣款 <span  aria-hidden="true" class="od-inline-icon"><InfoFilled /></span></button></PopoverTrigger><PopoverContent>{{ "金额来自服务端当前试算，已考虑适用的折扣。提交时服务端会再次核对；这不是平台利润。" }}</PopoverContent></Popover>
          <span class="od-summary-value">{{ quoteLoading ? '计算中…' : dispatchQuote ? `${amountToXaCoin(estimateAmount)}币` : '—' }}</span>
        </div>
        <div v-if="insufficient" class="od-summary-warn">余额不足以支付本次派单</div>
        <div v-else-if="!dispatchForm.customer_id || !dispatchForm.service_id" class="od-summary-note">选择老板和服务后开始试算</div>
        <div v-else-if="quoteLoading" class="od-summary-note">正在获取服务端试算</div>
        <div v-else-if="!dispatchQuote" class="od-summary-note">{{ quoteError || '试算未完成，请检查输入' }} <Button size="xs" v-if="!dispatchCommissionError" @click="refreshDispatchQuote" variant="link" :disabled="(dispatching || dispatchResultUnknown)">重新试算</Button></div>
      </div>
      <div class="od-dispatch-actions">
        <Button size="xs" v-if="!dispatchResultUnknown" @click="dispatchVisible = false" :disabled="(dispatching || dispatchConfirming)">取消</Button>
        <Button size="xs" @click="onDispatchSave" :disabled="(dispatchResultUnknown || dispatchConfirming) || (dispatching)">核对派单</Button>
      </div>
      </div>
    </div></DialogContent></Dialog>

  <Dialog :open="assignVisible" @update:open="value => { if (!value && !assigning) assignVisible = false }"><DialogContent class="od-config-dialog od-assign-dialog" :show-close-button="!assigning" @interact-outside="event => event.preventDefault()" @escape-key-down="event => { if (assigning) event.preventDefault() }"><DialogHeader><DialogTitle>指派待接单订单</DialogTitle><DialogDescription>核对订单并选择陪玩后确认派单。</DialogDescription></DialogHeader>
    <div class="ap-modal-form ap-form-grid">
      <div v-if="assignOrder" class="od-assign-order">
        <div><span>订单号</span><strong>{{ assignOrder.order_no || '待核对' }}</strong></div>
        <div><span>项目</span><strong>{{ assignOrder.service_name_snapshot || '待核对' }}</strong></div>
      </div>
      <div v-if="assignOrder" class="od-assign-context">
        <div><span>老板</span><strong>{{ assignOrder.customer_name || '待核对' }}</strong><small>{{ customerAccountLabel(assignOrder) }}</small></div>
        <div><span>人数</span><strong>{{ orderTeamSize(assignOrder) === 2 ? '双陪 · 2 人' : orderTeamSize(assignOrder) === 1 ? '单陪 · 1 人' : '待核对' }}</strong></div>
      </div>
      <div class="od-assign-search"><label for="assign-provider-search">{{ isStaffTeamAssign(assignOrder) ? '陪玩（选择两名）' : '陪玩' }}</label><Input id="assign-provider-search" :model-value="assignSearchKeyword" :disabled="assigning" placeholder="输入昵称或账号搜索陪玩" @update:model-value="queueAssignSearch" /><p class="od-assign-hint">搜索状态仅供参考，提交时重新核验资格、档期和名额。</p>
        <p v-if="assignProviderLoading" role="status">搜索中…</p><p v-else-if="assignSearchKeyword && !assignProviderOptions.length && !assignSearchError" role="status">未找到陪玩，请更换关键词。</p>
        <div v-if="assignProviderOptions.length" class="od-assign-results" role="group" aria-label="搜索结果"><button v-for="p in assignProviderOptions" :key="p.user" type="button" class="od-assign-candidate" :aria-pressed="isAssignProviderSelected(p.user)" :disabled="assigning || (!isAssignProviderSelected(p.user) && isStaffTeamAssign(assignOrder) && assignProviderIds.length >= 2)" @click="toggleAssignProvider(p.user)"><span class="od-candidate-main"><strong>{{ p.display_name || p.username || '陪玩' }}</strong><span>{{ p.escort_no || '编号未记录' }} · {{ assignCandidateStatus(p) }}</span></span><span class="od-candidate-account">账号 {{ p.username || '未记录' }} · {{ isAssignProviderSelected(p.user) ? '已选' : '选择' }}</span></button></div>
      </div>
      <section v-if="assignSelectedProviders.length" class="od-assign-selected" aria-label="已选择的陪玩">
        <strong>已选 {{ assignSelectedProviders.length }} 人</strong>
        <div v-for="person in assignSelectedProviders" :key="person.id" class="od-assign-selected-row">
          <span>{{ person.label }}</span>
        </div>
      </section>
      <Alert v-if="assignSearchError" variant="destructive"><AlertTitle>搜索失败</AlertTitle><AlertDescription>{{ assignSearchError }} <Button variant="link" :disabled="assigning || assignProviderLoading" @click="searchAssignProvider(assignSearchKeyword)">重新搜索</Button></AlertDescription></Alert>
      <Alert v-if="assignSubmitError" variant="destructive"><AlertTitle>指派待核对</AlertTitle><AlertDescription>{{ assignSubmitError }} <Button variant="link" :disabled="assigning" @click="closeAssignAndRefresh">关闭并刷新订单</Button></AlertDescription></Alert>
      <div v-if="isStaffPending(assignOrder)" class="od-assign-deadline"><strong>待派截止时间</strong><span>{{ assignOrder?.auto_cancel_at ? `${formatDateTime(assignOrder.auto_cancel_at)}（到期后不能派单）` : '截止时间未记录，请核对订单详情' }}</span></div>
    </div>
    <DialogFooter><Button variant="outline" :disabled="assigning" @click="assignVisible = false">取消</Button><Button :disabled="assigning || !!assignSubmitError" @click="onAssignSave">{{ assigning ? '派单中…' : '确认派单' }}</Button></DialogFooter>
  </DialogContent></Dialog>

  <Dialog :open="transferVisible" @update:open="value => { if (!value && !transferLocked) transferVisible = false }"><DialogContent class="od-config-dialog od-transfer-dialog" :show-close-button="!transferLocked" @interact-outside="event => { if (transferLocked) event.preventDefault() }" @escape-key-down="event => { if (transferLocked) event.preventDefault() }"><DialogHeader><DialogTitle>订单转单</DialogTitle><DialogDescription>核对转出人员、收益分配和转入人员。</DialogDescription></DialogHeader>
    <template v-if="transferOrder">
      <p class="od-transfer-order">{{ transferOrder.order_no }} · {{ transferOrder.service_name_snapshot || '服务待核对' }}</p>
      <Alert><AlertTitle>旧陪玩保留填写的份额，其余转给新陪玩。</AlertTitle><AlertDescription>收益入账时间以订单结算模式为准；罚款余额不足时整笔转单失败。</AlertDescription></Alert>
      <div v-for="row in transferRows" :key="row.old_provider_id" class="od-provider-row">
        <label class="od-transfer-choice"><Checkbox :model-value="row.enabled" :disabled="transferLocked" @update:model-value="value => row.enabled = value === true" /><span>转出：{{ row.old_provider_label }}（{{ row.pool === null ? '份额待核对' : `可分配 ${amountToXaCoin(row.pool)} 币` }}）</span></label>
        <div v-if="row.enabled" class="od-transfer-fields">
          <div class="od-transfer-field od-transfer-field--provider">
            <span>转入陪玩</span>
              <div class="od-transfer-search"><Input :model-value="row.search_keyword || ''" :disabled="transferLocked" placeholder="输入昵称或账号搜索" :aria-label="`搜索转入陪玩：${row.old_provider_label}`" @update:model-value="value => queueTransferSearch(row, value)" /><p v-if="row.new_provider_id" class="hint">已选：{{ transferProviderOptions.find(p => p.user === row.new_provider_id) ? assignCandidateLabel(transferProviderOptions.find(p => p.user === row.new_provider_id)) : `账号 #${row.new_provider_id}` }}</p><p v-if="transferSearchRow === row.old_provider_id && transferProviderLoading" role="status">搜索中…</p><div v-if="transferSearchRow === row.old_provider_id && transferProviderOptions.length" class="od-assign-results" role="group" aria-label="转入陪玩搜索结果"><button v-for="p in transferProviderOptions" :key="p.user" type="button" class="od-assign-candidate" :aria-pressed="row.new_provider_id === p.user" :disabled="transferLocked" @click="row.new_provider_id = p.user; transferSearchRow = null"><span class="od-candidate-main"><strong>{{ p.display_name || p.username || '陪玩' }}</strong><span>{{ p.escort_no || '编号未记录' }} · {{ assignCandidateStatus(p) }}</span></span><span class="od-candidate-account">账号 {{ p.username || '未记录' }}</span></button></div></div>
          </div>
          <label class="od-transfer-field">
              <span>份额方式</span>
              <NativeSelect v-model="row.split_type" :disabled="transferLocked" aria-label="份额方式" class="w-full"><NativeSelectOption value="PERCENT">按比例</NativeSelectOption><NativeSelectOption value="FIXED">固定额</NativeSelectOption></NativeSelect>
          </label>
          <label class="od-transfer-field">
              <span>{{ row.split_type === 'FIXED' ? '旧陪玩保留（币）' : '旧陪玩保留（%）' }}</span>
              <Input v-if="row.split_type === 'PERCENT'" :model-value="row.split_percent" type="number" min="0" max="100" step="1" :disabled="transferLocked" aria-label="旧陪玩保留百分比" @update:model-value="value => row.split_percent = value === '' ? null : Number(value)" />
              <Input
                v-else
                v-model="row.split_fixed_yuan"
                :disabled="transferLocked"
                inputmode="decimal"
                maxlength="12"
                aria-label="旧陪玩保留金额"
                style="width: 100%"
              />
              <span v-if="row.split_type === 'PERCENT' && validTransferPercent(row.split_percent)" class="hint">旧陪玩 {{ row.split_percent }}% · 新陪玩 {{ 100 - row.split_percent }}%</span>
              <span v-if="row.split_type === 'FIXED' && row.pool !== null && transferCoinRaw(row.split_fixed_yuan) !== null && transferCoinRaw(row.split_fixed_yuan)! > row.pool" class="hint">超过当前可分配份额，提交时按 {{ amountToXaCoin(row.pool) }} 币上限处理。</span>
              <span v-if="transferAttempted && (row.split_type === 'FIXED' ? transferCoinRaw(row.split_fixed_yuan) === null : !validTransferPercent(row.split_percent))" role="alert" class="od-transfer-error">{{ row.split_type === 'FIXED' ? '请输入非负金额，最多一位小数' : '请输入 0–100 的整数' }}</span>
          </label>
          <label class="od-transfer-field">
              <span>罚款（币）</span>
              <Input
                v-model="row.penalty_yuan"
                :disabled="transferLocked"
                inputmode="decimal"
                maxlength="12"
                aria-label="罚款（币）"
                style="width: 100%"
              />
              <span v-if="transferAttempted && transferCoinRaw(row.penalty_yuan) === null" role="alert" class="od-transfer-error">请输入非负金额，最多一位小数</span>
          </label>
        </div>
      </div>
      <label class="od-transfer-reason">转单原因<Textarea v-model="transferReason" :disabled="transferLocked" :rows="2" maxlength="255" placeholder="可选，将记入订单流水" /></label>
      <p class="hint">搜索结果不代表已保留名额，提交时会重新核验转入陪玩的资格和接单状态。</p>
      <Alert v-if="transferSearchError" variant="destructive"><AlertTitle>搜索失败</AlertTitle><AlertDescription>{{ transferSearchError }} <Button variant="link" :disabled="transferLocked || transferProviderLoading" @click="searchTransferProvider(transferSearchKeyword)">重新搜索</Button></AlertDescription></Alert>
      <Alert v-if="transferSubmitError" variant="destructive"><AlertTitle>转单待核对</AlertTitle><AlertDescription>{{ transferSubmitError }} <Button variant="link" :disabled="transferLocked" @click="closeTransferAndRefresh">关闭并刷新订单</Button></AlertDescription></Alert>
    </template>
    <DialogFooter><Button variant="outline" :disabled="transferLocked" @click="transferVisible = false">取消</Button><Button :disabled="transferLocked || !!transferSubmitError" @click="onTransferSave">{{ transferring ? '转单中…' : '核对转单' }}</Button></DialogFooter>
  </DialogContent></Dialog>
  <Dialog :open="transferConfirming" @update:open="value => { if (!value) finishTransferPrompt(false) }"><DialogContent class="od-config-dialog od-transfer-confirm-dialog" @interact-outside="event => event.preventDefault()"><DialogHeader><DialogTitle>核对转单</DialogTitle><DialogDescription>订单 {{ transferPrompt?.orderNo }}</DialogDescription></DialogHeader><div class="od-transfer-confirm"><div v-for="(item, index) in transferPrompt?.summary" :key="index" class="od-transfer-confirm-item"><strong>转出：{{ item.old }}</strong><p>转入：{{ item.incoming }}</p><p>旧陪玩保留方式：{{ item.splitLabel }}</p><p>预计旧陪玩 {{ amountToXaCoin(item.oldShare) }} 币 · 新陪玩 {{ amountToXaCoin(item.newShare) }} 币{{ item.capped ? '（已按份额上限计算）' : '' }}</p><p>旧陪玩罚款 {{ amountToXaCoin(item.penalty) }} 币</p></div><p>原因：{{ transferPrompt?.reason || '未填写' }}</p><p class="od-transfer-confirm-note">金额按读取时份额估算；提交时按最新份额处理并核验资格，实际以订单记录为准。</p></div><DialogFooter><Button variant="outline" @click="finishTransferPrompt(false)">返回修改</Button><Button @click="finishTransferPrompt(true)">确认转单</Button></DialogFooter></DialogContent></Dialog>
<ActionDialog :open="actionPromptOpen" :title="actionPromptTitle" :mode="actionPromptMode" :message="actionPromptMessage" :options="actionPromptOptions" :input="actionPromptInput" :error="actionPromptError" @update:input="actionPromptInput=$event" @accept="acceptActionPrompt" @cancel="cancelActionPrompt" />
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, h, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import ActionDialog from '@/components/ActionDialog.vue'
import SearchSelect from '@/components/SearchSelect.vue'
import { useActionDialog } from '@/composables/useActionDialog'
import { showNotice } from '@/utils/notice'
import { NumberField, NumberFieldContent, NumberFieldDecrement, NumberFieldIncrement, NumberFieldInput } from '@xa/shadcn-vue/number-field'
import { RadioGroup, RadioGroupItem } from '@xa/shadcn-vue/radio-group'
import { Info as InfoFilled, Plus, Search, Settings as Setting } from '@xa/shadcn-vue/icons'
import CrudTable from '@/components/CrudTable.vue'
import SuggestionInput from '@/components/SuggestionInput.vue'
import DetailList from '@/components/DetailList.vue'
import DetailItem from '@/components/DetailItem.vue'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { Button } from '@xa/shadcn-vue/button'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Input } from '@xa/shadcn-vue/input'
import { Badge } from '@xa/shadcn-vue/badge'
import { Checkbox } from '@xa/shadcn-vue/checkbox'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { Popover, PopoverContent, PopoverTrigger } from '@xa/shadcn-vue/popover'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
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
const {open:actionPromptOpen,title:actionPromptTitle,mode:actionPromptMode,message:actionPromptMessage,options:actionPromptOptions,input:actionPromptInput,error:actionPromptError,accept:acceptActionPrompt,cancel:cancelActionPrompt,...actionPrompt}=useActionDialog()
function successNotice(message:string) { showNotice(message,'success') }
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
    showNotice(order?.dispatch_message_unavailable_reason || '当前订单不能发单，请核对订单状态')
    return
  }
  dispatchMessageOrderId.value = order.id
  dispatchMessageVisible.value = true
}
const tableRef = ref()
const orderKind = computed(() => route.query.order_kind === 'GIFT' || route.query.order_kind === 'SERVICE' ? route.query.order_kind : '')
async function clearOrderKind() {
  const remaining = { ...route.query }
  delete remaining.order_kind
  await router.replace({ query: remaining })
  tableRef.value?.reload()
}
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
  aftersale_open: '', overdue_pending: '', legacy_team: '',
})
async function clearDashboardScope() {
  const remaining = { ...route.query }
  delete remaining.exclude_direct_test
  await router.replace({ query: remaining })
  tableRef.value?.reload()
}
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
  return (typeof queue === 'string' && ['staff', 'staff_review', 'paid_pending', 'paid_pending_staff', 'paid_pending_public', 'unpaid_pending', 'open_aftersale', 'overdue_pending', 'legacy_team'].includes(queue))
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
const hasOrderFilters = computed(() => Boolean(orderKind.value) || Object.values(query).some(value => Boolean(value)))
const exactFilterUnsupported = ref(false)
const customerFilterUnsupported = ref(false)
const createdDayUnsupported = ref(false)
const createdDayTimezone = ref('')
const staffQueueDeadlineOrder = ref(false)
const aftersaleQueueOrder = ref(false)
const overdueQueueDeadlineOrder = ref(false)
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
  if (query.overdue_pending === params.overdue_pending) overdueQueueDeadlineOrder.value = false
  if (query.aftersale_open === params.aftersale_open && query.actionable_staff === params.actionable_staff) aftersaleQueueOrder.value = false
  const dashboardScope = route.query.exclude_direct_test === '1'
  const kind = orderKind.value
  const response = await orderApi.list({ ...params, ...(dashboardScope ? { exclude_direct_test: '1' } : {}), ...(kind ? { order_kind: kind } : {}) }, true)
  if (kind && (response.data as any)?.order_kind_applied !== kind) throw new Error('服务端未确认订单类型筛选，请清除类型后重试')
  if (dashboardScope && (response.data as any)?.exclude_direct_test_applied !== true) {
    throw new Error('服务端未确认工作台订单范围，请更新服务端或查看全部来源')
  }
  if (params.legacy_team === '1' && (response.data as any)?.legacy_team_applied !== true) {
    throw new Error('服务端未确认旧版双陪筛选，请清除筛选后重试')
  }
  if (params.overdue_pending === '1' && (response.data as any)?.overdue_pending_applied !== true) {
    throw new Error('服务端未确认接单期限筛选，请清除筛选后重试')
  }
  if (sequence === orderFetchSequence && query.overdue_pending === params.overdue_pending && query.aftersale_open === params.aftersale_open && query.actionable_staff === params.actionable_staff) {
    overdueQueueDeadlineOrder.value = params.overdue_pending === '1' && (response.data as any)?.overdue_pending_order === 'deadline_asc'
  }
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
function isPaidPendingOrder(order: any) {
  return order?.status === 'PENDING' && order.payment_status === 'PAID'
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

async function showStaffPending(reload: () => void) {
  if (orderKind.value) {
    const remaining = { ...route.query }
    delete remaining.order_kind; delete remaining.created_day; delete remaining.find_customer
    await router.replace({ query: remaining })
  }
  query.legacy_team = ''
  Object.assign(query, {
    order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: '',
    status: 'PENDING', payment_status: 'PAID', dispatch_route: 'STAFF', actionable_staff: '1', aftersale_open: '', overdue_pending: '',
  })
  reload()
}

function clearActionableFilter(reload: () => void) {
  query.actionable_staff = ''
  query.overdue_pending = ''
  reload()
}

async function clearStaffPending(reload: () => void) {
  if (orderKind.value) {
    const remaining = { ...route.query }
    delete remaining.order_kind; delete remaining.created_day; delete remaining.find_customer
    await router.replace({ query: remaining })
  }
  query.legacy_team = ''
  Object.assign(query, { order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: '', status: '', payment_status: '', dispatch_route: '', actionable_staff: '', aftersale_open: '', overdue_pending: '' })
  reload()
}

function showStaffReview(reload: () => void) {
  query.legacy_team = ''
  Object.assign(query, {
    order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: '',
    status: 'PENDING', payment_status: 'PAID', dispatch_route: 'STAFF', actionable_staff: '', aftersale_open: '', overdue_pending: '',
  })
  reload()
}

watch(() => route.query.queue, queue => {
  if (queue !== 'staff' && queue !== 'staff_review' && queue !== 'paid_pending' && queue !== 'paid_pending_staff' && queue !== 'paid_pending_public' && queue !== 'unpaid_pending' && queue !== 'open_aftersale' && queue !== 'overdue_pending' && queue !== 'legacy_team') return
  query.legacy_team = ''
  if (queue === 'staff_review') showStaffReview(() => tableRef.value?.reload())
  else if (queue === 'paid_pending' || queue === 'paid_pending_staff' || queue === 'paid_pending_public') {
    const route = queue === 'paid_pending_staff' ? 'STAFF' : queue === 'paid_pending_public' ? 'PUBLIC' : ''
    Object.assign(query, { order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: '', status: 'PENDING', payment_status: 'PAID', dispatch_route: route, actionable_staff: '', aftersale_open: '', overdue_pending: '' })
    tableRef.value?.reload()
  }
  else if (queue === 'legacy_team') {
    Object.assign(query, { order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: '', status: '', payment_status: '', dispatch_route: '', actionable_staff: '', aftersale_open: '', overdue_pending: '', legacy_team: '1' })
    tableRef.value?.reload()
  }
  else if (queue === 'overdue_pending') {
    Object.assign(query, { order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: '', status: 'PENDING', payment_status: 'PAID', dispatch_route: '', actionable_staff: '', aftersale_open: '', overdue_pending: '1' })
    tableRef.value?.reload()
  }
  else if (queue === 'unpaid_pending') {
    Object.assign(query, { order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: '', status: 'PENDING', payment_status: 'UNPAID', dispatch_route: '', actionable_staff: '', aftersale_open: '', overdue_pending: '' })
    tableRef.value?.reload()
  }
  else if (queue === 'open_aftersale') {
    Object.assign(query, { order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: '', status: '', payment_status: '', dispatch_route: '', actionable_staff: '', aftersale_open: '1', overdue_pending: '' })
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
  query.legacy_team = ''
  Object.assign(query, { order_no: orderNo.trim(), order_no_exact: orderNo.trim(), customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: '', status: '', payment_status: '', dispatch_route: '', actionable_staff: '', aftersale_open: '', overdue_pending: '' })
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
  query.legacy_team = ''
  Object.assign(query, { order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: customerId, created_day: '', status: '', payment_status: '', dispatch_route: '', actionable_staff: '', aftersale_open: '', overdue_pending: '' })
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
  query.legacy_team = ''
  Object.assign(query, { order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: '', status, payment_status: '', dispatch_route: '', actionable_staff: '', aftersale_open: '', overdue_pending: '' })
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
  query.legacy_team = ''
  Object.assign(query, { order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: '', created_day: day, status: '', payment_status: '', dispatch_route: '', actionable_staff: '', aftersale_open: '', overdue_pending: '' })
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
  return async (keyword: string): Promise<string[]> => {
    if (!keyword) return []
    const revision = auth.sessionRevision
    try {
      const res = (await orderApi.list({ [field]: keyword, page: 1, page_size: 10 })) as any
      if (revision !== auth.sessionRevision) return []
      return [...new Set<string>((res.data?.list || []).map((o: any) => o[field]).filter((value: unknown): value is string => typeof value === 'string' && !!value))]
    } catch { return [] }
  }
}

const afterSalePromptOpen = ref(false)
const afterSalePromptOrder = ref('')
const afterSalePromptTicket = ref<number | string>('')
const afterSaleResolution = ref('')
const afterSalePromptError = ref('')
let afterSalePromptResolve: ((value: string | null) => void) | null = null
function settleAfterSalePrompt(value: string | null) { const resolve = afterSalePromptResolve; afterSalePromptResolve = null; afterSalePromptOpen.value = false; resolve?.(value) }
function confirmAfterSalePrompt() { const value = afterSaleResolution.value.trim(); if (value.length < 2 || value.length > 1000) { afterSalePromptError.value = '处理说明须为 2–1000 字'; return } settleAfterSalePrompt(value) }
function askAfterSaleResolution(order: string, ticket: number | string): Promise<string | null> {
  if (afterSalePromptResolve) return Promise.resolve(null)
  afterSalePromptOrder.value = order; afterSalePromptTicket.value = ticket; afterSaleResolution.value = ''; afterSalePromptError.value = ''; afterSalePromptOpen.value = true
  return new Promise(resolve => { afterSalePromptResolve = resolve })
}
const detailVisible = ref(false)
const current = ref<any>(null)
const logs = ref<any[]>([])
const aftersales = ref<any[]>([])
function aftersaleStatusText(status: string) {
  return ({ OPEN: '处理中', RESOLVED: '处理完成', REFUNDED: '已退款', PARTIALLY_REFUNDED: '部分退款' } as Record<string, string>)[status] || status || '状态待核对'
}
function pastAftersales(ticket: any) {
  if (!Array.isArray(ticket.history)) return []
  return ticket.history.filter((item: any) => item && item.status !== 'OPEN' && (
    ticket.client_request_id ? item.client_request_id !== ticket.client_request_id :
      !ticket.created_at || Date.parse(item.created_at) !== Date.parse(ticket.created_at)
  )).slice().reverse()
}
const detailLoading = ref(false)
const detailFailed = ref(false)
const aftersaleFailed = ref(false)
const logsFailed = ref(false)
const detailOrderId = ref<number | string | null>(null)
const orderActionBusy = ref(false)
type RefundRecovery = { wallet_id: number; account_id: number | null; role: string; income_amount: number; held_amount: number; deposit_amount: number; available_required: number; available_balance: number; protected_deposit_amount?: number; shortfall: number; original_transaction_ids: number[]; source_safe?: boolean; explanation?: string }
type RefundPreview = { order_id: number; is_settled: boolean; eligible: boolean; original_amount: number; refunded_amount: number; remaining_amount: number; refund_amount: number; remaining_after_refund: number; full_refund: boolean; recovery_total: number; recoveries: RefundRecovery[]; shortfalls: { wallet_id: number; account_id: number | null; required: number; available: number; shortfall: number }[]; plan_token: string; evidence_error?: string }
type NegotiatedRow = { key: string; name: string; account_id: number | null; entitlement: number; settled: boolean; input: string }
const negotiatedRows = ref<NegotiatedRow[]>([])
const negotiatedReviewed = ref(false)
const negotiatedAllocation = computed(() => {
  if (!negotiatedRows.value.length) return null
  const entries = negotiatedRows.value.map(row => ({ key: row.key, amount: /^\d+(?:\.\d)?$/.test(row.input.trim()) ? xaCoinToAmount(row.input) : null }))
  return entries.every(row => row.amount !== null && Number.isSafeInteger(row.amount)) ? entries : null
})
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
  && (!negotiatedRows.value.length || negotiatedReviewed.value)
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
let cancelOrderPrompt: (() => void) | null = null
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
    await actionPrompt.confirm('请确认已核对当前陪玩、状态流水及请求处理情况。请求仍可能在处理中。', '解除指派限制', {
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

watch(detailVisible, visible => { if (!visible) { if (afterSalePromptOpen.value) invalidateOrderAction(); clearOrderDetail() } }, { flush: 'sync' })

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
  settleAfterSalePrompt(null)
  orderActionBusy.value = false
  if (pendingActionOrderId !== null) { cancelOrderPrompt?.(); actionPrompt.close() }
  cancelOrderPrompt = null
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
    wait: async <T>(prompt: Promise<T>): Promise<T | null> => {
      let cancel: () => void = () => {}
      const interrupted = new Promise<null>(resolve => { cancel = () => resolve(null); cancelOrderPrompt = cancel })
      try { return await Promise.race([prompt, interrupted]) }
      finally { if (cancelOrderPrompt === cancel) cancelOrderPrompt = null }
    },
    finish: () => { if (sequence === actionSequence) { orderActionBusy.value = false; pendingActionOrderId = null; cancelOrderPrompt = null } },
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
      showNotice('订单已变化，请核对最新状态后再操作')
    }
    if (assignVisible.value && assignOrder.value?.id === data.id && !assigning.value && !assignSubmitError.value) {
      assignSubmitError.value = '订单已变化，请关闭并刷新后核对'
    }
    if (transferVisible.value && transferOrder.value?.id === data.id && !transferring.value && !transferSubmitError.value) {
      invalidateTransferPrompt()
      transferSubmitError.value = '订单已变化，请关闭并刷新后核对'
    }
    requestOrderRefresh(data.id)
  })
  offConnected = adminWs.on('connected', () => {
    // A reconnect cannot tell us whether an in-flight refund committed.
    // Keep its locked review until HTTP resolves or the operator checks it.
    if (!refundSubmitting.value && !refundResultUnknown.value) invalidateOrderAction()
    if (assignVisible.value && !assigning.value && !assignSubmitError.value) assignSubmitError.value = '连接已恢复，请关闭并刷新订单后核对'
    if (transferVisible.value && !transferring.value && !transferSubmitError.value) { invalidateTransferPrompt(); transferSubmitError.value = '连接已恢复，请关闭并刷新订单后核对' }
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

async function enableTeamEvidence() {
  const orderId = detailOrderId.value
  if (!orderId || !detailVisible.value || detailLoading.value || detailFailed.value || current.value?.escort_mode !== 'DOUBLE'
      || current.value?.requires_boss_selection !== false || current.value?.status !== 'GRABBED') return
  const operation = beginOrderAction(orderId, 'order:dispatch')
  if (!operation) return
  try {
    try {
      await operation.wait(actionPrompt.confirm('请先与两位陪玩和老板核实服务尚未开始。启用后，双方须分别提交入队和结单截图。', '启用双方凭证流程', {
        type: 'warning', confirmButtonText: '已核实，启用', cancelButtonText: '返回',
      }))
    } catch { return }
    if (!operation.current() || detailOrderId.value !== orderId || !detailVisible.value) return
    pendingActionOrderId = null
    try {
      await request({ url: `/orders/${orderId}/enable-team-evidence/`, method: 'post', data: { confirmed: true }, silent: true })
      if (operation.current(true)) successNotice('已启用双方凭证流程')
    } catch (error: any) {
      if (operation.current(true)) showNotice(orderActionFailure(error, '启用双人凭证'))
    }
    if (operation.current(true)) requestOrderRefresh(orderId)
  } finally { operation.finish() }
}

async function processAfterSale(ticket: any, action: 'resolve' | 'refund') {
  const orderId = detailOrderId.value
  if (!orderId || !detailVisible.value || detailLoading.value || detailFailed.value || aftersaleFailed.value || ticket.status !== 'OPEN') return
  if (action === 'refund') { await openRefundReview(orderId, ticket.id); return }
  const operation = beginOrderAction(orderId, 'order:settle')
  if (!operation) return
  try {
    const resolution = await operation.wait(askAfterSaleResolution(current.value?.order_no || `#${orderId}`, ticket.id))
    if (!resolution) return
    if (!operation.current() || detailOrderId.value !== orderId || !detailVisible.value || detailLoading.value || detailFailed.value || aftersaleFailed.value) return
    pendingActionOrderId = null
    try {
      await request({ url: `/aftersales/${ticket.id}/resolve/`, method: 'post', data: { resolution }, silent: true })
      if (!operation.current(true)) return
      successNotice('售后已处理')
    } catch (error: any) {
      if (!operation.current(true)) return
      showNotice(orderActionFailure(error, '售后处理'))
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
      const result = await operation.wait(actionPrompt.prompt(`订单 ${row.order_no || `#${row.id}`}：${consequence}`, '取消订单', {
        inputType: 'textarea', inputPlaceholder: '取消原因（可选，最多 255 字）',
        inputValidator: (value: string) => (value || '').trim().length <= 255,
        inputErrorMessage: '取消原因最多 255 字', confirmButtonText: '确认取消订单', cancelButtonText: '返回', type: 'warning',
      }))
      if (!result) return
      reason = (result.value || '').trim()
    } catch { return }
    if (!operation.current()) return
    pendingActionOrderId = null
    try {
      await request({ url: `/orders/${row.id}/cancel/`, method: 'post', data: { reason }, silent: true })
      if (!operation.current(true)) return
      successNotice('订单已取消，请查看支付状态')
    } catch (error: any) {
      if (!operation.current(true)) return
      showNotice(orderActionFailure(error, '取消订单'))
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
  negotiatedRows.value = []
  negotiatedReviewed.value = false
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
watch(negotiatedRows, () => {
  if (!refundVisible.value || !negotiatedRows.value.length) return
  negotiatedReviewed.value = false
  ++refundPreviewSequence
  refundPreview.value = null
  refundLoading.value = false
  refundError.value = '分摊已修改，请重新试算后确认'
}, { deep: true, flush: 'sync' })

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
  const key = JSON.stringify([amount, reason, negotiatedAllocation.value])
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
  if (negotiatedRows.value.length && (!negotiatedAllocation.value || negotiatedAllocation.value.reduce((sum, row) => sum + (row.amount || 0), 0) !== raw)) {
    refundPreview.value = null
    refundError.value = '请逐项填写承担金额，合计须等于本次退款金额'
    return
  }
  const sequence = ++refundPreviewSequence
  refundLoading.value = true
  refundPreview.value = null
  refundError.value = ''
  try {
    const result = negotiatedRows.value.length
      ? await request<RefundPreview>({ url: `/orders/${target.orderId}/negotiated-refund-preview/`, method: 'post', data: { refund_amount: raw, allocations: negotiatedAllocation.value }, silent: true })
      : await request<RefundPreview>({ url: `/orders/${target.orderId}/refund-preview/`, method: 'get', params: initializeAmount ? undefined : { refund_amount: raw }, silent: true })
    if (!operation.current() || !refundVisible.value || sequence !== refundPreviewSequence) return
    if (String(result.data.order_id) !== String(target.orderId)) throw new Error('订单试算不匹配')
    if (!initializeAmount && result.data.refund_amount !== raw) throw new Error('退款金额试算不匹配')
    applyRefundPreview(result.data, initializeAmount)
  } catch (error: any) {
    if (!operation.current() || !refundVisible.value || sequence !== refundPreviewSequence) return
    const body = error?.response?.data || error
    if (initializeAmount && !target.ticketId && body?.errorCode === 'REFUND_LEDGER_INCOMPLETE') {
      try {
        const result = await request<NegotiatedRow[]>({ url: `/orders/${target.orderId}/negotiated-refund-options/`, method: 'get', silent: true })
        if (!operation.current() || !refundVisible.value || sequence !== refundPreviewSequence) return
        const total = result.data.reduce((sum, row) => sum + row.entitlement, 0)
        refundLimits.value = { original: total, refunded: 0, remaining: total }
        negotiatedRows.value = result.data.map(row => ({ ...row, input: '' }))
        refundError.value = '请填写协商退款金额与各方承担金额后试算'
      } catch { refundError.value = typeof body?.msg === 'string' ? body.msg : '该订单须人工核账' }
    } else if (body?.errorCode === 'REFUND_INVALID_AMOUNT') {
      try { await refreshRefundLimits(target.orderId, operation, sequence) } catch { /* Keep the prior limits until a successful read. */ }
      if (operation.current() && refundVisible.value && sequence === refundPreviewSequence) refundError.value = '可退余额已变化，请核对剩余可退金额后重新试算'
    } else {
      const status = error?.response?.status ?? body?.code
      const message = typeof body?.msg === 'string' ? body.msg : typeof body?.detail === 'string' ? body.detail : ''
      refundError.value = status >= 400 && status < 500 && message ? message : '退款试算加载失败，请重试'
    }
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
    if (negotiatedRows.value.length) await request({ url: `/orders/${target.orderId}/negotiated-refund/`, method: 'post', data: { reason, ...intent, allocations: negotiatedAllocation.value, reviewed: negotiatedReviewed.value }, silent: true })
    else if (target.ticketId) await request({ url: `/aftersales/${target.ticketId}/refund/`, method: 'post', data: { resolution: reason, ...intent }, silent: true })
    else await request({ url: `/orders/${target.orderId}/refund/`, method: 'post', data: { reason, ...intent }, silent: true })
    // Our own committed order notification may arrive before this HTTP result.
    if (!operation.current(true)) return
    successNotice('已退款')
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
      showNotice('订单已变化，请重新打开退款复核')
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
  if (assignReview.value) { showNotice('请先核对上一笔指派结果'); return }
  if (!canAssignOrder(row)) {
    showNotice('当前订单无法派单，请刷新并核对支付与订单状态')
    return
  }
  if (assignSearchTimer) clearTimeout(assignSearchTimer)
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

let assignSearchTimer: ReturnType<typeof setTimeout> | undefined
function isAssignProviderSelected(id: number) {
  return isStaffTeamAssign(assignOrder.value) ? assignProviderIds.value.includes(id) : assignProviderId.value === id
}
function toggleAssignProvider(id: number) {
  if (assigning.value) return
  if (!isStaffTeamAssign(assignOrder.value)) assignProviderId.value = assignProviderId.value === id ? undefined : id
  else if (assignProviderIds.value.includes(id)) assignProviderIds.value = assignProviderIds.value.filter(item => item !== id)
  else if (assignProviderIds.value.length < 2) assignProviderIds.value = [...assignProviderIds.value, id]
}
function queueAssignSearch(value: string | number) {
  const keyword = String(value)
  assignSearchKeyword.value = keyword
  ++assignSearchSequence
  if (assignSearchTimer) clearTimeout(assignSearchTimer)
  assignProviderLoading.value = false
  assignSearchError.value = ''
  assignProviderOptions.value = assignProviderOptions.value.filter(p => isAssignProviderSelected(p.user))
  assignSearchTimer = setTimeout(() => { if (assignVisible.value) void searchAssignProvider(keyword) }, 250)
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
    if (selected.length !== 2 || new Set(selected).size !== 2) { showNotice('请选择两名不同的陪玩'); return }
    if (!auth.hasPerm('order:dispatch') || !assignVisible.value) return
    const revision = auth.sessionRevision, orderId = assignOrder.value.id
    beginAssignReview(orderId, assignOrder.value.order_no || `#${orderId}`)
    assigning.value = true
    try {
      await orderApi.action(orderId, 'assign', { provider_ids: selected }, true)
      if (pageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('order:dispatch') || !assignVisible.value || assignOrder.value?.id !== orderId) return
      clearAssignReview()
      successNotice('双陪派单成功')
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
    showNotice('该订单不能使用单人指派，请刷新并核对订单人数')
    return
  }
  if (!assignOrder.value || !assignProviderId.value) {
    showNotice('请选择陪玩')
    return
  }
  const revision = auth.sessionRevision, orderId = assignOrder.value.id, providerId = assignProviderId.value
  beginAssignReview(orderId, assignOrder.value.order_no || `#${orderId}`)
  assigning.value = true
  try {
    await orderApi.action(orderId, 'assign', { provider_id: providerId }, true)
    if (pageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('order:dispatch') || !assignVisible.value || assignOrder.value?.id !== orderId) return
    clearAssignReview()
    successNotice('派单成功')
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
const transferSearchRow = ref<number | null>(null)
let transferSearchTimer: ReturnType<typeof setTimeout> | undefined
let transferConfirmationSequence = 0
const transferPrompt = ref<{ orderNo: string; reason: string; summary: Array<{ old: string; incoming: string; splitLabel: string; oldShare: number; newShare: number; penalty: number; capped: boolean }> } | null>(null)
let cancelTransferPrompt: ((accepted: boolean) => void) | null = null
function finishTransferPrompt(accepted: boolean) {
  const resolve = cancelTransferPrompt
  cancelTransferPrompt = null
  transferPrompt.value = null
  transferConfirming.value = false
  resolve?.(accepted)
}
function invalidateTransferPrompt() {
  if (!transferConfirming.value) return
  ++transferConfirmationSequence
  finishTransferPrompt(false)
}

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
    await actionPrompt.confirm('请确认已核对当前陪玩、转单流水及请求处理情况。请求仍可能在处理中。', '解除转单限制', {
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

function queueTransferSearch(row: any, value: string | number) {
  const keyword = String(value)
  row.search_keyword = keyword
  transferSearchRow.value = row.old_provider_id
  transferSearchKeyword.value = keyword
  ++transferSearchSequence
  if (transferSearchTimer) clearTimeout(transferSearchTimer)
  transferProviderLoading.value = false
  transferSearchError.value = ''
  transferProviderOptions.value = transferProviderOptions.value.filter(p => transferRows.value.some(item => item.new_provider_id === p.user))
  transferSearchTimer = setTimeout(() => { if (transferVisible.value) void searchTransferProvider(keyword) }, 250)
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
  if (transferSearchTimer) clearTimeout(transferSearchTimer)
  transferSearchRow.value = null
  if (transferReview.value) { showNotice('请先核对上一笔转单结果'); return }
  if (row.status !== 'IN_SERVICE') { showNotice('订单状态已变化，请刷新后核对'); return }
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
  if (!picked.length) { showNotice('请至少选择一名要转出的打手'); return }
  for (const r of picked) {
    if (!r.new_provider_id) { showNotice(`请为「${r.old_provider_name}」选择转入新打手`); return }
    if (r.new_provider_id === r.old_provider_id) { showNotice('新打手不能与原打手相同'); return }
    if (r.pool === null) { transferSubmitError.value = '可分配份额未确认，请刷新订单后核对'; return }
    if ((r.split_type === 'FIXED' ? transferCoinRaw(r.split_fixed_yuan) === null : !validTransferPercent(r.split_percent)) || transferCoinRaw(r.penalty_yuan) === null) return
  }
  const newIds = picked.map((r) => r.new_provider_id)
  if (new Set(newIds).size !== newIds.length) { showNotice('多个转入新打手不能重复'); return }

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
  transferPrompt.value = { orderNo, reason: reason.trim(), summary }
  const accepted = await new Promise<boolean>(resolve => { cancelTransferPrompt = resolve })
  if (!accepted || confirmation !== transferConfirmationSequence || pageDisposed || revision !== auth.sessionRevision
    || !auth.hasPerm('order:dispatch') || !transferVisible.value || transferOrder.value?.id !== orderId || transferSubmitError.value
    || version !== (orderVersions.get(orderId) || 0) || transferReview.value) return
  beginTransferReview(orderId, transferOrder.value.order_no || `#${orderId}`)
  transferring.value = true
  try {
    await orderApi.action(orderId, 'transfer', { transfers, reason }, true)
    if (pageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('order:dispatch') || !transferVisible.value || transferOrder.value?.id !== orderId) return
    clearTransferReview()
    successNotice('转单成功')
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
  invalidateTransferPrompt()
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
  query.legacy_team = ''
  Object.assign(query, { order_no: '', order_no_exact: '', customer_username: '', customer_nickname: '', customer_phone: '', customer_account_id: marker.customerId, created_day: '', status: '', payment_status: '', dispatch_route: '', actionable_staff: '', aftersale_open: '', overdue_pending: '' })
  tableRef.value?.reload()
}
async function finishDispatchReview() {
  const marker = dispatchReview.value, revision = auth.sessionRevision
  if (!marker || dispatching.value) return
  try {
    await actionPrompt.confirm('请确认已核对该老板的订单与扣款，并了解请求仍可能在处理中。解除后可重新发起快捷派单。', '解除快捷派单限制', {
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
  if (dispatchConfirming.value) actionPrompt.close()
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
  if (dispatchReview.value) { showNotice('上次快捷派单结果待核对，请先核对订单和扣款'); return }
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
  if (dispatchVisible.value || dispatching.value) { showNotice('请先完成当前派单'); return }
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
  if (removed) showNotice('已移除不符合陪玩选择的人员，请重新选择')
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
  if (!dispatchForm.customer_id) { showNotice('请选择下单老板'); return }
  if (!dispatchForm.service_id) { showNotice('请选择服务'); return }
  const providerCount = serviceProviderCount(currentService.value)
  if (providerCount === null) { showNotice('套餐人数尚未加载，请重新选择服务'); return }
  if (dispatchForm.escort_mode !== (providerCount === 2 ? 'DOUBLE' : 'SINGLE')
      || dispatchForm.providers.length !== providerCount) {
    showNotice('陪玩人数与所选套餐不一致，请重新选择服务')
    return
  }
  if (!dispatchForm.game_rounds || dispatchForm.game_rounds < 1) {
    showNotice('局数至少为 1')
    return
  }
  const picked = dispatchForm.providers.filter((s: any) => s.provider_id)
  if (dispatchForm.escort_mode === 'DOUBLE') {
    if (picked.length !== 2) { showNotice('双陪需指定 2 名打手'); return }
    const ids = picked.map((s: any) => s.provider_id)
    if (new Set(ids).size !== ids.length) { showNotice('不可重复指派同一打手'); return }
  }
  if (dispatchCommissionError.value) { showNotice(dispatchCommissionError.value); return }
  if (dispatchPendingPercentage() && dispatchPendingRate.value == null) { showNotice('请等待或填写待接单陪玩的抽成比例'); return }
  if (quoteLoading.value || !dispatchQuote.value || quotedInputs !== dispatchQuoteKey() || quotedSession !== auth.sessionRevision) { showNotice('请等待成交价及抽成试算完成后再派单'); return }
  if (insufficient.value) { showNotice('老板余额不足，无法派单'); return }
  if (!dispatchFixed.value && picked.some((slot: any) => slot.suggestion_loading)) { showNotice('请等待陪玩抽成建议加载完成'); return }
  if (!dispatchFixed.value && picked.some((slot: any) => commissionError('PERCENT', slot.commission_rate, 0))) { showNotice('陪玩抽成比例须为 0 到 100 的整数'); return }
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
    await actionPrompt.confirm(h('div', { class: 'od-transfer-confirm' }, [
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
    showNotice('派单信息或试算已变化，请重新核对')
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
    successNotice('派单成功')
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
      await operation.wait(actionPrompt.confirm(`订单 ${row.order_no || `#${row.id}`}：${consequence}`, action === 'start' ? '开始服务' : '完成服务', {
        type: 'warning', confirmButtonText: action === 'start' ? '确认开始' : '确认完成', cancelButtonText: '取消',
      }))
    } catch { return }
    if (!operation.current()) return
    // The order's own update may arrive over WebSocket before the HTTP response.
    pendingActionOrderId = null
    try {
      await request({ url: `/orders/${row.id}/${action}/`, method: 'post', silent: true })
      if (!operation.current(true)) return
      successNotice(action === 'start' ? '服务已开始' : '服务已完成，结算状态请查看订单详情')
    } catch (error: any) {
      if (!operation.current(true)) return
      const body = error?.response?.data || error
      const known = (!error?.response?.status || error.response.status < 500) && typeof body?.code === 'number' && body.code >= 400 && body.code < 500 && typeof body.msg === 'string'
      showNotice(known ? `${action === 'start' ? '开始' : '完成'}未成功：${body.msg}` : '操作结果未确认，请刷新订单并核对状态后再操作')
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
  invalidateTransferPrompt()
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
.od-dispatch-form { display:grid;gap:16px;border:0;padding:0;margin:0;min-width:0; }
.od-input-grid { display:grid;grid-template-columns:repeat(24,minmax(0,1fr));gap:16px; }
.od-input-col { min-width:0; }
.od-dispatch-field { display:grid;gap:8px;min-width:0;margin-bottom:12px;font-size:13px; }
.od-field-error { margin:0;color:var(--destructive);font-size:12px; }
.od-inline-icon { display:inline-flex;width:1em;height:1em;vertical-align:middle; }
.od-inline-icon :deep(svg){width:1em;height:1em;}
.od-filter-check, .od-commission-choices, .od-commission-option { display:inline-flex;align-items:center;gap:8px;flex-wrap:wrap; }
:global(.od-dispatch-dialog){max-width:min(800px,calc(100vw - 24px));max-height:calc(100dvh - 24px);overflow-y:auto;}
@media(max-width:720px){.od-input-grid>.od-input-col{grid-column:1 / -1!important;}}

:global(.od-detail-sheet) { position: fixed; left: auto; right: 0; top: 0; transform: none; translate: none; width: min(520px, 100vw); max-width: none; height: 100dvh; max-height: 100dvh; overflow-y: auto; border-radius: 0; display: block; }
:global(.od-refund-dialog) { width: min(720px, calc(100vw - 32px)); max-width: 720px; max-height: calc(100dvh - 32px); overflow-y: auto; }
:global(.od-config-dialog) { width: calc(100vw - 32px); max-height: calc(100dvh - 32px); overflow-y: auto; }
:global(.od-config-dialog--small) { max-width: 560px; }
:global(.od-config-dialog--medium) { max-width: 960px; }
:global(.od-config-dialog--large) { max-width: 1100px; }
:global(.od-assign-dialog) { width: min(480px, calc(100vw - 32px)); max-width: 480px; min-width: 0; grid-template-columns: minmax(0, 1fr); }
:global(.od-transfer-dialog) { width: min(880px, calc(100vw - 32px)); max-width: 880px; min-width: 0; grid-template-columns: minmax(0, 1fr); }
:global(.od-transfer-dialog > *) { min-width: 0; }
:global(.od-transfer-confirm-dialog) { width: min(560px, calc(100vw - 32px)); max-width: 560px; min-width: 0; grid-template-columns: minmax(0, 1fr); }
.od-transfer-reason { display: grid; gap: 8px; font-size: 12px; font-weight: 600; }
.od-transfer-search { display: grid; grid-template-columns: minmax(0, 1fr); gap: 8px; width: 100%; min-width: 0; }
.od-transfer-choice { display: flex; align-items: flex-start; gap: 8px; margin-bottom: 8px; }
.od-transfer-fields { display: grid; grid-template-columns: minmax(0, 1.6fr) repeat(3, minmax(0, 1fr)); gap: 12px; }
.od-transfer-field { display: grid; align-content: start; min-width: 0; gap: 8px; font-size: 12px; }
.od-transfer-field :deep(.group\/native-select) { width: 100%; }
.od-transfer-error { color: var(--destructive); }
@media (max-width: 600px) {
  .od-transfer-fields { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .od-transfer-field--provider { grid-column: 1 / -1; }
}
:global(.od-assign-dialog > *) { min-width: 0; }
.od-assign-search, .od-assign-deadline { display: grid; grid-template-columns: minmax(0, 1fr); min-width: 0; gap: 8px; }
.od-assign-search > label, .od-assign-deadline > strong { font-size: 12px; font-weight: 600; }
.od-assign-results { display: grid; gap: 4px; max-height: 208px; overflow-y: auto; }
.od-assign-candidate { display: grid; grid-template-columns: minmax(0, 1fr); gap: 4px; width: 100%; min-width: 0; min-height: 44px; padding: 8px; text-align: left; overflow-wrap: anywhere; border: 1px solid var(--border); border-radius: 8px; background: var(--card); }
.od-assign-candidate[aria-pressed="true"] { border-color: var(--primary); background: color-mix(in srgb, var(--primary) 8%, var(--card)); }
.od-assign-candidate:disabled { opacity: .5; }
.od-assign-candidate:focus-visible { outline: 2px solid var(--ring); outline-offset: 2px; }
.od-assign-deadline span { font-size: 12px; }
.od-refund-input { display: grid; gap: 8px; }
.od-aftersale-resolution { display: grid; gap: 8px; }
.od-logs { display: grid; gap: 12px; padding-left: 20px; }
.od-logs time { display: block; color: var(--muted-foreground); font-size: 12px; }
.od-table-width { min-width: 1640px; }
.od-row-actions { display: flex; flex-wrap: wrap; gap: 4px; }
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
.od-game-value { overflow-wrap: anywhere; }
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
  color: var(--muted-foreground);
  font-size: 12px;
  line-height: 1.4;
}
.od-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.od-mobile-list { display: grid; gap: 12px; }
.od-mobile-card { padding: 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.od-mobile-head { display: flex; align-items: center; gap: 8px; }
.od-mobile-head strong { flex: 1; min-width: 0; font-size: 13px; overflow-wrap: anywhere; color: var(--foreground); }
.od-mobile-service { margin: 12px 0; color: var(--foreground); font-weight: 600; overflow-wrap: anywhere; }
.od-mobile-values { display: grid; gap: 8px; margin: 0; padding: 12px 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.od-mobile-values > div { display: flex; justify-content: space-between; gap: 12px; }
.od-mobile-values dt { color: var(--muted-foreground); }
.od-mobile-values dd { margin: 0; text-align: right; overflow-wrap: anywhere; }
.od-deadline-status { margin-left: 4px; }
.od-customer-identity { min-width: 0; line-height: 1.4; overflow-wrap: anywhere; }
:deep(.od-customer-column .cell) { white-space: normal; }
.od-customer-account { display: block; color: var(--muted-foreground); font-size: 12px; line-height: 1.4; overflow-wrap: anywhere; }
.od-identity-help { margin-left: 6px; }
.od-mobile-time { margin: 10px 0 0; color: var(--muted-foreground); font-size: 12px; }
.od-mobile-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }

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
.od-aftersale-history { margin-top: 12px; font-size: 12px; }
.od-aftersale-history summary { cursor: pointer; color: var(--primary); padding: 8px 0; }
.od-aftersale-history-item { margin-top: 8px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px; overflow-wrap: anywhere; }
.od-aftersale-history-item p { margin: 4px 0; }
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
@media (max-width: 720px) {
  .od-dispatch-footer { flex-direction: column; align-items: stretch; gap: 10px; }
  .od-summary { gap: 6px 18px; padding: 8px 10px; }
  .od-summary-note, .od-summary-warn { flex-basis: 100%; }
}
</style>
