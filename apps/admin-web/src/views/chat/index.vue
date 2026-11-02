<template>
  <div class="chat-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">处理站内咨询会话，实时收发消息与图片；微信客服会话由企业微信工作台接待。</p>
    </div>
    <p v-if="notice" role="status" class="chat-notice">{{ notice }}</p>
    <div class="chat-workbench">
    <!-- 左侧会话列表 -->
    <div class="session-pane">
      <div class="session-toolbar">
        <Input v-model="keyword" placeholder="搜索用户账号/昵称" aria-label="搜索用户账号或昵称" @keyup.enter="loadSessions()" @update:model-value="value => { if (value === '') loadSessions() }" />
        <Button :variant="onlyUnread ? 'default' : 'outline'" @click="toggleUnread">
          未读
        </Button>
      </div>
      <div class="session-list">
        <button
          v-for="s in sessions"
          :key="s.id"
          type="button"
          class="session-item"
          :class="{ active: current?.id === s.id }"
          :aria-pressed="current?.id === s.id"
          @click="openSession(s)"
        >
          <span class="session-avatar"><img v-if="s.user_avatar" :src="s.user_avatar" alt="" /><span v-else>{{ (s.user_name || '?').charAt(0) }}</span><span v-if="s.unread_support" class="session-unread" :aria-label="`${s.unread_support} 条未读`">{{ s.unread_support }}</span></span>
          <div class="session-meta">
            <div class="session-top">
              <span class="session-name">{{ s.user_name }}</span>
              <span class="session-time">{{ shortTime(s.last_message_at) }}</span>
            </div>
            <div class="session-preview">{{ s.last_message || '暂无消息' }}</div>
          </div>
        </button>
        <p v-if="!sessions.length" class="chat-empty">暂无会话</p>
        <Button v-if="sessions.length < sessionTotal" variant="outline" :disabled="sessionsLoading" class="load-more-sessions" @click="loadSessions(false)">{{ sessionsLoading ? '加载中…' : '加载更多会话' }}</Button>
      </div>
    </div>

    <!-- 右侧对话窗口 -->
    <div class="chat-pane">
      <template v-if="current">
        <div class="chat-header">
          <span class="chat-title">{{ current.user_name }}</span>
          <Badge variant="outline">{{ current.user_role_display }}</Badge>
        </div>
        <Alert v-if="!sessionsLoading && !sessions.some(row => row.id === current.id)"><AlertDescription>当前会话不在已加载的列表中 <Button v-if="keyword || onlyUnread" variant="link" @click="clearSessionFilters">清除筛选</Button></AlertDescription></Alert>
        <div ref="msgScrollRef" class="msg-area" @scroll="onMessageScroll">
          <div v-if="hasOlder" class="history-older"><Button variant="outline" :disabled="historyLoading" @click="loadOlder">{{ historyLoading ? '加载中…' : '加载更早消息' }}</Button></div>
          <Alert v-if="historyError" variant="destructive"><AlertDescription>{{ historyError }}</AlertDescription></Alert>
          <div
            v-for="m in messages"
            :key="m.id"
            class="msg-row"
            :data-message-id="m.id"
            :class="m.is_from_support ? 'row-right' : 'row-left'"
          >
            <div class="msg-wrap">
              <div class="msg-time">{{ formatDateTime(m.created_at) }}</div>
              <PrivateImage
                v-if="m.content_type === 'IMAGE'"
                :src="m.image_url"
                :preview-src-list="[m.image_url]"
                fit="cover"
                class="msg-image"
              />
              <div v-else class="bubble" :class="m.is_from_support ? 'bubble-right' : 'bubble-left'">
                {{ m.content }}
              </div>
              <div v-if="m.content_type === 'IMAGE' && m.content" class="bubble image-caption" :class="m.is_from_support ? 'bubble-right' : 'bubble-left'">{{ m.content }}</div>
              <div v-if="auth.hasPerm('order:view')" class="message-orders">
                <Button v-for="orderNo in orderNumbers(m.content)" :key="orderNo" variant="link" @click="viewOrder(orderNo)">查看订单 {{ orderNo }}</Button>
              </div>
            </div>
          </div>
          <p v-if="!messages.length" class="chat-empty">开始对话吧</p>
        </div>
        <div v-if="historyBrowsing" class="history-return"><Button variant="outline" @click="returnToLatest">回到最新消息</Button></div>

        <div class="input-area" v-if="auth.hasPerm('chat:reply')">
          <Alert v-if="pending"><AlertTitle>{{ pendingSaved ? '上次发送结果未确认，请先核对。' : '上次发送结果未确认，编号未保存，请保持此页面并先核对。' }}</AlertTitle><AlertDescription>
            <Button variant="outline" :disabled="sending" @click="checkPending">核对结果</Button>
            <Button v-if="pending.content !== undefined || pending.image" variant="outline" :disabled="sending" @click="retryPending">重试原消息</Button>
            <Button variant="outline" :disabled="sending" @click="abandonPending">放弃核对</Button>
          </AlertDescription></Alert>
          <div class="input-actions">
            <label class="image-picker" :class="{ disabled: sending || !!pending }">图片<input type="file" accept="image/*" :disabled="sending || !!pending" @change="onImageChange" /></label>
          </div>
          <textarea v-model="input" class="chat-input" rows="3" maxlength="1000" aria-label="回复内容" placeholder="输入回复内容，Enter 发送 / Shift+Enter 换行" @keydown.enter.exact.prevent="onSendText" />
          <div class="input-footer">
            <span class="chat-count">{{ input.length }} / 1000</span><Button :disabled="sending || !!pending" @click="onSendText">{{ sending ? '发送中…' : '发送' }}</Button>
          </div>
        </div>
        <div v-else class="input-area no-perm">无回复权限</div>
      </template>
      <p v-else class="chat-empty">选择左侧会话开始</p>
    </div>
    </div>
  </div>
  <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
