<template>
  <Dialog :open="modelValue" @update:open="value => { if (!sending && !confirming) emit('update:modelValue', value) }"><DialogContent class="dispatch-dialog" :show-close-button="!sending && !confirming" @interact-outside="event => event.preventDefault()" @escape-key-down="event => { if (sending || confirming) event.preventDefault() }">
    <DialogHeader><DialogTitle>一键发单</DialogTitle><DialogDescription>核对文案和发送渠道。</DialogDescription></DialogHeader>
    <div class="dispatch-body" :aria-busy="loading">
      <p v-if="loading" role="status" class="dispatch-tip">正在加载…</p><p v-if="notice" role="status" class="dispatch-tip">{{ notice }}</p>
      <Alert v-if="loadError" variant="destructive"><AlertDescription>{{ loadError }}
        <Button variant="link" :disabled="loading || sending" @click="load(!preview, true)">重新加载</Button>
      </AlertDescription></Alert>
      <template v-if="preview">
        <Alert v-if="!preview.can_send"><AlertDescription>{{ preview.reason }}</AlertDescription></Alert>
        <h3 class="dispatch-heading">核对发单文案 <Popover><PopoverTrigger as-child><Button variant="link" aria-label="查看发单文案说明">说明</Button></PopoverTrigger><PopoverContent class="dispatch-popover">单钱为折后扣除抽成的实得；文案不包含原价、老板实付和内部备注。</PopoverContent></Popover></h3>
        <div class="dispatch-content-tabs"><Button :variant="contentChannel === 'COMMON' ? 'default' : 'outline'" :aria-pressed="contentChannel === 'COMMON'" :disabled="sending || confirming" @click="contentChannel = 'COMMON'">通用复制文案</Button><Button :variant="contentChannel === 'KOOK' ? 'default' : 'outline'" :aria-pressed="contentChannel === 'KOOK'" :disabled="sending || confirming" @click="contentChannel = 'KOOK'">KOOK 频道文案</Button></div>
        <p v-if="contentChannel === 'KOOK' && kookChannel" class="dispatch-tip">{{ contentDescription }}</p>
        <Textarea :model-value="displayContent" :rows="9" readonly aria-label="派单文案" />
        <div class="dispatch-channel-heading"><h3 class="dispatch-heading">选择发送渠道 <Popover><PopoverTrigger as-child><Button variant="link" aria-label="查看渠道状态说明">说明</Button></PopoverTrigger><PopoverContent class="dispatch-popover">已发送、发送中及结果待核对的渠道不能重复发送。</PopoverContent></Popover></h3><span>已选 {{ selected.length }} 个</span></div>
        <div class="dispatch-channels">
          <div v-for="channel in preview.channels" :key="channel.channel" class="dispatch-channel">
            <label class="dispatch-check"><Checkbox :model-value="selected.includes(channel.channel)" :disabled="sending || confirming || !!loadError || !selectable(channel)" @update:model-value="value => setChannel(channel.channel, value === true)" />{{ channel.label }}</label>
            <Badge variant="outline">
              {{ channelStatus(channel) }}
            </Badge>
            <Badge v-if="!channel.configured" variant="outline">当前未配置</Badge>
            <span v-if="channel.channel === 'KOOK' && channel.target_id" class="dispatch-tip">{{ channel.target_source === 'recorded' ? '记录目标' : (channel.route_name || '当前目标') }} · {{ channel.target_id }}</span>
            <span v-if="channel.channel === 'KOOK' && channel.previous_target_id" class="dispatch-tip">上次尝试目标 · {{ channel.previous_target_id }}</span>
            <p v-if="channel.channel === 'KOOK' && validCardDetails(channel)" class="dispatch-card-info">{{ channel.target_source === 'recorded' ? '记录卡片' : '待发卡片' }}：{{ cardSummary(channel) }}</p>
            <p v-else-if="channel.channel === 'KOOK' && (channel.target_source === 'recorded' || channel.configured)" class="dispatch-card-info">{{ channel.target_source === 'recorded' ? '历史卡片明细未记录或无法识别' : '卡片明细无法读取，暂不可发单，请刷新或检查配置' }}</p>
            <p v-if="channel.last_error" class="dispatch-error">{{ channel.last_error }}</p>
          </div>
        </div>
        <p v-if="copyMayDuplicate" class="dispatch-tip">已有渠道正在处理、结果待核对或已发送，人工发送前请核对群消息。</p>
        <p v-else-if="preview.channels.every(channel => !channel.configured)" class="dispatch-tip">渠道未配置，可复制文案人工发送。</p>
      </template>
    </div>
    <DialogFooter>
      <Button variant="outline" :disabled="sending || confirming" @click="emit('update:modelValue', false)">关闭</Button>
      <Button variant="outline" :disabled="loading || sending || confirming" @click="load(!preview, true)">刷新状态</Button>
      <Button variant="outline" :disabled="!preview?.can_send || !displayContent || loading || sending || confirming || !!loadError" @click="copyText">复制文案</Button>
      <Button :disabled="!preview?.can_send || !selected.length || loading || sending || confirming || !!loadError" @click="confirmSend">{{ sending ? '提交中…' : '提交发单' }}</Button>
    </DialogFooter>
  </DialogContent></Dialog>
  <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Checkbox } from '@xa/shadcn-vue/checkbox'
