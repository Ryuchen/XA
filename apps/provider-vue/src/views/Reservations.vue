<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Badge } from '@xa/shadcn-vue/badge'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { api } from '../lib/session'
import { coalesced, onRealtime } from '../lib/realtime'

type Reservation = { id:number; reservation_no:string; status:string; start_time:string; end_time:string; duration_minutes:number; game_rounds:number; game_region:string; game_nickname:string; game_uid:string; remark:string; cancel_reason:string; reject_reason:string; customer:{name:string}; service:{name:string}; order:number | null; order_no:string; allowed_actions:string[] }
type Cursor = { start_time:string; id:number }
type Page = { items:Reservation[]; next_cursor:Cursor | null }
type Action = 'confirm' | 'reject' | 'cancel'
const statuses:Record<string,string> = { PENDING:'待确认', CONFIRMED:'已确认', REJECTED:'已拒绝', CANCELLED:'已取消', CONVERTED:'已转订单', EXPIRED:'已过期' }
const route = useRoute(), router = useRouter(), initial = Number(route.query.id)
const selected = ref(Number.isSafeInteger(initial) && initial > 0 ? initial : 0), items = ref<Reservation[]>([]), detail = ref<Reservation | null>(null), next = ref<Cursor | null>(null)
const loading = ref(true), busy = ref(false), error = ref(''), notice = ref(''), action = ref<Action | null>(null), reason = ref('')
let sequence = 0, alive = true, pendingRefresh = false
const dialogOpen = computed({ get: () => !!action.value, set: value => { if (!value && !busy.value) { action.value = null; reason.value = '' } } })
function date(value:string) { const time = new Date(value); return Number.isNaN(time.getTime()) ? '—' : time.toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false}) }
async function fetchPage(cursor:Cursor | null) { return api<Page>(`/orders/reservations/?role=provider&cursor=1&page_size=20${cursor ? `&before_time=${encodeURIComponent(cursor.start_time)}&before_id=${cursor.id}` : ''}`) }
async function load(cursor:Cursor | null = null, append = false) {
  const current = ++sequence; loading.value = true; error.value = ''
  try {
    if (selected.value) {
      const data = await api<Reservation>(`/orders/reservations/${selected.value}/`)
      if (!alive || current !== sequence) return
      detail.value = data
      if (action.value && !data.allowed_actions.includes(action.value)) { action.value = null; reason.value = '' }
    } else {
      const oldest = !append ? items.value[items.value.length-1]?.id : undefined, rows = append ? [...items.value] : [], seen = new Set(rows.map(row => row.id)); let cursorNext = cursor
      do {
        const data = await fetchPage(cursorNext); if (!alive || current !== sequence) return
        for (const row of data.items) if (!seen.has(row.id)) { seen.add(row.id); rows.push(row) }
        cursorNext = data.next_cursor
      } while (!append && oldest && !seen.has(oldest) && cursorNext)
      items.value = rows; next.value = cursorNext
    }
  } catch (cause) { if (alive && current === sequence) { if (selected.value) detail.value = null; error.value = cause instanceof Error ? cause.message : '预约暂时无法加载' } }
  finally { if (alive && current === sequence) loading.value = false }
}
function start(kind:Action) { if (!busy.value && detail.value?.allowed_actions.includes(kind)) { action.value = kind; reason.value = ''; notice.value = '' } }
async function act() {
  const row = detail.value, kind = action.value
  if (!row || !kind || busy.value || !row.allowed_actions.includes(kind)) return
  sequence++; loading.value = false; busy.value = true; notice.value = ''
  try {
    const data = await api<Reservation>(`/orders/reservations/${row.id}/${kind}/`, {method:'POST',data:{reason:kind === 'confirm' ? '' : reason.value.trim()}})
    if (!alive || selected.value !== row.id) return
    sequence++; detail.value = data; action.value = null; reason.value = ''; notice.value = '预约已更新'
  } catch (cause) {
    if (!alive || selected.value !== row.id) return
    const failure = cause as {status?:number;message?:string}
    notice.value = failure.status && failure.status < 500 ? failure.message || '操作失败' : '预约结果未确认，请核对状态'
    await load()
  } finally { if (alive) { busy.value = false; if (pendingRefresh) { pendingRefresh = false; void load() } } }
}
function requestRefresh() { if (document.hidden) return; if (busy.value) pendingRefresh = true; else refresh.request() }
const refresh = coalesced(load), offReservation = onRealtime('reservation_status_update', requestRefresh), offConnected = onRealtime('connected', requestRefresh)
function navigateReservation(id:number) { void router.push({path:'/reservations', query:id ? {id} : {}}) }
watch(() => route.query.id, value => { const id = Number(value); selected.value = Number.isSafeInteger(id) && id > 0 ? id : 0 })
watch(selected, () => { notice.value = ''; detail.value = null; action.value = null; reason.value = ''; void load() })
onMounted(() => { void load(); document.addEventListener('visibilitychange', requestRefresh) })
onUnmounted(() => { alive = false; sequence++; refresh.dispose(); offReservation(); offConnected(); document.removeEventListener('visibilitychange', requestRefresh) })
</script>