</template>

<script setup lang="ts">
import PrivateImage from "@/components/PrivateImage.vue"
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Badge } from '@xa/shadcn-vue/badge'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { chatApi } from '@/api/modules'
import { formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { adminWs } from '@/utils/ws'
import { attendedChatSession } from '@/utils/chatAttention'

const auth = useAuthStore()
const notice = ref('')
const feedback = { warning: (text: string) => { notice.value = text }, error: (text: string) => { notice.value = text }, success: (text: string) => { notice.value = text } }
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const router = useRouter()
const keyword = ref('')
const routeUnread = () => router.currentRoute?.value?.query?.only_unread === '1'
const onlyUnread = ref(routeUnread())
const sessions = ref<any[]>([])
const sessionPage = ref(1)
const sessionTotal = ref(0)
const sessionsLoading = ref(false)
let sessionSequence = 0
let messageSequence = 0
let historySequence = 0
let focusSequence = 0
const current = ref<any>(null)
const messages = ref<any[]>([])
const hasOlder = ref(false), beforeId = ref<number | null>(null), historyLoading = ref(false), historyError = ref('')
const input = ref('')
// Drafts belong to the visible staff session and stay only in this page.
const draftsBySession = new Map<number, string>()
const sending = ref(false)
type PendingReply = { id: string; sessionId: number; owner: string; content?: string; image?: File }
const pending = ref<PendingReply | null>(null), pendingSaved = ref(true)
const pendingBySession = new Map<string, PendingReply>()
const pendingKey = (owner: string, sessionId: number) => `xa:chat-reply:${owner}:${sessionId}`
function savePending(item: PendingReply): boolean {
  pendingBySession.set(pendingKey(item.owner, item.sessionId), item)
  try {
    if (!item.owner || typeof sessionStorage === 'undefined') return false
    sessionStorage.setItem(pendingKey(item.owner, item.sessionId), JSON.stringify({ id: item.id }))
    return true
  } catch { return false }
}
function restorePending(sessionId: number) {
  const owner = String(auth.profile?.id || ''), key = pendingKey(owner, sessionId)
  let item = owner ? pendingBySession.get(key) || null : null
  let stored: any = null
  try { if (owner) stored = JSON.parse(sessionStorage.getItem(key) || 'null') } catch { /* storage may be unavailable */ }
  if (!item && owner) {
    if (stored && /^[a-zA-Z0-9_-]{8,64}$/.test(stored.id)) item = { id: stored.id, owner, sessionId }
  }
  pending.value = item
  pendingSaved.value = !item || stored?.id === item.id
}
function clearPending(item: PendingReply) {
  const key = pendingKey(item.owner, item.sessionId)
  if (pendingBySession.get(key)?.id === item.id) pendingBySession.delete(key)
  try { const stored = JSON.parse(sessionStorage.getItem(key) || 'null'); if (stored?.id === item.id) sessionStorage.removeItem(key) } catch { /* in-memory state remains authoritative */ }
  if (pending.value?.id === item.id && pending.value.owner === item.owner && pending.value.sessionId === item.sessionId) pending.value = null
}
const msgScrollRef = ref<HTMLElement | null>(null)
const historyBrowsing = ref(false)
watch([() => current.value?.id, historyBrowsing], ([id, browsing]) => {
  attendedChatSession.value = !browsing && Number.isSafeInteger(id) ? id : null
}, { flush: 'sync' })

// Only offer a shortcut for the current system's complete order number.
// Other text stays untouched and can still be copied into the order search.
function orderNumbers(content: unknown): string[] {
  if (typeof content !== 'string') return []
  return [...new Set((content.match(/\bORD[0-9A-F]{20}\b/gi) || []).map(value => value.toUpperCase()))]
}
function viewOrder(orderNo: string) {
  if (auth.hasPerm('order:view')) void router.push({ path: '/orders', query: { find_order: orderNo } })
}

function shortTime(value?: string | null): string {
  if (!value) return ''
  const d = new Date(value)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function loadSessions(reset = true) {
  if (!reset && sessionsLoading.value) return
  const sequence = ++sessionSequence
  const page = reset ? 1 : sessionPage.value + 1
  sessionsLoading.value = true
  const params: Record<string, any> = { page, page_size: 20 }
  if (keyword.value) params.keyword = keyword.value
  if (onlyUnread.value) params.only_unread = 1
  try {
    const res = await chatApi.list(params)
    if (sequence !== sessionSequence) return
    const items = res.data.list || []
    sessions.value = reset ? items : [...new Map([...sessions.value, ...items].map(item => [item.id, item])).values()]
    sessionPage.value = page
    sessionTotal.value = res.data.total
  } finally {
    if (sequence === sessionSequence) sessionsLoading.value = false
  }
}

function toggleUnread() {
  onlyUnread.value = !onlyUnread.value
  const query = { ...(router.currentRoute?.value?.query || {}) }
  if (onlyUnread.value) query.only_unread = '1'
  else delete query.only_unread
  void router.replace?.({ query })
  loadSessions()
}

function clearSessionFilters() {
  keyword.value = ''
  onlyUnread.value = false
  const query = { ...(router.currentRoute?.value?.query || {}) }
  delete query.only_unread
  void router.replace?.({ query })
  void loadSessions()
}

watch(() => router.currentRoute?.value?.query?.only_unread, () => {
  const next = routeUnread()
  if (next !== onlyUnread.value) { onlyUnread.value = next; void loadSessions() }
})

async function scrollToBottom() {
  await nextTick()
  const wrap = msgScrollRef.value
  if (wrap) { wrap.scrollTop = wrap.scrollHeight; historyBrowsing.value = false }
}

async function acknowledgeVisible() {
  const sessionId = current.value?.id, revision = auth.sessionRevision
  if (!sessionId || document.hidden || historyBrowsing.value || !auth.hasPerm('chat:reply')) return
  await nextTick()
  if (revision !== auth.sessionRevision || current.value?.id !== sessionId || document.hidden || historyBrowsing.value) return
  const ids = messages.value.filter(item => !item.is_from_support && !item.is_read)
    .map(item => Number(item.id)).filter(id => Number.isSafeInteger(id) && id > 0)
  for (let offset = 0; offset < ids.length; offset += 200) {
    if (revision !== auth.sessionRevision || current.value?.id !== sessionId || document.hidden || historyBrowsing.value) return
    const selected = ids.slice(offset, offset + 200)
    const result = await chatApi.readIds(sessionId, selected)
    if (revision !== auth.sessionRevision || current.value?.id !== sessionId) return
    const row = sessions.value.find(item => item.id === sessionId)
    if (row) row.unread_support = result.data.unread_support
    for (const item of messages.value) if (selected.includes(Number(item.id))) item.is_read = true
  }
  // The read response is authoritative even if its realtime notification is
  // delayed. Refresh the filtered queue so resolved sessions leave it.
  if (ids.length && onlyUnread.value && revision === auth.sessionRevision && current.value?.id === sessionId) await loadSessions()
}
function onMessageScroll() {
  const wrap = msgScrollRef.value
  if (!wrap || historyLoading.value) return
  const wasBrowsing = historyBrowsing.value
  historyBrowsing.value = wrap.scrollHeight - wrap.clientHeight - wrap.scrollTop > 48
  if (wasBrowsing && !historyBrowsing.value && !document.hidden) void acknowledgeVisible().catch(() => {})
}
async function returnToLatest() { await scrollToBottom(); await acknowledgeVisible().catch(() => {}) }

function mergeMessages(items: any[]): any[] {
  return [...new Map(items.map(item => [item.id, item])).values()].sort((a, b) => {
    const time = Date.parse(a.created_at) - Date.parse(b.created_at)
    return Number.isFinite(time) && time !== 0 ? time : Number(a.id) - Number(b.id)
  })
}

async function loadOlder() {
  const sessionId = current.value?.id, cursor = beforeId.value
  if (!sessionId || !cursor || historyLoading.value) return
  const sequence = ++historySequence, revision = auth.sessionRevision
  const wrap = msgScrollRef.value
  const anchorId = messages.value[0]?.id
  const anchor = anchorId && wrap?.querySelector<HTMLElement>(`[data-message-id="${anchorId}"]`)
  const anchorTop = anchor?.getBoundingClientRect().top
  historyLoading.value = true; historyError.value = ''
  try {
    const result = await chatApi.messages(sessionId, { cursor: 1, before_id: cursor })
    if (sequence !== historySequence || revision !== auth.sessionRevision || current.value?.id !== sessionId) return
    historyBrowsing.value = true
    messages.value = mergeMessages([...(result.data.list || []), ...messages.value])
    hasOlder.value = !!result.data.has_more; beforeId.value = result.data.next_before_id || null
    await nextTick()
    if (sequence !== historySequence || revision !== auth.sessionRevision || current.value?.id !== sessionId) return
    const moved = anchorId && wrap?.querySelector<HTMLElement>(`[data-message-id="${anchorId}"]`)
    if (wrap && moved && anchorTop !== undefined) wrap.scrollTop += moved.getBoundingClientRect().top - anchorTop
  } catch {
    if (sequence === historySequence && revision === auth.sessionRevision && current.value?.id === sessionId) historyError.value = '更早消息加载失败，请重试'
  } finally { if (sequence === historySequence) historyLoading.value = false }
}

async function openSession(s: any, fromNotice = false) {
  if (!fromNotice) ++focusSequence
  const sequence = ++messageSequence
  ++historySequence; historyLoading.value = false; historyError.value = ''; hasOlder.value = false; beforeId.value = null
  if (current.value?.id) draftsBySession.set(current.value.id, input.value)
  current.value = s
  historyBrowsing.value = false
  restorePending(s.id)
  messages.value = []
  input.value = draftsBySession.get(s.id) || ''
  const res = await chatApi.messages(s.id, { cursor: 1 })
  if (sequence !== messageSequence || current.value?.id !== s.id) return
  messages.value = res.data.list || []
  hasOlder.value = !!res.data.has_more; beforeId.value = res.data.next_before_id || null
  await scrollToBottom()
  if (!document.hidden && s.unread_support) await acknowledgeVisible().catch(() => {})
}

watch(() => router.currentRoute?.value?.query?.session_id, async value => {
  if (typeof value !== 'string' || !/^[1-9]\d*$/.test(value)) return
  const id = Number(value)
  if (!Number.isSafeInteger(id)) return
  const sequence = ++focusSequence, revision = auth.sessionRevision
  try {
    const result = await chatApi.detail(id)
    if (sequence !== focusSequence || revision !== auth.sessionRevision || router.currentRoute?.value?.query?.session_id !== value) return
    await openSession(result.data, true)
    if (sequence !== focusSequence || revision !== auth.sessionRevision || router.currentRoute?.value?.query?.session_id !== value) return
    const query = { ...router.currentRoute.value.query }; delete query.session_id
    void router.replace({ query })
  } catch {
    if (sequence === focusSequence && revision === auth.sessionRevision) feedback.warning('会话暂时无法打开，请在列表中重试')
  }
}, { immediate: true })

function startReply(content?: string, image?: File) {
  if (!current.value || sending.value || pending.value || !auth.hasPerm('chat:reply')) return
  const owner = String(auth.profile?.id || '')
  if (!owner) { feedback.warning('正在读取客服身份，请稍后重试'); return }
  const item: PendingReply = { id: `chat-${Date.now()}-${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`, sessionId: current.value.id, owner, content, image }
  pending.value = item; pendingSaved.value = savePending(item)
  void retryPending()
}
function onSendText() { const content = input.value.trim(); if (content) startReply(content) }
function onImageChange(event: Event) { const inputElement = event.target as HTMLInputElement; const file = inputElement.files?.[0]; if (file) startReply(undefined, file); inputElement.value = '' }
function clearSentDraft(item: PendingReply) {
  if (item.content === undefined) return
  const saved = draftsBySession.get(item.sessionId)
  if (saved !== undefined && saved.trim() === item.content) draftsBySession.delete(item.sessionId)
  if (current.value?.id === item.sessionId && input.value.trim() === item.content) input.value = ''
}
async function retryPending() {
  const item = pending.value, revision = auth.sessionRevision
  if (!item || sending.value || (item.content === undefined && !item.image) || !auth.hasPerm('chat:reply')) return
  sending.value = true
  try {
    const fd = new FormData()
    if (item.content !== undefined) fd.append('content', item.content)
    if (item.image) fd.append('image', item.image)
    // Read only the messages actually loaded by this paged view. The legacy
    // reply threshold remains zero to avoid acknowledging unseen history.
    fd.append('read_through_id', '0'); fd.append('client_message_id', item.id)
    const res: any = await chatApi.reply(item.sessionId, fd)
    if (res.code !== 0 || !res.data || res.data.client_message_id !== item.id) throw new Error('结果未确认')
    clearPending(item)
    if (revision === auth.sessionRevision) clearSentDraft(item)
    if (revision === auth.sessionRevision && current.value?.id === item.sessionId) {
      appendMessage(res.data)
      void acknowledgeVisible().catch(() => {})
    }
  } catch (e: any) {
    if (revision !== auth.sessionRevision) return
    const status = e?.response?.status || (typeof e?.code === 'number' ? e.code : 0)
    if (status >= 400 && status < 500 && status !== 409 && status !== 429) { clearPending(item); feedback.error(e?.response?.data?.msg || e?.msg || '发送被拒绝') }
    else { feedback.warning('发送结果未确认，请先核对'); if (current.value?.id === item.sessionId) await refreshCurrentSession().catch(() => {}) }
  } finally { sending.value = false }
}
async function checkPending() {
  const item = pending.value, revision = auth.sessionRevision
  if (!item || sending.value || !auth.hasPerm('chat:reply')) return
  sending.value = true
  try {
    const result = await chatApi.messageStatus(item.sessionId, item.id)
    if (revision !== auth.sessionRevision || current.value?.id !== item.sessionId || pending.value?.id !== item.id) return
    if (result.data?.status === 'FOUND' && result.data.message?.client_message_id === item.id) {
      clearPending(item)
      clearSentDraft(item)
      appendMessage(result.data.message); feedback.success('消息已发送')
    } else feedback.warning('暂未查到结果，请稍后核对')
  } catch { if (revision === auth.sessionRevision) feedback.warning('暂时无法核对，请稍后重试') }
  finally { sending.value = false }
}
async function abandonPending() {
  const item = pending.value, revision = auth.sessionRevision
  if (!item || sending.value) return
  if (!await askConfirm({ title: '放弃核对？', description: '这条消息可能已经送达，请先查看聊天记录。', confirmText: '放弃核对', cancelText: '返回' })) return
  if (revision === auth.sessionRevision && pending.value?.id === item.id) clearPending(item)
}

function appendMessage(msg: any) {
  if (!messages.value.some((m) => m.id === msg.id)) {
    const follow = !historyBrowsing.value
    messages.value.push(msg)
    if (follow) void scrollToBottom()
  }
}

function bumpSession(sessionId: number, preview: string, fromSupport: boolean) {
  const idx = sessions.value.findIndex((s) => s.id === sessionId)
  if (idx === -1) {
    loadSessions()
    return
  }
  const s = sessions.value[idx]
  s.last_message = preview
  s.last_message_at = new Date().toISOString()
  if (!fromSupport && (current.value?.id !== sessionId || document.hidden || historyBrowsing.value)) {
    s.unread_support = (s.unread_support || 0) + 1
  }
  sessions.value.splice(idx, 1)
  sessions.value.unshift(s)
}

let offMessage: (() => void) | null = null
let offConnected: (() => void) | null = null
let offSession: (() => void) | null = null

watch(() => auth.sessionRevision, () => {
  ++sessionSequence; ++messageSequence; ++historySequence; ++focusSequence
  hasOlder.value = false; beforeId.value = null; historyLoading.value = false; historyError.value = ''
  draftsBySession.clear(); pendingBySession.clear(); pending.value = null; current.value = null; messages.value = []; input.value = ''; sessions.value = []
  if (auth.hasPerm('chat:view')) void loadSessions().catch(() => {})
})

// Re-read after reconnect or returning to the tab without discarding a draft.
// Only acknowledge messages returned by this read while the tab is visible.
async function refreshCurrentSession() {
  const sessionId = current.value?.id
  if (!sessionId) return
  const sequence = ++messageSequence
  let res = await chatApi.messages(sessionId, { cursor: 1 })
  if (sequence !== messageSequence || current.value?.id !== sessionId) return
  const existingIds = new Set(messages.value.map(message => message.id))
  const fetched = [...(res.data.list || [])]
  // Reconnection can miss more than one page. Read back until the new window
  // overlaps what is already displayed before acknowledging any of it.
  while (existingIds.size && !fetched.some(message => existingIds.has(message.id)) && res.data.has_more && res.data.next_before_id) {
    res = await chatApi.messages(sessionId, { cursor: 1, before_id: res.data.next_before_id })
    if (sequence !== messageSequence || current.value?.id !== sessionId) return
    fetched.push(...(res.data.list || []))
  }
  if (!existingIds.size) { hasOlder.value = !!res.data.has_more; beforeId.value = res.data.next_before_id || null }
  messages.value = mergeMessages([...fetched, ...messages.value])
  if (document.hidden || historyBrowsing.value) return
  await scrollToBottom()
  if (document.hidden || sequence !== messageSequence || current.value?.id !== sessionId) return
  await acknowledgeVisible()
}

function onVisibilityChange() {
  if (document.hidden) return
  void loadSessions().catch(() => {})
  void refreshCurrentSession().catch(() => {})
}

onMounted(() => {
  document.addEventListener('visibilitychange', onVisibilityChange)
  loadSessions()
  adminWs.connect()
  offMessage = adminWs.on('chat_message', (data: any) => {
    if (!data?.message) return
    const preview = data.message.content_type === 'IMAGE' ? '[图片]' : data.message.content
    bumpSession(data.session_id, preview, data.message.is_from_support)
    if (current.value?.id === data.session_id) {
      appendMessage(data.message)
      if (!document.hidden && !historyBrowsing.value && !data.message.is_from_support && auth.hasPerm('chat:reply')) {
        void acknowledgeVisible().catch(() => {})
      }
    }
  })
  offSession = adminWs.on('chat_session_update', () => { void loadSessions().catch(() => {}) })
  offConnected = adminWs.on('connected', () => {
    void loadSessions().catch(() => {})
    void refreshCurrentSession().catch(() => {})
  })
})

onUnmounted(() => {
  attendedChatSession.value = null
  ++focusSequence
  document.removeEventListener('visibilitychange', onVisibilityChange)
  ++sessionSequence; ++historySequence
  ++messageSequence
  offMessage?.()
  offConnected?.()
  offSession?.()
})
</script>

<style scoped lang="scss">
.chat-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.chat-workbench {
  display: flex;
  height: calc(100vh - 160px);
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}

.session-pane {
  width: 280px;
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
}

.session-toolbar {
  display: flex;
  gap: 8px;
  padding: 12px;
  border-bottom: 1px solid var(--border);
}

.session-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.session-item {
  display: flex;
  width: 100%;
  text-align: left;
  background: transparent;
  border: 0;
  align-items: center;
  gap: 10px;
  padding: 12px;
  cursor: pointer;
  border-bottom: 1px solid var(--border);
  transition: background-color 0.18s ease;
}

.session-item:hover, .session-item.active {
  background: var(--secondary);
}

.session-item.active {
  box-shadow: inset 3px 0 0 var(--primary);
}

.session-avatar {
  flex-shrink: 0;
  position: relative;
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--secondary);
}
.session-avatar img { width:100%; height:100%; border-radius:50%; object-fit:cover; }
.session-unread { position:absolute; right:-4px; top:-4px; min-width:18px; padding:0 4px; border-radius:12px; background:var(--destructive); color:var(--primary-foreground); font-size:11px; text-align:center; }
.chat-empty { padding:24px; text-align:center; color:var(--muted-foreground); }
.chat-notice { color:var(--muted-foreground); }
.chat-input { width:100%; padding:8px; resize:none; border:1px solid var(--border); border-radius:4px; background:var(--background); color:var(--foreground); }
.image-picker { position:relative; display:inline-flex; cursor:pointer; color:var(--primary); padding:8px; }
.image-picker.disabled { opacity:.5; cursor:not-allowed; }
.image-picker input { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
.image-picker:focus-within { outline:2px solid var(--primary); outline-offset:2px; }
.chat-count { color:var(--muted-foreground); font-size:12px; margin-right:8px; }

.session-meta {
  flex: 1;
  min-width: 0;
}

.session-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.session-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--foreground);
}