import { Badge } from '@xa/shadcn-vue/badge'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { Popover, PopoverContent, PopoverTrigger } from '@xa/shadcn-vue/popover'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { request } from '@/api/request'
import { useAuthStore } from '@/stores/auth'

type CardDetails = { mention_role_ids: string[]; link_count: number }
type Channel = { channel: string; label: string; configured: boolean; status: string; last_error: string; confirmation_key: string; target_id?: string; route_name?: string; content?: string; target_source?: string; previous_target_id?: string; content_source?: string; card_details?: CardDetails | null }
type Preview = { order_id: number; order_no: string; content: string; can_send: boolean; reason: string; channels: Channel[] }
const props = defineProps<{ modelValue: boolean; orderId?: number }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const auth = useAuthStore()
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const preview = ref<Preview>()
const contentChannel = ref('COMMON')
const kookChannel = computed(() => preview.value?.channels.find(c => c.channel === 'KOOK'))
const displayContent = computed(() => contentChannel.value === 'KOOK' ? kookChannel.value?.content ?? '' : preview.value?.content || '')
const contentDescription = computed(() => ({ recorded: '显示该轮次记录的文案', unavailable: '该记录未保存文案，无法还原', retry: '显示按当前规则重试的文案', current: '显示当前规则的预览文案', fallback: '当前无匹配规则，显示通用文案' } as Record<string, string>)[kookChannel.value?.content_source || ''] || '')
const selected = ref<string[]>([])
const loading = ref(false)
const sending = ref(false)
const confirming = ref(false)
const loadError = ref('')
const notice = ref('')
function setChannel(channel:string, value:boolean) { selected.value = value ? [...new Set([...selected.value,channel])] : selected.value.filter(item => item !== channel) }
let poll: ReturnType<typeof setInterval> | undefined
let sequence = 0
let disposed = false
const statusLabels: Record<string, string> = {
  NOT_SENT: '未发送', PENDING: '已入队', SENDING: '发送中，勿重复发送', SENT: '已发送',
  FAILED: '发送失败', UNKNOWN: '结果待核对', SKIPPED: '已跳过',
}
const copyMayDuplicate = computed(() => !!preview.value?.channels.some(channel => ['PENDING', 'SENDING', 'SENT', 'UNKNOWN'].includes(channel.status)))
const validCardDetails = (channel: Channel) => !!channel.card_details && Array.isArray(channel.card_details.mention_role_ids)
  && channel.card_details.mention_role_ids.every(id => typeof id === 'string' && /^[0-9]{1,64}$/.test(id))
  && Number.isSafeInteger(channel.card_details.link_count) && channel.card_details.link_count >= 0
