<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { api } from '../lib/session'
import { coalesced, onRealtime } from '../lib/realtime'

type Message = { id: number; type: string; title: string; preview: string; detail?: string; action_url: string; related_order_id: number | null; is_read: boolean; created_at: string }
type Page = { messages: Message[]; unread: number; next_before: number | null }
const labels: Record<string, string> = { SYSTEM: '系统通知', ORDER: '订单消息', SUPPORT: '客服消息', PROMOTION: '活动通知' }
const router = useRouter(), items = ref<Message[]>([]), unread = ref(0), before = ref<number | null>(null)
const loaded = ref(false), loading = ref(false), moreLoading = ref(false), readBusy = ref(false)
const chatUnread = ref<number | null>(null), chatError = ref(false)
const error = ref(''), moreError = ref(''), notice = ref(''), detail = ref<Message | null>(null), detailLoading = ref(false), detailError = ref('')
const detailTarget = ref<Message | null>(null), detailMissing = ref(false)
let sequence = 0, detailSequence = 0, chatSequence = 0
function preview(item: Message) { return item.title === '收到新的服务评价' ? item.preview.replace('可在订单中查看并回复', '可查看评价并回复') : item.preview }
const detailOpen = computed({ get: () => detailLoading.value || !!detail.value || !!detailError.value, set: value => { if (!value) { detailSequence++; detailLoading.value = false; detail.value = null; detailTarget.value = null; detailError.value = ''; detailMissing.value = false } } })
async function page(cursor?: number) { return api<Page>(`/messages/?page=1&page_size=50${cursor ? `&before=${cursor}` : ''}`) }
async function load() {
  const current = ++sequence, oldest = items.value[items.value.length - 1]?.id
  loading.value = true; moreLoading.value = false; moreError.value = ''
  try {
    const rows: Message[] = []; let cursor: number | null = null; let count = 0
    do {
      const data = await page(cursor || undefined)
      if (current !== sequence) return
      rows.push(...data.messages); count = data.unread; cursor = data.next_before
    } while (oldest && !rows.some(row => row.id === oldest) && cursor)
    items.value = rows; unread.value = count; before.value = cursor; error.value = ''
  } catch (cause) { if (current === sequence) error.value = cause instanceof Error ? cause.message : '消息加载失败' }
  finally { if (current === sequence) { loading.value = false; loaded.value = true } }
}
async function more() {
  if (!before.value || loading.value || moreLoading.value) return
  const current = sequence; moreLoading.value = true; moreError.value = ''
  try {
    const data = await page(before.value); if (current !== sequence) return
    const ids = new Set(items.value.map(item => item.id)); items.value.push(...data.messages.filter(item => !ids.has(item.id)))
    unread.value = data.unread; before.value = data.next_before
  } catch (cause) { if (current === sequence) moreError.value = cause instanceof Error ? cause.message : '加载更早消息失败' }
  finally { if (current === sequence) moreLoading.value = false }
}
async function readAll() {
  if (readBusy.value) return
  readBusy.value = true; notice.value = ''
  try { await api('/messages/read-all/', { method: 'POST' }); await load(); notice.value = '已全部标记为已读' }
  catch (cause) { notice.value = cause instanceof Error ? cause.message : '标记失败，请重试' }
  finally { readBusy.value = false }
}
async function show(item: Message) {
  const current = ++detailSequence; detailTarget.value = item; detail.value = null; detailError.value = ''; detailMissing.value = false; detailLoading.value = true
  try { const result = await api<Message>(`/messages/${item.id}/`); if (current === detailSequence) { detail.value = result; refresh.request() } }
  catch (cause) { if (current === detailSequence) { detailMissing.value = (cause as { code?: number } | null)?.code === 404; detailError.value = detailMissing.value ? '消息不存在' : '消息暂时无法加载，请重试' } }
  finally { if (current === detailSequence) detailLoading.value = false }
}
function openRelated(message: Message) {
  const id = message.related_order_id
  if (!id) return
  const application = /^\/pages\/orders\/index\?application_id=([1-9]\d*)$/.exec(message.action_url || '')
  if (application || message.action_url === '/pages/orders/index?tab=pending') {
    void router.push({ path: `/applications/${id}`, query: application ? { application: application[1] } : {} })
  } else if (message.action_url === '/pages/orders/index?tab=pool' || message.title === '队友已撤回组队报名') void router.push('/orders')
  else void router.push({ path: '/orders', query: { order: id } })
}
function openReservation(message: Message) {
  const match = /^\/pages\/reservations\/index\?id=([1-9]\d*)$/.exec(message.action_url || '')
  if (match) void router.push({path:'/reservations', query:{id:match[1]}})
}
function openClaim(message: Message) {
  const match = /^\/pages\/reward-claim\/index\?id=([1-9]\d*)$/.exec(message.action_url || '')
  if (match) void router.push({path:'/points',query:{claim_id:match[1]}})
}
async function loadChatUnread() {
  const current = ++chatSequence
  try { const data = await api<{unread:number}>('/chat/unread/'); if (current === chatSequence) { chatUnread.value = data.unread; chatError.value = false } }
  catch { if (current === chatSequence) { chatUnread.value = null; chatError.value = true } }
}
const refresh = coalesced(load), refreshChat = coalesced(loadChatUnread)
const updateMessages = () => { if (!document.hidden) refresh.request() }
const updateChat = () => { if (!document.hidden) refreshChat.request() }
const update = () => { updateMessages(); updateChat() }
const offMessage = onRealtime('site_message_update', updateMessages), offChat = onRealtime('chat_message', updateChat), offConnected = onRealtime('connected', update)
onMounted(() => { void load(); void loadChatUnread(); document.addEventListener('visibilitychange', update) })
onUnmounted(() => { sequence++; detailSequence++; chatSequence++; refresh.dispose(); refreshChat.dispose(); offMessage(); offChat(); offConnected(); document.removeEventListener('visibilitychange', update) })
</script>

