<template>
    <router-view />
    <NoticeStack />
</template>

<script setup lang="ts">
import { watch, onMounted, onUnmounted } from 'vue'
import NoticeStack from '@/components/NoticeStack.vue'
import { showInteractiveNotice } from '@/utils/notice'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useStaffDispatchQueue } from '@/stores/staffDispatchQueue'
import { adminWs } from '@/utils/ws'
import { request, type PageResult } from '@/api/request'
import { createStaffDispatchNotice } from '@/utils/staffDispatchNotice'
import { chatNoticeSound, staffNoticeSound } from '@/utils/staffNoticeSound'
import { attendedChatSession } from '@/utils/chatAttention'

const auth = useAuthStore()
const staffQueue = useStaffDispatchQueue()
const router = useRouter()
const canNotifyStaffDispatch = () => Boolean(auth.token) && auth.hasPerm('order:view') && auth.hasPerm('order:dispatch')
const hasActiveOverlay = () => Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"], [role="alertdialog"]'))
  .some(element => element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden')
const staffDispatchNotice = createStaffDispatchNotice({
  session: () => auth.sessionRevision,
  allowed: canNotifyStaffDispatch,
  blocked: hasActiveOverlay,
  retrieve: async id => (await request<any>({ url: `/orders/${id}/`, silent: true })).data,
  listPending: async () => (await request<PageResult<any>>({
    url: '/orders/', params: { actionable_staff: '1', page: 1, page_size: 1 }, silent: true,
  })).data,
  queue: (status, count) => staffQueue.update(status, count),
  show: (open, closed) => {
    staffNoticeSound.play()
    const notification = showInteractiveNotice({
      title: '客服待派订单',
      message: '有订单需要客服指派，点击查看待派列表。',
      duration: 8000,
      onClick: () => { notification.close(); open() },
      onClose: closed,
    })
    return notification
  },
  open: () => {
    if (canNotifyStaffDispatch()) void router.push({ name: 'orders', query: { queue: staffQueue.status === 'review' ? 'staff_review' : 'staff' } })
  },
})
const offOrderNotice = adminWs.on('order_status_update', data => { void staffDispatchNotice.receive(data) })
let chatNotice: { close: () => void } | null = null
let chatNoticeSessions = new Set<number>()
const offChatNotice = adminWs.on('chat_message', data => {
  if (!auth.token || !auth.hasPerm('chat:view') || !Number.isSafeInteger(data?.session_id) || data.session_id < 1 || !data?.message || data.message.is_from_support !== false
      || (router.currentRoute.value.path === '/chat' && !document.hidden && attendedChatSession.value === data.session_id)) return
  if (chatNotice) { chatNoticeSessions.add(data.session_id); return }
  const sessions = chatNoticeSessions = new Set([data.session_id])
  const revision = auth.sessionRevision
  chatNoticeSound.play()
  const notification = showInteractiveNotice({
    title: '新的客服咨询', message: '有未读咨询，点击查看。', duration: 8000,
    onClick: () => {
      if (chatNotice === notification) chatNotice = null
      notification.close()
      if (revision === auth.sessionRevision && auth.token && auth.hasPerm('chat:view')) {
        const query = sessions.size === 1 ? { session_id: String(sessions.values().next().value) } : { only_unread: '1' }
        void router.push({ path: '/chat', query })
      }
    },
    onClose: () => { if (chatNotice === notification) chatNotice = null },
  })
  chatNotice = notification
})
const offConnected = adminWs.on('connected', () => { void staffDispatchNotice.reconcile() })
watch(() => auth.token, () => adminWs.connect(), { immediate: true })
watch([() => auth.sessionRevision, () => auth.hasPerm('order:view'), () => auth.hasPerm('order:dispatch')], () => {
  staffDispatchNotice.reset()
  void staffDispatchNotice.reconcile()
}, { flush: 'sync', immediate: true })
watch([() => auth.sessionRevision, () => auth.hasPerm('chat:view')], () => { chatNotice?.close(); chatNotice = null; chatNoticeSessions.clear() }, { flush: 'sync' })
let overlayObserver: MutationObserver | undefined
onMounted(() => {
  staffNoticeSound.prepare()
  chatNoticeSound.prepare()
  let blocked = hasActiveOverlay()
  overlayObserver = new MutationObserver(() => {
    const current = hasActiveOverlay()
    if (current !== blocked) { blocked = current; staffDispatchNotice.modalChanged() }
  })
  overlayObserver.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class', 'aria-hidden'] })
  staffDispatchNotice.modalChanged()
})
onUnmounted(() => { overlayObserver?.disconnect(); offOrderNotice(); offChatNotice(); offConnected(); chatNotice?.close(); staffDispatchNotice.reset(); staffNoticeSound.dispose(); chatNoticeSound.dispose(); adminWs.disconnect() })
</script>