const cardSummary = (channel: Channel) => {
  if (!validCardDetails(channel)) return '卡片明细不可用'
  const details = channel.card_details!
  return `${details.mention_role_ids.length ? `提醒身份组 ${details.mention_role_ids.join('、')}` : '不提醒身份组'}；${details.link_count ? `附带 ${details.link_count} 个链接` : '无链接'}`
}
const selectable = (channel: Channel) => channel.configured && ['NOT_SENT', 'FAILED'].includes(channel.status) && (channel.channel !== 'KOOK' || validCardDetails(channel))
const channelStatus = (channel: Channel) => channel.status === 'FAILED' && selectable(channel) ? '发送失败，可重试' : statusLabels[channel.status] || channel.status

async function load(initial: boolean, foreground = initial): Promise<boolean> {
  if (!props.orderId || !props.modelValue || sending.value || !auth.hasPerm('order:dispatch')) return false
  const orderId = props.orderId
  const revision = auth.sessionRevision, requestSequence = ++sequence
  if (foreground) loading.value = true
  try {
    const result = await request<Preview>({ url: `/orders/${orderId}/dispatch-message/`, silent: true })
    if (disposed || requestSequence !== sequence || revision !== auth.sessionRevision || !props.modelValue || orderId !== props.orderId || !auth.hasPerm('order:dispatch')) return false
    preview.value = result.data
    loadError.value = ''
    if (initial) contentChannel.value = result.data.channels.some(c => c.channel === 'KOOK' && (c.configured || c.status !== 'NOT_SENT')) ? 'KOOK' : 'COMMON'
    const available = result.data.channels.filter(selectable).map(channel => channel.channel)
    selected.value = initial ? [] : selected.value.filter(channel => available.includes(channel))
    return true
  } catch {
    if (!disposed && requestSequence === sequence && revision === auth.sessionRevision && props.modelValue && orderId === props.orderId) {
      loadError.value = preview.value ? '渠道状态刷新失败，请重新加载后操作' : '发单预览加载失败，请重新加载'
    }
    return false
  } finally {
    if (requestSequence === sequence) loading.value = false
  }
}

async function copyText() {
  if (!await load(false, true) || !preview.value?.can_send || !displayContent.value) return
  const orderId = props.orderId, session = auth.sessionRevision
  if (copyMayDuplicate.value) {
    if (!await askConfirm({title:'确认复制文案',description:'已有渠道正在处理、结果待核对或已发送。人工发送可能重复，请先核对群消息。',confirmText:'仍然复制',cancelText:'取消'})) return
    if (disposed || session !== auth.sessionRevision || orderId !== props.orderId || !props.modelValue || !auth.hasPerm('order:dispatch') || !preview.value?.can_send || !displayContent.value || loadError.value) return
  }
  try {
    await navigator.clipboard.writeText(displayContent.value)
    notice.value = '派单信息已复制'
  } catch {
    notice.value = '浏览器限制了复制，请在文案框内全选复制'
  }
}

