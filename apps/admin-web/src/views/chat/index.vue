<template>
  <div class="chat-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">统一处理用户咨询会话，实时收发消息与图片</p>
    </div>
    <div class="chat-workbench">
    <!-- 左侧会话列表 -->
    <div class="session-pane">
      <div class="session-toolbar">
        <el-input
          v-model="keyword"
          placeholder="搜索用户账号/昵称"
          clearable
          size="small"
          @keyup.enter="loadSessions"
          @clear="loadSessions"
        />
        <el-button size="small" :type="onlyUnread ? 'primary' : 'default'" @click="toggleUnread">
          未读
        </el-button>
      </div>
      <el-scrollbar class="session-list">
        <div
          v-for="s in sessions"
          :key="s.id"
          class="session-item"
          :class="{ active: current?.id === s.id }"
          @click="openSession(s)"
        >
          <el-badge :value="s.unread_support" :hidden="!s.unread_support" class="session-avatar">
            <el-avatar :size="40" :src="s.user_avatar">{{ (s.user_name || '?').charAt(0) }}</el-avatar>
          </el-badge>
          <div class="session-meta">
            <div class="session-top">
              <span class="session-name">{{ s.user_name }}</span>
              <span class="session-time">{{ shortTime(s.last_message_at) }}</span>
            </div>
            <div class="session-preview">{{ s.last_message || '暂无消息' }}</div>
          </div>
        </div>
        <el-empty v-if="!sessions.length" description="暂无会话" :image-size="60" />
      </el-scrollbar>
    </div>

    <!-- 右侧对话窗口 -->
    <div class="chat-pane">
      <template v-if="current">
        <div class="chat-header">
          <span class="chat-title">{{ current.user_name }}</span>
          <el-tag size="small" type="info">{{ current.user_role_display }}</el-tag>
        </div>
        <el-scrollbar ref="msgScrollRef" class="msg-area">
          <div
            v-for="m in messages"
            :key="m.id"
            class="msg-row"
            :class="m.is_from_support ? 'row-right' : 'row-left'"
          >
            <div class="msg-wrap">
              <div class="msg-time">{{ formatDateTime(m.created_at) }}</div>
              <el-image
                v-if="m.content_type === 'IMAGE'"
                :src="m.image_url"
                :preview-src-list="[m.image_url]"
                fit="cover"
                class="msg-image"
              />
              <div v-else class="bubble" :class="m.is_from_support ? 'bubble-right' : 'bubble-left'">
                {{ m.content }}
              </div>
            </div>
          </div>
          <el-empty v-if="!messages.length" description="开始对话吧" :image-size="60" />
        </el-scrollbar>

        <div class="input-area" v-if="auth.hasPerm('chat:reply')">
          <div class="input-actions">
            <el-upload
              :show-file-list="false"
              :before-upload="onPickImage"
              accept="image/*"
            >
              <el-button text :icon="Picture">图片</el-button>
            </el-upload>
          </div>
          <el-input
            v-model="input"
            type="textarea"
            :rows="3"
            resize="none"
            placeholder="输入回复内容，Enter 发送 / Shift+Enter 换行"
            @keydown.enter.exact.prevent="onSendText"
          />
          <div class="input-footer">
            <el-button type="primary" :loading="sending" @click="onSendText">发送</el-button>
          </div>
        </div>
        <div v-else class="input-area no-perm">无回复权限</div>
      </template>
      <el-empty v-else description="选择左侧会话开始" />
    </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Picture } from '@element-plus/icons-vue'
import { chatApi } from '@/api/modules'
import { formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { adminWs } from '@/utils/ws'

const auth = useAuthStore()
const keyword = ref('')
const onlyUnread = ref(false)
const sessions = ref<any[]>([])
const current = ref<any>(null)
const messages = ref<any[]>([])
const input = ref('')
const sending = ref(false)
const msgScrollRef = ref()

function shortTime(value?: string | null): string {
  if (!value) return ''
  const d = new Date(value)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function loadSessions() {
  const params: Record<string, any> = {}
  if (keyword.value) params.keyword = keyword.value
  if (onlyUnread.value) params.only_unread = 1
  const res = await chatApi.list(params)
  sessions.value = res.data.list || []
}

function toggleUnread() {
  onlyUnread.value = !onlyUnread.value
  loadSessions()
}

async function scrollToBottom() {
  await nextTick()
  const wrap = msgScrollRef.value?.wrapRef
  if (wrap) wrap.scrollTop = wrap.scrollHeight
}

async function openSession(s: any) {
  current.value = s
  const res = await chatApi.messages(s.id)
  messages.value = res.data.list || []
  scrollToBottom()
  if (s.unread_support) {
    await chatApi.read(s.id)
    s.unread_support = 0
  }
}

async function onSendText() {
  const content = input.value.trim()
  if (!content || sending.value || !current.value) return
  sending.value = true
  try {
    const fd = new FormData()
    fd.append('content', content)
    const res: any = await chatApi.reply(current.value.id, fd)
    if (res.code === 0 && res.data) {
      input.value = ''
      appendMessage(res.data)
    }
  } catch {
    /* 拦截器已提示 */
  } finally {
    sending.value = false
  }
}

async function onPickImage(file: File) {
  if (!current.value) return false
  sending.value = true
  try {
    const fd = new FormData()
    fd.append('image', file)
    const res: any = await chatApi.reply(current.value.id, fd)
    if (res.code === 0 && res.data) {
      appendMessage(res.data)
    }
  } catch {
    /* 拦截器已提示 */
  } finally {
    sending.value = false
  }
  return false
}

function appendMessage(msg: any) {
  if (!messages.value.some((m) => m.id === msg.id)) {
    messages.value.push(msg)
    scrollToBottom()
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
  if (!fromSupport && current.value?.id !== sessionId) {
    s.unread_support = (s.unread_support || 0) + 1
  }
  sessions.value.splice(idx, 1)
  sessions.value.unshift(s)
}

let offMessage: (() => void) | null = null

onMounted(() => {
  loadSessions()
  adminWs.connect()
  offMessage = adminWs.on('chat_message', (data: any) => {
    if (!data?.message) return
    const preview = data.message.content_type === 'IMAGE' ? '[图片]' : data.message.content
    bumpSession(data.session_id, preview, data.message.is_from_support)
    if (current.value?.id === data.session_id) {
      appendMessage(data.message)
      if (!data.message.is_from_support) {
        chatApi.read(data.session_id)
      }
    }
  })
})

onUnmounted(() => {
  offMessage?.()
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
}

.session-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px;
  cursor: pointer;
  border-bottom: 1px solid var(--border);
  transition: background-color 0.18s ease;
}

.session-item:hover,
.session-item.active {
  background: var(--secondary);
}

.session-avatar {
  flex-shrink: 0;
}

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
  padding: 18px;
  background: var(--background);
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

.msg-image {
  max-width: 240px;
  border-radius: var(--radius-sm);
}

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
    min-height: 480px;
  }
}
</style>
