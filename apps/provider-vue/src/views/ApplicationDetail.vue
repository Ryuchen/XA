<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Badge } from '@xa/shadcn-vue/badge'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { api } from '../lib/session'
import { coalesced, onRealtime } from '../lib/realtime'

type Application = { id: number; status: string; ready: boolean; is_partner: boolean; partner_confirmed: boolean; members: { id: number; nickname: string; level: string }[] }
type Detail = { order: { order_no: string; status?: string; dispatch_route?: string; payment_status?: string; pending_deadline_expired?: boolean; service_name_snapshot?: string; service?: { name?: string }; reservation_origin?: { start_time: string; end_time: string } }; application: Application; can_confirm: boolean; closed_reason?: string; confirm_reason: string; selectable?: boolean | null; selection_reason?: string }
const route = useRoute(), router = useRouter(), orderId = Number(route.params.orderId)
const applicationId = ref(Number(route.query.application) || 0), detail = ref<Detail | null>(null), applications = ref<Application[] | null>(null)
const loading = ref(true), busy = ref(false), error = ref(''), notice = ref(''), confirming = ref(false)
const declining = computed(() => !!detail.value?.application.is_partner && !detail.value.application.partner_confirmed)
const statusText = computed(() => {
  const data = detail.value
  if (!data) return ''
  if (data.closed_reason) return data.closed_reason
  if (data.application.ready) return data.selectable === false ? '队伍已确认，当前暂不可供老板选择' : '队伍已确认，等待老板选择'
  return data.can_confirm ? '需要你确认组队' : '等待队友确认'
})
let sequence = 0
const valid = () => Number.isSafeInteger(orderId) && orderId > 0 && Number.isSafeInteger(applicationId.value) && applicationId.value >= 0
async function load() {
  const current = ++sequence; loading.value = true; error.value = ''
  if (!valid()) { error.value = '报名链接无效，请返回消息中心'; loading.value = false; return }
  try {
    if (applicationId.value) {
      const data = await api<Detail>(`/orders/orders/${orderId}/applications/${applicationId.value}/mine/`)
      if (current === sequence) { detail.value = data; applications.value = null }
    } else {
      const data = await api<{ applications: Application[] }>(`/orders/orders/${orderId}/applications/mine/`)
      if (current === sequence) { applications.value = data.applications || []; detail.value = null }
    }
  } catch (cause) { if (current === sequence) { detail.value = null; applications.value = null; error.value = cause instanceof Error ? cause.message : '报名暂时无法查看' } }
  finally { if (current === sequence) loading.value = false }
}
async function act(action: 'CONFIRM_TEAM' | 'WITHDRAW') {
  if (!detail.value || busy.value) return
  const wasDeclining = declining.value; busy.value = true; notice.value = ''; confirming.value = false
  try {
    await api(`/orders/orders/${orderId}/applications/${applicationId.value}/action/`, { method: 'POST', data: { action } })
    notice.value = action === 'CONFIRM_TEAM' ? '组队已确认' : wasDeclining ? '已拒绝邀请' : '报名已撤回'
  } catch (cause) {
    const failure = cause as { status?: number; message?: string }
    notice.value = failure.status && failure.status < 500 ? failure.message || '操作失败' : '操作结果未确认，请核对报名状态'
  } finally { await load(); busy.value = false }
}
const refresh = coalesced(load)
const offOrder = onRealtime('order_status_update', data => { if (!document.hidden && data && typeof data === 'object' && Number((data as { id?: unknown }).id) === orderId) refresh.request() })
const offConnected = onRealtime('connected', () => { if (!document.hidden) refresh.request() })
function onVisible() { if (!document.hidden) refresh.request() }
watch(applicationId, () => { notice.value = ''; void load() })
onMounted(() => { void load(); document.addEventListener('visibilitychange', onVisible) })
onUnmounted(() => { sequence++; refresh.dispose(); offOrder(); offConnected(); document.removeEventListener('visibilitychange', onVisible) })
const date = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
</script>

