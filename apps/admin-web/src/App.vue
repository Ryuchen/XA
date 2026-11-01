<template>
  <el-config-provider size="small">
    <router-view />
  </el-config-provider>
</template>

<script setup lang="ts">
import { watch, onMounted, onUnmounted } from 'vue'
import { ElNotification } from 'element-plus'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useStaffDispatchQueue } from '@/stores/staffDispatchQueue'
import { adminWs } from '@/utils/ws'
import { request, type PageResult } from '@/api/request'
import { createStaffDispatchNotice } from '@/utils/staffDispatchNotice'
import { staffNoticeSound } from '@/utils/staffNoticeSound'

const auth = useAuthStore()
const staffQueue = useStaffDispatchQueue()
const router = useRouter()
const canNotifyStaffDispatch = () => Boolean(auth.token) && auth.hasPerm('order:view') && auth.hasPerm('order:dispatch')
const hasActiveOverlay = () => Array.from(document.querySelectorAll<HTMLElement>('.el-overlay'))
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
    const notification = ElNotification({
      title: '客服待派订单',
      message: '有订单需要客服指派，点击查看待派列表。',
      type: 'warning',
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
const offConnected = adminWs.on('connected', () => { void staffDispatchNotice.reconcile() })
watch(() => auth.token, () => adminWs.connect(), { immediate: true })
watch([() => auth.sessionRevision, () => auth.hasPerm('order:view'), () => auth.hasPerm('order:dispatch')], () => {
  staffDispatchNotice.reset()
  void staffDispatchNotice.reconcile()
}, { flush: 'sync', immediate: true })
let overlayObserver: MutationObserver | undefined
onMounted(() => {
  staffNoticeSound.prepare()
  let blocked = hasActiveOverlay()
  overlayObserver = new MutationObserver(() => {
    const current = hasActiveOverlay()
    if (current !== blocked) { blocked = current; staffDispatchNotice.modalChanged() }
  })
  overlayObserver.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class', 'aria-hidden'] })
  staffDispatchNotice.modalChanged()
})
onUnmounted(() => { overlayObserver?.disconnect(); offOrderNotice(); offConnected(); staffDispatchNotice.reset(); staffNoticeSound.dispose(); adminWs.disconnect() })
</script>