async function confirmSend() {
  if (sending.value || confirming.value || loading.value || loadError.value || !props.orderId || !props.modelValue || !auth.hasPerm('order:dispatch') || !preview.value?.can_send || !selected.value.length) return
  const orderId = props.orderId
  const revision = auth.sessionRevision, channels = [...selected.value]
  const chosen = preview.value.channels.filter(channel => channels.includes(channel.channel))
  if (chosen.length !== channels.length || chosen.some(channel => !selectable(channel) || !channel.confirmation_key)) { loadError.value = '发单预览不完整，请刷新后重新确认'; return }
  const confirmationKeys = Object.fromEntries(chosen.map(channel => [channel.channel, channel.confirmation_key]))
  const targets = chosen.map(channel => channel.channel === 'KOOK' && channel.target_id ? `${channel.label}（${channel.target_id}）` : channel.label).join('、')
  const selectedKook = chosen.find(channel => channel.channel === 'KOOK')
  const cardInfo = selectedKook ? ` KOOK 卡片：${cardSummary(selectedKook)}。` : ''
  confirming.value = true
  const accepted = await askConfirm({title:'确认提交发单',description:`订单 ${preview.value.order_no} 将提交至 ${targets}。${cardInfo} 请确认文案及渠道。`,confirmText:'确认提交',cancelText:'取消'})
  confirming.value = false
  if (!accepted) return
  if (disposed || revision !== auth.sessionRevision || !props.modelValue || props.orderId !== orderId || !auth.hasPerm('order:dispatch')) return
  const requestSequence = ++sequence
  sending.value = true
  try {
    const result = await request<Preview>({
      url: `/orders/${orderId}/dispatch-message/`, method: 'post', data: { channels, confirmation_keys: confirmationKeys }, silent: true,
    })
    if (disposed || requestSequence !== sequence || revision !== auth.sessionRevision || !props.modelValue || props.orderId !== orderId || !auth.hasPerm('order:dispatch')) return
    preview.value = result.data
    selected.value = []
    if (result.data.channels.some(channel => ['FAILED', 'UNKNOWN'].includes(channel.status))) {
      notice.value = '部分渠道未确认发送成功，请查看渠道状态'
    } else {
      notice.value = '已处理发单请求，请查看渠道状态'
    }
  } catch (error: any) {
    if (!disposed && requestSequence === sequence && revision === auth.sessionRevision && props.modelValue && props.orderId === orderId) {
      selected.value = []
      const status = error?.response?.status
      loadError.value = status === 409 ? (error?.response?.data?.msg || '订单或渠道已变化，请刷新并重新确认。')
        : status && status >= 400 && status < 500 ? (error?.response?.data?.msg || '发单未提交，请刷新并重新确认。')
        : '发单结果尚未确认；请求可能仍在处理，刷新仅显示当前状态，请先核对。'
    }
  } finally {
    if (requestSequence === sequence) sending.value = false
  }
}

watch([() => props.modelValue, () => props.orderId, () => auth.sessionRevision, () => auth.hasPerm('order:dispatch')], ([visible, , , allowed]) => {
  ++sequence
  if (poll) clearInterval(poll)
  preview.value = undefined
  selected.value = []
  loading.value = false
  sending.value = false
  confirming.value = false
  loadError.value = ''
  notice.value = ''
  confirmNo()
  if (visible && !allowed) emit('update:modelValue', false)
  if (visible && props.orderId && allowed) {
    void load(true)
    poll = setInterval(() => {
      if (!loading.value && !sending.value && !confirming.value && preview.value?.channels.some(channel => ['PENDING', 'SENDING'].includes(channel.status))) {
        void load(false)
      }
    }, 5000)
  }
}, { immediate: true })
onUnmounted(() => { disposed = true; ++sequence; if (poll) clearInterval(poll) })
</script>

<style scoped>
.dispatch-dialog { max-width:min(760px, calc(100vw - 24px)); }
.dispatch-body { max-height:65vh; overflow:auto; }
.dispatch-tip { color: var(--muted-foreground); font-size: 13px; line-height: 1.6; }
.dispatch-popover { width:280px; background:var(--card); color:var(--foreground); font-size:13px; }
.dispatch-check { display:inline-flex; align-items:center; gap:8px; }
.dispatch-heading { margin: 20px 0 0; font-size: 14px; color: var(--foreground); }
.dispatch-help { display: inline-flex; align-items: center; padding: 0; border: 0; background: transparent; color: var(--muted-foreground); cursor: help; vertical-align: middle; }
.dispatch-help:focus-visible { outline: 2px solid var(--xa-color-primary); outline-offset: 3px; border-radius: 3px; }
.dispatch-content-tabs { display: flex; flex-direction: row; flex-wrap: wrap; gap:8px; margin: 18px 0 12px; }
.dispatch-channel-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-top: 22px; padding-top: 18px; border-top: 1px solid var(--border); }
.dispatch-channel-heading .dispatch-heading { margin: 0; }
.dispatch-channel-heading span { color: var(--muted-foreground); font-size: 12px; }
.dispatch-channels { display: flex; flex-direction: column; gap: 12px; margin-top: 12px; }
.dispatch-channel { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.dispatch-error { width: 100%; margin: 0; color: var(--destructive); font-size: 13px; }
.dispatch-card-info { width: 100%; margin: 0; color: var(--muted-foreground); font-size: 12px; line-height: 1.5; overflow-wrap: anywhere; }
</style>
