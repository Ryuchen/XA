<template>
  <el-dialog :model-value="modelValue" title="一键发单" width="760px" @update:model-value="emit('update:modelValue', $event)" class="ap-modal">
    <div v-loading="loading">
      <template v-if="preview">
        <el-alert v-if="!preview.can_send" :title="preview.reason" type="warning" :closable="false" />
        <p class="dispatch-tip">单钱按活动成交价扣除抽成后的实际收益展示。原价、老板实付和内部备注不会进入发单文案。</p>
        <el-radio-group v-model="contentChannel" class="dispatch-content-tabs">
          <el-radio-button value="COMMON">通用复制文案</el-radio-button>
          <el-radio-button value="KOOK">KOOK 频道文案</el-radio-button>
        </el-radio-group>
        <el-input :model-value="displayContent" type="textarea" :rows="9" readonly aria-label="派单文案" />
        <el-checkbox-group v-model="selected" class="dispatch-channels">
          <div v-for="channel in preview.channels" :key="channel.channel" class="dispatch-channel">
            <el-checkbox :value="channel.channel" :disabled="!selectable(channel)">{{ channel.label }}</el-checkbox>
            <el-tag size="small" :type="channel.status === 'SENT' ? 'success' : channel.status === 'FAILED' || channel.status === 'UNKNOWN' ? 'warning' : 'info'">
              {{ channel.configured ? statusLabels[channel.status] || channel.status : '未配置' }}
            </el-tag>
            <span v-if="channel.channel === 'KOOK' && channel.target_id" class="dispatch-tip">{{ channel.route_name }} · {{ channel.target_id }}</span>
            <p v-if="channel.last_error" class="dispatch-error">{{ channel.last_error }}</p>
          </div>
        </el-checkbox-group>
        <p class="dispatch-tip">可同时发送到已配置的频道和群。已发送的渠道不会重复发送；未配置时可复制文案人工发群。</p>
      </template>
    </div>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">关闭</el-button>
      <el-button :disabled="!preview || loading" @click="load(false)">刷新状态</el-button>
      <el-button :disabled="!preview?.can_send || loading" @click="copyText">复制派单信息</el-button>
      <el-button type="primary" :loading="sending" :disabled="!preview?.can_send || !selected.length || loading" @click="send">一键发送</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { request } from '@/api/request'

type Channel = { channel: string; label: string; configured: boolean; status: string; last_error: string; target_id?: string; route_name?: string; content?: string }
type Preview = { order_id: number; content: string; can_send: boolean; reason: string; channels: Channel[] }
const props = defineProps<{ modelValue: boolean; orderId?: number }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const preview = ref<Preview>()
const contentChannel = ref('COMMON')
const displayContent = computed(() => contentChannel.value === 'KOOK' ? preview.value?.channels.find(c => c.channel === 'KOOK')?.content || preview.value?.content || '' : preview.value?.content || '')
const selected = ref<string[]>([])
const loading = ref(false)
const sending = ref(false)
let poll: ReturnType<typeof setInterval> | undefined
const statusLabels: Record<string, string> = {
  NOT_SENT: '未发送', PENDING: '已入队', SENDING: '发送中，勿重复发送', SENT: '已发送',
  FAILED: '发送失败，可重试', UNKNOWN: '结果待核对', SKIPPED: '已跳过',
}
const selectable = (channel: Channel) => channel.configured && ['NOT_SENT', 'FAILED'].includes(channel.status)

async function load(initial: boolean) {
  if (!props.orderId) return
  const orderId = props.orderId
  if (initial) loading.value = true
  try {
    const result = await request<Preview>({ url: `/orders/${orderId}/dispatch-message/` })
    if (!props.modelValue || orderId !== props.orderId) return
    preview.value = result.data
    if (initial) contentChannel.value = result.data.channels.some(c => c.channel === 'KOOK' && c.configured) ? 'KOOK' : 'COMMON'
    const available = result.data.channels.filter(selectable).map(channel => channel.channel)
    selected.value = initial ? available : selected.value.filter(channel => available.includes(channel))
  } finally {
    if (orderId === props.orderId) loading.value = false
  }
}

async function copyText() {
  await load(false)
  if (!preview.value?.can_send) return
  try {
    await navigator.clipboard.writeText(displayContent.value)
    ElMessage.success('派单信息已复制')
  } catch {
    ElMessage.warning('浏览器限制了复制，请在文案框内全选复制')
  }
}

async function send() {
  if (sending.value || !props.orderId || !selected.value.length) return
  const orderId = props.orderId
  sending.value = true
  try {
    const result = await request<Preview>({
      url: `/orders/${orderId}/dispatch-message/`, method: 'post', data: { channels: selected.value },
    })
    if (!props.modelValue || props.orderId !== orderId) return
    preview.value = result.data
    selected.value = []
    if (result.data.channels.some(channel => ['FAILED', 'UNKNOWN'].includes(channel.status))) {
      ElMessage.warning('部分渠道未确认发送成功，请查看渠道状态')
    } else {
      ElMessage.success('已处理发单请求，请查看渠道状态')
    }
  } finally {
    sending.value = false
  }
}

watch(() => [props.modelValue, props.orderId] as const, ([visible]) => {
  if (poll) clearInterval(poll)
  preview.value = undefined
  selected.value = []
  if (visible && props.orderId) {
    void load(true).catch(() => {})
    poll = setInterval(() => {
      if (preview.value?.channels.some(channel => ['PENDING', 'SENDING'].includes(channel.status))) {
        void load(false).catch(() => {})
      }
    }, 5000)
  }
}, { immediate: true })
onUnmounted(() => { if (poll) clearInterval(poll) })
</script>

<style scoped>
.dispatch-tip { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.dispatch-content-tabs { display: flex; flex-direction: row; flex-wrap: nowrap; margin: 18px 0 12px; }
.dispatch-channels { display: flex; flex-direction: column; gap: 12px; margin-top: 18px; }
.dispatch-channel { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.dispatch-error { width: 100%; margin: 0; color: var(--el-color-warning); font-size: 13px; }
</style>