.session-time {
  font-size: 11px;
  color: var(--muted-foreground);
}

.session-preview {
  font-size: 12px;
  color: var(--muted-foreground);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 4px;
}

.chat-pane {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.chat-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border);
}

.chat-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--foreground);
}

.msg-area {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px;
  background: var(--background);
}

.history-older {
  display: flex;
  justify-content: center;
  margin-bottom: 12px;
}

.msg-row {
  display: flex;
  margin-bottom: 18px;
}

.row-left {
  justify-content: flex-start;
}

.row-right {
  justify-content: flex-end;
}

.msg-wrap {
  max-width: 60%;
  display: flex;
  flex-direction: column;
}

.row-right .msg-wrap {
  align-items: flex-end;
}

.msg-time {
  font-size: 11px;
  color: var(--muted-foreground);
  text-align: center;
  margin-bottom: 6px;
}

.bubble {
  padding: 10px 14px;
  border-radius: 16px;
  font-size: 14px;
  line-height: 1.5;
  word-break: break-all;
  box-shadow: var(--shadow-xs);
}

.bubble-left {
  background: var(--card);
  color: var(--foreground);
  border: 1px solid var(--border);
  border-top-left-radius: 4px;
}

.bubble-right {
  background: var(--primary);
  color: var(--primary-foreground);
  border-top-right-radius: 4px;
}

.message-orders {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  max-width: 100%;
  margin-top: 4px;
}

.msg-image {
  max-width: 240px;
  border-radius: var(--radius-sm);
}

.image-caption { margin-top: 6px; }

.input-area {
  border-top: 1px solid var(--border);
  padding: 10px 14px;
}

.input-area.no-perm {
  color: var(--muted-foreground);
  text-align: center;
  padding: 20px;
}

.input-actions {
  margin-bottom: 6px;
}

.input-footer {
  display: flex;
  justify-content: flex-end;
  margin-top: 8px;
}

@media (max-width: 900px) {
  .session-pane {
    width: 220px;
  }
}

@media (max-width: 720px) {
  .chat-workbench {
    flex-direction: column;
    height: auto;
  }
  .session-pane {
    width: 100%;
    max-height: 320px;
    border-right: none;
    border-bottom: 1px solid var(--border);
  }
  .chat-pane {
    flex: none;
    height: min(70vh, 560px);
    min-height: 420px;
  }
}
</style>
