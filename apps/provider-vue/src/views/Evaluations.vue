<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { api } from '../lib/session'
import { coalesced, onRealtime } from '../lib/realtime'

type Evaluation = { id: number; order: number; order_no: string; score: number; content: string; customer_name: string; service_name: string; reply_content: string; reply_author: 'PROVIDER' | 'STAFF' | ''; reply_version: number; created_at: string }
const route = useRoute(), router = useRouter()
const items = ref<Evaluation[]>([]), loading = ref(true), error = ref(''), feedback = ref('')
const selected = ref<Evaluation | null>(null), draft = ref(''), submitting = ref(false), readingLatest = ref(false), conflict = ref(false), uncertain = ref(false)
const savedDrafts = new Map<number, string>()
let version = 0
const pending = computed(() => items.value.filter(item => !item.reply_content).length)
const focus = computed(() => { const value = String(route.query.order || ''); const id = Number(value); return /^[1-9]\d*$/.test(value) && Number.isSafeInteger(id) ? id : null })
const shown = computed(() => focus.value ? items.value.filter(item => item.order === focus.value) : items.value)
const open = computed({ get: () => selected.value !== null, set: value => { if (!value && !submitting.value && !readingLatest.value) close() } })
const unchanged = computed(() => !!selected.value && draft.value.trim() === (selected.value.reply_content || '').trim())
async function load() {
  const current = ++version; error.value = ''; loading.value = true
  try { const data = await api<{ list: Evaluation[] }>('/orders/evaluations/mine/'); if (current === version) items.value = data.list || [] }
  catch (cause) { if (current === version) error.value = cause instanceof Error ? cause.message : '评价加载失败' }
  finally { if (current === version) loading.value = false }
}
function start(item: Evaluation) { selected.value = item; draft.value = savedDrafts.get(item.id) ?? item.reply_content ?? ''; conflict.value = false; uncertain.value = false; feedback.value = '' }
function close() { if (selected.value && (conflict.value || uncertain.value || savedDrafts.has(selected.value.id))) savedDrafts.set(selected.value.id, draft.value); selected.value = null; if (conflict.value || uncertain.value) void load(); conflict.value = false; uncertain.value = false }
function discardDraft() { if (!selected.value) return; savedDrafts.delete(selected.value.id); draft.value = selected.value.reply_content || ''; feedback.value = '' }
async function readLatest() {
  const item = selected.value
  if (!item || submitting.value || readingLatest.value) return
  readingLatest.value = true
  try {
    const data = await api<{list:Evaluation[]}>('/orders/evaluations/mine/')
    const latest = data.list.find(row => row.id === item.id)
    if (!latest) { feedback.value = '未找到此评价，草稿仍保留，请联系客服核对。'; return }
    version++; loading.value = false; items.value = data.list; selected.value = latest
    conflict.value = false; uncertain.value = false
    feedback.value = draft.value.trim() === (latest.reply_content || '').trim() ? '当前回复与草稿一致，无需重复发布。' : '已读取最新回复，请核对后决定是否发布草稿。'
  } catch { feedback.value = '最新回复暂时无法读取，草稿仍保留，请重试。' }
  finally { readingLatest.value = false }
}
async function submit() {
  const item = selected.value, content = draft.value.trim()
  if (!item || !content || submitting.value || readingLatest.value || conflict.value || uncertain.value || unchanged.value) return
  submitting.value = true
  try {
    const updated = await api<Evaluation>(`/orders/evaluations/${item.id}/reply/`, { method: 'POST', data: { reply_content: content, reply_version: item.reply_version } })
    savedDrafts.delete(item.id); version++; loading.value = false; items.value = items.value.map(row => row.id === item.id ? updated : row); selected.value = null; feedback.value = '回复已发布'
  } catch (cause) {
    const failure = cause as { errorCode?: string; status?: number; message?: string }
    if (failure.errorCode === 'EVALUATION_REPLY_CONFLICT') { savedDrafts.set(item.id, draft.value); feedback.value = ''; conflict.value = true }
    else if (failure.status && failure.status < 500) feedback.value = failure.message || '回复失败'
    else { savedDrafts.set(item.id, draft.value); uncertain.value = true; feedback.value = '回复结果未确认，请关闭后刷新核对' }
  } finally { submitting.value = false }
}
const refresh = coalesced(load)
const offOrder = onRealtime('order_status_update', data => {
  if (!document.hidden && data && typeof data === 'object' && Object.prototype.hasOwnProperty.call(data, 'provider') && (data as { status?: string }).status === 'COMPLETED') refresh.request()
})
const offConnected = onRealtime('connected', () => { if (!document.hidden) refresh.request() })
function onVisible() { if (document.visibilityState === 'visible') refresh.request() }
onMounted(() => { void load(); document.addEventListener('visibilitychange', onVisible) })
onUnmounted(() => { version++; offOrder(); offConnected(); refresh.dispose(); document.removeEventListener('visibilitychange', onVisible) })
</script>