<template>
  <main class="page application-page">
    <header class="page-header">
      <div><p class="eyebrow">兴安电竞 · 陪玩端</p><h1>报名详情</h1></div>
      <Button variant="outline" size="xs" @click="router.push('/messages')">返回消息</Button>
    </header>

    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <Card v-if="loading" class="application-card"><p role="status">正在加载报名…</p></Card>
    <Card v-else-if="error" class="application-card">
      <p role="alert" class="error">{{ error }}</p>
      <div><Button size="xs" variant="outline" @click="load">重新查看</Button></div>
    </Card>

    <Card v-else-if="applications" class="application-card">
      <div><h2>当前待处理报名</h2><p class="hint">这条消息没有指定邀请，请选择报名查看。</p></div>
      <div v-if="applications.length" class="choices">
        <Button
          v-for="item in applications" :key="item.id"
          class="choice" size="xs" variant="outline" @click="applicationId = item.id"
        >
          <span class="team-name">{{ item.members.map(member => member.nickname).join(' + ') }}</span>
          <Badge variant="secondary">{{ item.ready ? '已确认' : '待确认' }}</Badge>
        </Button>
      </div>
      <p v-else class="hint">当前没有待处理的报名。</p>
    </Card>

    <template v-else-if="detail">
      <Card class="application-card summary">
        <div class="service-heading">
          <h2>{{ detail.order.service_name_snapshot || detail.order.service?.name || '陪玩服务' }}</h2>
          <p class="order-no"><span>订单号</span> {{ detail.order.order_no }}</p>
        </div>
        <section class="decision" aria-label="报名状态">
          <h3>报名状态</h3>
          <p class="state-text">{{ statusText }}</p>
          <p v-if="detail.confirm_reason && detail.confirm_reason !== statusText" class="hint">{{ detail.confirm_reason }}</p>
          <p v-if="detail.selectable === false && detail.selection_reason" class="hint">{{ detail.selection_reason }}</p>
        </section>
        <div class="actions">
          <Button v-if="detail.application.status === 'WITHDRAWN'" size="xs" variant="outline" @click="router.push('/orders')">回到抢单池</Button>
          <Button v-if="detail.application.status === 'NOT_SELECTED' && detail.order.status === 'PENDING' && detail.order.dispatch_route === 'PUBLIC' && detail.order.payment_status === 'PAID' && detail.order.pending_deadline_expired === false" size="xs" variant="outline" @click="router.push('/orders')">前往抢单池</Button>
          <Button v-if="detail.application.status === 'SELECTED'" size="xs" @click="router.push({ path: '/orders', query: { order: orderId } })">查看订单</Button>
          <template v-if="!detail.closed_reason">
            <Button v-if="detail.application.is_partner && !detail.application.partner_confirmed" size="xs" :disabled="busy || !detail.can_confirm" @click="act('CONFIRM_TEAM')">确认组队</Button>
            <Button size="xs" variant="outline" :disabled="busy" @click="confirming = true">{{ declining ? '拒绝邀请' : '撤回报名' }}</Button>
            <Button size="xs" variant="outline" :disabled="busy" @click="load">刷新报名状态</Button>
          </template>
          <Button v-if="!route.query.application" size="xs" variant="outline" :disabled="busy" @click="applicationId = 0">查看其他报名</Button>
        </div>
      </Card>

      <div class="detail-grid">
        <Card v-if="detail.order.reservation_origin" class="application-card">
          <h2>原预约时段<span class="timezone">北京时间</span></h2>
          <dl class="period">
            <div><dt>开始</dt><dd>{{ date(detail.order.reservation_origin.start_time) }}</dd></div>
            <div><dt>结束</dt><dd>{{ date(detail.order.reservation_origin.end_time) }}</dd></div>
          </dl>
          <p class="hint">请与老板确认服务安排。</p>
        </Card>
        <Card class="application-card">
          <h2>组队成员</h2>
          <ul class="members">
            <li v-for="member in detail.application.members" :key="member.id"><strong>{{ member.nickname }}</strong><span v-if="member.level">{{ member.level }}</span></li>
          </ul>
        </Card>
      </div>
    </template>

    <Dialog v-model:open="confirming">
      <DialogContent class="application-withdraw-dialog">
        <DialogHeader>
          <DialogTitle>{{ declining ? '拒绝邀请' : '撤回报名' }}</DialogTitle>
          <DialogDescription>{{ declining ? '拒绝后，这次组队报名将取消。' : '撤回后，这次组队报名将取消。' }}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button size="xs" variant="outline" @click="confirming = false">取消</Button>
          <Button size="xs" :disabled="busy" @click="act('WITHDRAW')">{{ declining ? '确认拒绝' : '确认撤回' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </main>
</template>

<style scoped>
.application-page { display:grid; gap:16px; max-width:800px; }
.page-header { display:flex; align-items:center; justify-content:space-between; gap:8px; }
h1,h2,h3,p,dl,dd,ul { margin:0; }
h1 { margin-top:4px; font-size:24px; line-height:32px; }
h2 { font-size:16px; line-height:24px; }
h3,.eyebrow,.hint,.order-no,.period dt,.members span,.timezone { color:var(--muted-foreground); font-size:12px; line-height:16px; }
h3 { font-weight:500; }
.application-card { min-width:0; gap:12px; padding:16px; border:1px solid var(--border); border-radius:12px; background:var(--card); box-shadow:none; overflow-wrap:anywhere; }
.service-heading h2 { font-size:20px; line-height:28px; }
.hint,.order-no { margin-top:4px; line-height:20px; }
.order-no { display:flex; flex-wrap:wrap; gap:4px 8px; }
.decision { display:grid; gap:4px; padding-top:12px; border-top:1px solid var(--border); }
.state-text { color:var(--foreground); font-size:16px; line-height:24px; font-weight:600; }
.decision .hint { margin:0; }
.actions { display:flex; flex-wrap:wrap; gap:8px; }
.actions:empty { display:none; }
.choices { display:grid; gap:4px; }
.choices .choice { display:flex; width:100%; justify-content:space-between; gap:8px; min-height:32px; height:auto; padding:4px 8px; }
.team-name { min-width:0; text-align:start; white-space:normal; overflow-wrap:anywhere; }
.detail-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,256px),1fr)); align-items:start; gap:12px; }
.timezone { margin-left:8px; font-weight:400; }
.period { display:grid; gap:8px; }
.period div { display:grid; grid-template-columns:36px minmax(0,1fr); gap:4px; align-items:baseline; }
.period dd,.members li { font-size:14px; line-height:20px; }
.members { padding:0; list-style:none; }
.members li { display:flex; flex-wrap:wrap; justify-content:space-between; align-items:baseline; gap:4px 8px; padding:8px 0; }
.members li:first-child { padding-top:0; }
.members li:last-child { padding-bottom:0; }
.members li + li { border-top:1px solid var(--border); }
.notice { padding:8px 12px; border-radius:8px; background:var(--muted); color:#1e3a8a; font-size:14px; line-height:20px; }
.application-page :deep([data-slot='button']),:global(.application-withdraw-dialog [data-slot='button']) { border:1px solid transparent; font-family:inherit; font-size:12px; line-height:16px; box-shadow:none; }
.application-page :deep([data-variant='outline']),:global(.application-withdraw-dialog [data-variant='outline']) { background:var(--card); border-color:var(--border); color:var(--foreground); }
.application-page :deep([data-slot='button']:focus-visible),:global(.application-withdraw-dialog button:focus-visible) { outline:2px solid var(--primary); outline-offset:2px; }
@media(max-width:600px),(pointer:coarse) { .application-page :deep([data-slot='button']),:global(.application-withdraw-dialog [data-slot='button']) { min-width:44px; min-height:44px; } }
@media(max-width:600px) { .application-page { gap:12px; } .application-card { padding:12px; } .detail-grid { grid-template-columns:minmax(0,1fr); } }
:global(.application-withdraw-dialog [data-slot='dialog-close']) { padding:0; border:0; background:transparent; }
:global(.application-withdraw-dialog [data-slot='dialog-close'] svg) { width:12px; height:12px; }
:global(.application-withdraw-dialog [data-slot='dialog-title']) { padding-inline-end:24px; }
</style>