<template>
  <main class="page reservations-page">
    <header class="page-heading"><div><p class="caption">兴安电竞 · 陪玩端</p><h1>{{ selected ? '预约详情' : '我的预约' }}</h1></div>
      <Button size="xs" variant="outline" :disabled="busy" @click="router.push('/orders')">返回订单</Button>
    </header>
    <div v-if="selected"><Button size="xs" variant="outline" :disabled="busy" @click="navigateReservation(0)">查看全部预约</Button></div>
    <p v-else class="caption">确认档期后，转为订单才开始服务。时间均为北京时间。</p>
    <p v-if="notice && !action" role="status" class="feedback">{{ notice }}</p>
    <div v-if="error" role="alert" class="error"><span>{{ error }}</span><Button size="xs" variant="outline" @click="load()">重新加载</Button></div>
    <p v-if="loading" role="status" class="caption">正在加载预约…</p>
    <Card v-if="!loading && !error && !selected && !items.length" class="reservation-card"><p>暂无预约</p></Card>

    <template v-if="!selected">
      <div v-if="items.length" class="list-grid">
        <Card v-for="item in items" :key="item.id" class="reservation-card">
          <header class="card-heading"><h2>{{ item.service?.name || '陪玩服务' }}</h2><Badge :variant="item.status === 'PENDING' ? 'secondary' : 'outline'">{{ statuses[item.status] || item.status }}</Badge></header>
          <dl class="dates">
            <div><dt>开始</dt><dd>{{ date(item.start_time) }}</dd></div>
            <div><dt>结束</dt><dd>{{ date(item.end_time) }}</dd></div>
          </dl>
          <div class="list-footer"><p class="caption">老板 <strong>{{ item.customer?.name || '—' }}</strong></p><Button size="xs" variant="outline" @click="navigateReservation(item.id)">查看预约</Button></div>
        </Card>
      </div>
      <div v-if="!loading && next"><Button size="xs" variant="outline" @click="load(next,true)">加载更多</Button></div>
    </template>
    <template v-else-if="detail">
      <Card class="reservation-card schedule">
        <header class="card-heading"><div><h2>{{ detail.service?.name || '陪玩服务' }}</h2><p class="caption reservation-no">预约号 {{ detail.reservation_no }}</p></div>
          <Badge :variant="detail.status === 'PENDING' ? 'secondary' : 'outline'">{{ statuses[detail.status] || detail.status }}</Badge>
        </header>
        <section class="time-block">
          <div class="section-heading"><h3>预约时间</h3><span class="caption">北京时间 · {{ detail.duration_minutes }} 分钟</span></div>
          <dl class="dates"><div><dt>开始</dt><dd>{{ date(detail.start_time) }}</dd></div><div><dt>结束</dt><dd>{{ date(detail.end_time) }}</dd></div></dl>
        </section>
        <p v-if="detail.status === 'CONFIRMED'" class="caption">档期已确认，等待转为订单。</p>
        <p v-if="detail.status === 'EXPIRED'" class="caption">预约时段已结束，请与老板重新预约。</p>
        <div v-if="detail.status === 'CONVERTED'" class="linked-order"><span>订单 {{ detail.order_no || '已创建' }}</span>
          <Button v-if="detail.order" size="xs" variant="outline" @click="router.push({path:'/orders',query:{order:detail.order}})">查看订单</Button>
        </div>
        <div v-if="detail.reject_reason || detail.cancel_reason" class="reason"><h3>处理说明</h3><p>{{ detail.reject_reason || detail.cancel_reason }}</p></div>
        <div class="actions">
          <Button v-if="detail.allowed_actions.includes('confirm')" size="xs" :disabled="busy" @click="start('confirm')">确认预约</Button>
          <Button v-if="detail.allowed_actions.includes('reject')" size="xs" variant="outline" :disabled="busy" @click="start('reject')">拒绝预约</Button>
          <Button v-if="detail.allowed_actions.includes('cancel')" size="xs" variant="outline" :disabled="busy" @click="start('cancel')">取消预约</Button>
        </div>
      </Card>
      <Card class="reservation-card">
        <h2>老板与游戏</h2>
        <dl class="details">
          <div><dt>老板</dt><dd>{{ detail.customer?.name || '—' }}</dd></div><div><dt>数量</dt><dd>{{ detail.game_rounds }}</dd></div>
          <div v-if="detail.game_region"><dt>大区</dt><dd>{{ detail.game_region }}</dd></div>
          <div v-if="detail.game_nickname"><dt>游戏昵称</dt><dd>{{ detail.game_nickname }}</dd></div>
          <div v-if="detail.game_uid"><dt>游戏 UID</dt><dd>{{ detail.game_uid }}</dd></div>
          <div v-if="detail.remark"><dt>备注</dt><dd>{{ detail.remark }}</dd></div>
        </dl>
      </Card>
    </template>

    <Dialog v-model:open="dialogOpen">
      <DialogContent class="reservation-dialog" :show-close-button="!busy">
        <DialogHeader><DialogTitle>{{ action === 'confirm' ? '确认预约' : action === 'reject' ? '拒绝预约' : '取消预约' }}</DialogTitle>
          <DialogDescription>{{ action === 'confirm' ? '确认后请预留这段档期，转为订单后开始服务。' : '可填写原因，方便老板了解。' }}</DialogDescription>
        </DialogHeader>
        <div v-if="detail" class="dialog-context">
          <strong>{{ detail.service?.name || '陪玩服务' }}</strong>
          <span>北京时间 {{ date(detail.start_time) }} 至 {{ date(detail.end_time) }}</span>
        </div>
        <label v-if="action !== 'confirm'" class="reason-input">原因（选填）<Textarea v-model="reason" maxlength="255" :disabled="busy" /></label>
        <p v-if="notice" role="alert" class="feedback">{{ notice }}</p>
        <DialogFooter><Button size="xs" variant="outline" :disabled="busy" @click="action = null">返回</Button><Button size="xs" :disabled="busy" @click="act">{{ busy ? '处理中…' : '确认' }}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  </main>