<template>
  <main class="page stack">
    <header class="row"><div><p class="muted">兴安电竞 · 陪玩端</p><h1>消息中心</h1></div><Button variant="outline" @click="router.push('/orders')">返回订单</Button></header>
    <div class="row"><p class="muted">{{ !loaded ? '正在读取消息' : error ? '消息暂时无法更新' : unread ? `${unread} 条未读通知` : '平台通知均已阅读' }}</p><Button v-if="unread" variant="outline" :disabled="readBusy" @click="readAll">{{ readBusy ? '处理中…' : '全部已读' }}</Button></div>
    <div><Button variant="outline" @click="router.push('/support-chat')">平台客服对话<template v-if="chatUnread"> · {{ chatUnread }} 条未读</template></Button><span v-if="chatError" class="muted"> 客服未读数暂时无法加载 <Button variant="outline" @click="loadChatUnread">重试</Button></span></div>
    <p v-if="notice" role="status" class="muted">{{ notice }}</p><p v-if="error" role="alert" class="error">{{ error }} <Button variant="outline" @click="load">重新加载</Button></p>
    <p v-if="!loaded" class="muted">加载中…</p><Card v-else-if="!items.length && !error" class="panel"><strong>暂无消息</strong><p class="muted">平台通知和订单动态会出现在这里</p></Card>
    <div v-if="items.length" class="stack messages"><button v-for="item in items" :key="item.id" class="message" @click="show(item)"><span class="type">{{ labels[item.type] || '系统通知' }}</span><span class="message-body"><strong>{{ item.title }}</strong><span class="muted">{{ preview(item) || '点击查看详情' }}</span><time class="muted">{{ new Date(item.created_at).toLocaleString() }}</time></span><span v-if="!item.is_read" class="dot" aria-label="未读"></span></button></div>
    <p v-if="moreError" class="error">{{ moreError }}</p><Button v-if="loaded && before" variant="outline" :disabled="moreLoading || loading" @click="more">{{ moreLoading ? '加载中…' : moreError ? '重试加载' : '加载更早消息' }}</Button>
    <Dialog v-model:open="detailOpen"><DialogContent><DialogHeader><DialogTitle>{{ detail?.title || '消息详情' }}</DialogTitle><DialogDescription>{{ detail ? new Date(detail.created_at).toLocaleString() : detailLoading ? '加载中…' : detailError }}</DialogDescription></DialogHeader><Button v-if="detailError && !detailMissing && detailTarget" variant="outline" @click="show(detailTarget)">重新加载</Button><template v-if="detail"><p>{{ preview(detail) }}</p><p class="detail">{{ detail.detail }}</p><Button v-if="/^\/pages\/reward-claim\/index\?id=[1-9]\d*$/.test(detail.action_url)" @click="openClaim(detail)">查看权益详情</Button><Button v-if="/^\/pages\/reservations\/index\?id=[1-9]\d*$/.test(detail.action_url)" @click="openReservation(detail)">查看预约</Button><Button v-if="detail.action_url === '/pages/report/index'" @click="router.push({ path: '/reports', query: detail.related_order_id ? { order: detail.related_order_id } : {} })">查看报单</Button><Button v-if="detail.related_order_id && ['收到新的服务评价', '客服更新了评价回复'].includes(detail.title)" @click="router.push({path:'/evaluations', query:{order:detail.related_order_id}})">查看评价</Button><Button v-if="['提现审核通过', '提现审核未通过'].includes(detail.title)" @click="router.push({path:'/wallet',query:{tab:'withdraw'}})">查看提现记录</Button><Button v-if="detail.related_order_id" @click="openRelated(detail)">{{ detail.action_url === '/pages/orders/index?tab=pool' || detail.title === '队友已撤回组队报名' ? '前往抢单池' : detail.action_url.includes('application_id=') || detail.action_url === '/pages/orders/index?tab=pending' ? '查看组队报名' : '查看关联订单' }}</Button><Button v-else-if="detail.action_url === '/pages/wallet/index'" @click="router.push('/wallet')">前往钱包</Button></template></DialogContent></Dialog>
  </main>
</template>
<style scoped>
h1 { margin: 4px 0 0; font-size: 24px; } p { margin: 0; } .messages { gap: 8px; } .message { display: flex; width: 100%; gap: 12px; text-align: left; padding: 16px; background: var(--card); border: 1px solid var(--border); border-radius: 12px; color: inherit; } .type { flex: none; color: #1e3a8a; font-size: 12px; } .message-body { display: grid; flex: 1; min-width: 0; gap: 4px; overflow-wrap: anywhere; } .dot { flex: none; width: 8px; height: 8px; border-radius: 50%; background: #2563eb; } .detail { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