<template>
  <main class="page stack">
    <header class="row"><div><p class="muted">兴安电竞 · 陪玩端</p><h1>评价管理</h1></div><Button variant="outline" @click="router.push('/orders')">返回订单</Button></header>
    <div v-if="focus" class="row"><span class="muted">关联订单评价</span><Button variant="outline" @click="router.push('/evaluations')">查看全部评价</Button></div>
    <section class="summary"><div><strong>{{ items.length }}</strong><span>全部评价</span></div><div><strong>{{ pending }}</strong><span>待回复</span></div><Button variant="outline" :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新' }}</Button></section>
    <p v-if="feedback && !selected" role="status" class="notice">{{ feedback }}</p>
    <p v-if="error" role="alert" class="error">{{ error }} <Button variant="outline" @click="load">重新加载</Button></p>
    <p v-else-if="loading" class="muted">正在加载评价…</p>
    <Card v-else-if="!shown.length" class="panel"><strong>{{ focus ? '未找到关联评价' : '暂无评价' }}</strong><p class="muted">{{ focus ? '可查看全部评价或联系客服核对。' : '老板完成评价后会显示在这里' }}</p></Card>
    <Card v-for="item in shown" v-else :key="item.id" class="panel review">
      <div class="row"><div><strong>{{ item.customer_name || '老板' }}</strong><p class="muted">{{ item.service_name || item.order_no }}</p></div><strong class="score">★ {{ Number(item.score).toFixed(1) }}</strong></div>
      <p>{{ item.content || '老板未填写文字评价' }}</p><p class="muted">{{ new Date(item.created_at).toLocaleString() }}</p>
      <div v-if="item.reply_content" class="reply"><strong>{{ item.reply_author === 'PROVIDER' ? '我的回复' : item.reply_author === 'STAFF' ? '客服回复' : '回复（来源未记录）' }}</strong><p>{{ item.reply_content }}</p></div>
      <div><Button variant="outline" @click="start(item)">{{ item.reply_content ? '修改回复' : '回复评价' }}</Button></div>
    </Card>
    <Dialog v-model:open="open"><DialogContent :show-close-button="!submitting && !readingLatest"><DialogHeader><DialogTitle>回复 {{ selected?.customer_name || '老板' }} 的评价</DialogTitle><DialogDescription>回复发布后老板可见</DialogDescription></DialogHeader>
      <p v-if="selected?.reply_content && selected.reply_author !== 'PROVIDER'" class="muted">保存后将替换当前回复，并显示为你的回复。</p>
      <div v-if="selected?.reply_content" class="reply"><strong>当前{{ selected.reply_author === 'STAFF' ? '客服' : selected.reply_author === 'PROVIDER' ? '陪玩' : '' }}回复</strong><p>{{ selected.reply_content }}</p></div>
      <Textarea v-model="draft" maxlength="1000" placeholder="感谢老板的认可，也可以说明本次服务情况…" :disabled="submitting || readingLatest" />
      <div class="row"><p v-if="feedback" class="error" role="alert">{{ feedback }}</p><p v-else-if="conflict" class="error" role="alert">回复已变化，草稿仍在此处。请读取最新回复并核对。</p><span v-else class="muted">{{ draft.length }}/1000</span></div>
      <Button v-if="conflict || uncertain" variant="outline" :disabled="readingLatest" @click="readLatest">{{ readingLatest ? '读取中…' : '读取最新回复' }}</Button>
      <DialogFooter><Button v-if="selected && savedDrafts.has(selected.id) && !conflict && !uncertain" variant="outline" :disabled="readingLatest" @click="discardDraft">放弃草稿</Button><Button variant="outline" :disabled="submitting || readingLatest" @click="close">取消</Button><Button :disabled="submitting || readingLatest || conflict || uncertain || unchanged || !draft.trim()" @click="submit">{{ submitting ? '发布中…' : '发布回复' }}</Button></DialogFooter>
    </DialogContent></Dialog>
  </main>
</template>
<style scoped>
h1 { margin: 4px 0 0; font-size: 24px; } p { margin: 0; overflow-wrap: anywhere; } .summary { display: flex; align-items: center; gap: 24px; padding: 16px; background: var(--card); border: 1px solid var(--border); border-radius: 12px; } .summary div { display: grid; gap: 4px; } .summary span { color: var(--muted-foreground); font-size: 12px; } .summary button { margin-left: auto; } .review { gap: 12px; } .score { color: #1e3a8a; } .reply { padding: 12px; background: var(--secondary); border-radius: 8px; } .reply p { margin-top: 4px; } .notice { color: #1e3a8a; }
</style>