</template>

<style scoped>
.reservations-page { display:grid; min-width:0; max-width:840px; gap:12px; }
.page-heading,.card-heading,.section-heading,.actions,.list-footer,.linked-order,.error { display:flex; align-items:center; gap:8px; }
.page-heading,.card-heading,.section-heading,.list-footer,.linked-order { justify-content:space-between; }
.card-heading,.list-footer { align-items:start; } .section-heading,.linked-order,.actions { flex-wrap:wrap; }
h1,h2,h3,p,dl,dd { margin:0; } h1 { margin-top:4px; font-size:24px; line-height:32px; }
h2 { font-size:16px; line-height:24px; font-weight:600; } h3,.list-footer strong { font-size:14px; line-height:20px; font-weight:600; }
.caption,dt,.dialog-context span,.reason-input { color:var(--muted-foreground); font-size:12px; line-height:16px; }
.list-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; align-items:start; }
.reservation-card { display:grid; gap:12px; min-width:0; padding:16px; border:1px solid var(--border); border-radius:12px; background:var(--card); box-shadow:none; overflow-wrap:anywhere; }
.reservations-page :deep([data-slot='badge']) { flex-shrink:0; border-color:var(--border); }
.schedule .card-heading h2 { font-size:20px; line-height:28px; }
.card-heading>div,.card-heading h2,.list-footer p,.linked-order span,.error span { min-width:0; overflow-wrap:anywhere; }
.reservation-no,.reason p { margin-top:4px; }
.dates,.details,.time-block,.dialog-context,.reason-input { display:grid; gap:4px; min-width:0; }
.time-block { gap:8px; padding-top:12px; border-top:1px solid var(--border); }
.dates>div,.details>div { display:grid; grid-template-columns:64px minmax(0,1fr); gap:8px; align-items:baseline; }
.dates>div { grid-template-columns:36px minmax(0,1fr); }
.dates dd,.details dd,.reason p,.linked-order { font-size:14px; line-height:20px; }
.dates dd { font-variant-numeric:tabular-nums; }
.details dd,.reason p { white-space:pre-wrap; }
.list-footer,.linked-order,.reason { padding-top:8px; border-top:1px solid var(--border); }
.list-footer { align-items:center; } .list-footer strong { color:var(--foreground); }
.actions:empty { display:none; } .feedback,.dialog-context { padding:8px 12px; border-radius:8px; background:var(--muted); color:var(--foreground); font-size:13px; line-height:20px; overflow-wrap:anywhere; }
.reservations-page :deep([data-slot='button']),:global(.reservation-dialog [data-slot='button']) { border:1px solid transparent; font:600 12px/16px system-ui; box-shadow:none; }
.reservations-page :deep([data-variant='outline']),:global(.reservation-dialog [data-variant='outline']) { border-color:var(--border); color:var(--foreground); background:var(--card); }
.reservations-page :deep(button:focus-visible),:global(.reservation-dialog button:focus-visible) { outline:2px solid var(--primary); outline-offset:2px; }
@media(max-width:640px) { .list-grid { grid-template-columns:minmax(0,1fr); } .reservation-card { padding:12px; } }
@media(max-width:600px),(pointer:coarse) { .reservations-page :deep([data-slot='button']),:global(.reservation-dialog [data-slot='button']) { min-width:44px; min-height:44px; } .reason-input :deep(textarea) { font-size:16px; } }
</style>
