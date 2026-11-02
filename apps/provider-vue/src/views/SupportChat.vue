<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import PrivateImage from '../components/PrivateImage.vue'
import { api, currentUser } from '../lib/session'
import { coalesced, onRealtime } from '../lib/realtime'

type Message = { id:number; content:string; content_type:'TEXT'|'IMAGE'; image_url:string; is_from_support:boolean; is_read:boolean; created_at:string; client_message_id:string | null }
type History = { messages:Message[]; has_more:boolean; next_before_id:number | null }
type Pending = { id:string; content:string; draft?:string; draftScope?:string; kind?:'text'|'image' }
const route = useRoute(), router = useRouter(), rawOrder = String(route.query.order_no || ''), orderNo = ref(/^ORD[0-9A-F]{20}$/i.test(rawOrder) ? rawOrder.toUpperCase() : '')
const owner = currentUser.value?.id || '', key = `xa:provider:chat-pending:${owner}`, draftKey = (scope:string) => `xa:provider:chat-draft:${owner}${scope ? ':' + scope : ''}`
function read<T>(key:string):T | null { try { return (JSON.parse(localStorage.getItem(key) || 'null') as {data:T} | null)?.data ?? null } catch { return null } }
function write(key:string, value:unknown) { localStorage.setItem(key, JSON.stringify({data:value})) }
const input = ref(read<string>(draftKey(orderNo.value)) || ''), pending = ref<Pending | null>(read<Pending>(key)), pendingStored = ref(true), imageFile = ref<File | null>(null)
const items = ref<Message[]>([]), before = ref<number | null>(null), loading = ref(true), busy = ref(false), error = ref(''), notice = ref(''), browsing = ref(false), confirmAbandon = ref(false), confirmSwitch = ref(false)
const history = ref<HTMLElement | null>(null)
let sequence = 0, alive = true, loadingRequest = false, refreshQueued = false
const merge = (a:Message[], b:Message[]) => [...new Map([...a,...b].map(row => [row.id,row])).values()].sort((x,y) => x.created_at.localeCompare(y.created_at) || x.id-y.id)
function saveDraft(value:string, scope = orderNo.value) { try { if (value) write(draftKey(scope),value); else localStorage.removeItem(draftKey(scope)) } catch { /* keep current draft */ } }
watch(input, value => saveDraft(value))
async function load(older = false) {
  if (older && !before.value) return
  if (loadingRequest) { if (!older) refreshQueued = true; return }
  loadingRequest = true; const current = ++sequence; loading.value = true; error.value = ''
  try {
    const previous = items.value, recent = new Set(previous.map(row => row.id)), oldHeight = history.value?.scrollHeight || 0
    let response = await api<History>(`/chat/session/${older ? `?before_id=${before.value}` : ''}`), first = response
    if (!alive || current !== sequence) return
    const fetched = [...response.messages]
    while (!older && recent.size && !fetched.some(row => recent.has(row.id)) && response.has_more && response.next_before_id) {
      response = await api<History>(`/chat/session/?before_id=${response.next_before_id}`)
      if (!alive || current !== sequence) return
      fetched.push(...response.messages)
    }
    items.value = merge(previous, fetched)
    if (older || !previous.length) before.value = (older ? first : response).next_before_id || null
    if (older) { browsing.value = true; await nextTick(); if (history.value) history.value.scrollTop = history.value.scrollHeight - oldHeight }
    else if (!browsing.value) {
      await nextTick(); if (history.value) history.value.scrollTop = history.value.scrollHeight
      const ids = items.value.filter(row => row.is_from_support && !row.is_read).map(row => row.id)
      for (let offset=0; offset<ids.length && alive && !document.hidden; offset+=200) await api('/chat/read/', {method:'POST',data:{message_ids:ids.slice(offset,offset+200)}})
      if (ids.length) items.value = items.value.map(row => ids.includes(row.id) ? {...row,is_read:true} : row)
    }
  } catch (cause) { if (alive && current === sequence) error.value = cause instanceof Error ? cause.message : '聊天记录加载失败' }
  finally { loadingRequest = false; if (alive && current === sequence) loading.value = false; if (refreshQueued && alive && !document.hidden) { refreshQueued = false; refresh.request() } }
}
async function finish(message:Message, item:Pending) {
  if (message.client_message_id !== item.id) return
  try { if (read<Pending>(key)?.id === item.id) localStorage.removeItem(key) } catch { /* already visible in history */ }
  pending.value = null; imageFile.value = null
  const sent = item.draft === undefined ? item.content : item.draft, scope = item.draftScope || ''
  if (sent && read<string>(draftKey(scope))?.trim() === sent) saveDraft('',scope)
  if (scope === orderNo.value && input.value.trim() === sent) input.value = ''
  browsing.value = false; await load()
}
async function send(existing?:Pending, file?:File) {
  if (busy.value || (pending.value && !existing)) return
  const draft = input.value.trim(), content = orderNo.value ? `订单号 ${orderNo.value}${draft ? '\n' + draft : ''}` : draft
  const item = existing || ((draft || file) ? { id:`chat-${Date.now()}-${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`, content, draft, draftScope:orderNo.value, kind:file ? 'image' as const : 'text' as const } : null)
  if (!item || item.content.length > 1000) { notice.value = '内容过长，请精简后发送'; return }
  if (file && file.size > 5 * 1024 * 1024) { notice.value = '图片大小不能超过 5MB'; return }
  if (file) imageFile.value = file
  busy.value = true; pending.value = item; notice.value = ''
  try { write(key,item); pendingStored.value = true } catch { pendingStored.value = false }
  try {
    let data:unknown = {content:item.content,client_message_id:item.id}
    if (item.kind === 'image') { if (!imageFile.value) throw new Error('图片已不可用'); const form = new FormData(); form.append('content',item.content); form.append('client_message_id',item.id); form.append('image',imageFile.value); data = form }
    const result = await api<Message>('/chat/send/', {method:'POST',data})
    if (alive) await finish(result,item)
  } catch (cause) {
    if (!alive) return
    const failure = cause as {code?:number;message?:string}
    if (!existing && failure.code === 400) { try { localStorage.removeItem(key) } catch {} pending.value = null; imageFile.value = null; notice.value = failure.message || '发送失败，请检查内容' }
    else notice.value = '发送结果未确认，请先核对'
  } finally { if (alive) busy.value = false }
}
async function check() {
  const item = pending.value; if (!item || busy.value) return
  busy.value = true
  try { const result = await api<{status:'FOUND'|'UNKNOWN';message:Message | null}>(`/chat/message-status/?client_message_id=${encodeURIComponent(item.id)}`); if (result.status === 'FOUND' && result.message) await finish(result.message,item); else notice.value = item.kind === 'image' && !imageFile.value ? '暂未查到，请稍后再核对' : '暂未查到，可重试原消息' }
  catch { notice.value = '暂时无法核对，请稍后重试' } finally { busy.value = false }
}
function abandon() {
  const item = pending.value; if (!item || busy.value) return
  try { if (read<Pending>(key)?.id === item.id) localStorage.removeItem(key) } catch {}
  if (item.kind !== 'image' && (item.draftScope || '') === orderNo.value && !input.value) input.value = item.draft || item.content
  imageFile.value = null; pending.value = null; confirmAbandon.value = false
}
function clearOrderContext() {
  orderNo.value = ''
  const query = { ...route.query }; delete query.order_no
  void router.replace({ query })
}
function removeOrder() {
  const general = read<string>(draftKey('')) || ''
  if (general && general !== input.value) { confirmSwitch.value = true; return }
  saveDraft(input.value,''); saveDraft('',orderNo.value); clearOrderContext()
}
function switchOrder() { clearOrderContext(); input.value = read<string>(draftKey('')) || ''; confirmSwitch.value = false }
function choose(event:Event) { const el = event.target as HTMLInputElement, file = el.files?.[0]; el.value = ''; if (file) void send(undefined,file) }
const refresh = coalesced(async () => { await load() }), update = () => { if (!document.hidden) refresh.request() }
const offChat = onRealtime('chat_message',update), offConnected = onRealtime('connected',update)
onMounted(() => { void load(); document.addEventListener('visibilitychange',update) })
onUnmounted(() => { alive = false; sequence++; refresh.dispose(); offChat(); offConnected(); document.removeEventListener('visibilitychange',update) })
const canRetry = computed(() => pending.value && (pending.value.kind !== 'image' || imageFile.value))
</script>

<template>
  <main class="page chat-page">
    <header class="page-heading">
      <div><p class="caption">兴安电竞 · 陪玩端</p><h1>平台客服</h1></div>
      <Button size="xs" variant="outline" @click="router.push('/messages')">返回消息</Button>
    </header>
    <Card class="chat-panel">
      <header class="chat-heading"><h2>站内咨询</h2><span class="caption">文字与图片</span></header>
      <div v-if="orderNo" class="context">
        <div><span class="caption">关联订单</span><strong>{{ orderNo }}</strong></div>
        <Button size="xs" variant="outline" :disabled="busy || !!pending" @click="removeOrder">移除</Button>
      </div>

      <div ref="history" class="history" role="region" aria-label="咨询消息记录" tabindex="0" @scroll="event => { const el = event.target as HTMLElement; if (el.scrollHeight - el.clientHeight - el.scrollTop > 60) browsing = true }">
        <div v-if="before" class="history-action"><Button size="xs" variant="outline" :disabled="loading" @click="load(true)">加载更早消息</Button></div>
        <div v-if="error" role="alert" class="error"><p>{{ error }}</p><Button size="xs" variant="outline" @click="load()">重试</Button></div>
        <p v-if="loading && !items.length" role="status" class="history-empty">正在加载…</p>
        <p v-else-if="!items.length && !error" class="history-empty">写下你想咨询的问题</p>
        <article v-for="item in items" :key="item.id" :class="['message', item.is_from_support ? 'support' : 'own']">
          <div class="message-meta"><strong>{{ item.is_from_support ? '客服' : '我' }}</strong><time :datetime="item.created_at">{{ new Date(item.created_at).toLocaleString() }}</time></div>
          <div class="bubble"><PrivateImage v-if="item.image_url" :src="item.image_url" alt="咨询图片" /><p v-if="item.content">{{ item.content }}</p></div>
        </article>
      </div>

      <div v-if="browsing" class="history-action"><Button size="xs" variant="outline" @click="browsing = false; load()">回到最新消息</Button></div>
      <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
      <section v-if="pending" class="pending" aria-label="待核对的消息">
        <h3>{{ busy ? '正在处理消息…' : '上条消息结果未确认，请先核对。' }}</h3>
        <p v-if="!pendingStored">设备未能保存核对编号，请保持此页面。</p>
        <p v-if="pending.kind === 'image' && !imageFile">原图片已不可用，请先核对发送结果。</p>
        <p class="pending-copy">{{ pending.kind === 'image' ? (pending.content ? '图片消息：' + pending.content : '图片消息') : pending.content }}</p>
        <div class="actions">
          <Button size="xs" variant="outline" :disabled="busy" @click="check">核对结果</Button>
          <Button v-if="canRetry" size="xs" variant="outline" :disabled="busy" @click="send(pending)">重试原消息</Button>
          <Button size="xs" variant="outline" :disabled="busy" @click="confirmAbandon = true">放弃核对</Button>
        </div>
      </section>

      <div class="composer">
        <label class="compose-field">咨询内容<Textarea v-model="input" maxlength="1000" :disabled="!!pending || busy" placeholder="输入文字，也可作为图片说明" /></label>
        <p class="caption">选好图片后即发送，单张最多 5 MB。</p>
        <div class="composer-actions">
          <label class="file-button">发送图片<input type="file" accept="image/*" :disabled="busy || !!pending" @change="choose" /></label>
          <Button size="xs" :disabled="busy || !!pending || !input.trim()" @click="send()">{{ busy ? '发送中…' : '发送' }}</Button>
        </div>
      </div>
    </Card>

    <Dialog v-model:open="confirmAbandon">
      <DialogContent class="chat-dialog"><DialogHeader><DialogTitle>放弃核对？</DialogTitle><DialogDescription>消息可能已经送达，请先查看聊天记录。</DialogDescription></DialogHeader>
        <DialogFooter><Button size="xs" variant="outline" @click="confirmAbandon = false">返回</Button><Button size="xs" @click="abandon">放弃核对</Button></DialogFooter>
      </DialogContent>
    </Dialog>
    <Dialog v-model:open="confirmSwitch">
      <DialogContent class="chat-dialog"><DialogHeader><DialogTitle>切换为普通咨询</DialogTitle><DialogDescription>普通咨询已有草稿。切换后显示普通草稿，当前订单草稿仍会保留。</DialogDescription></DialogHeader>
        <DialogFooter><Button size="xs" variant="outline" @click="confirmSwitch = false">返回</Button><Button size="xs" @click="switchOrder">切换</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  </main>
</template>

<style scoped>
.chat-page { display:grid; min-width:0; max-width:760px; gap:12px; }
.page-heading,.chat-heading,.context,.actions,.composer-actions,.message-meta,.error { display:flex; align-items:center; gap:8px; }
.page-heading,.chat-heading,.context,.composer-actions { justify-content:space-between; }
h1,h2,h3,p { margin:0; } h1 { margin-top:4px; font-size:24px; line-height:32px; } h2 { font-size:16px; line-height:24px; } h3 { font-size:14px; line-height:20px; }
.caption,.message-meta,.compose-field { color:var(--muted-foreground); font-size:12px; line-height:16px; }
.chat-panel { display:grid; grid-template-columns:minmax(0,1fr); gap:12px; min-width:0; padding:16px; border:1px solid var(--border); border-radius:12px; background:var(--card); box-shadow:none; }
.context,.pending,.feedback { padding:8px 12px; border-radius:8px; background:var(--secondary); }
.context>div,.pending,.composer,.compose-field { display:grid; gap:4px; min-width:0; }
.context strong { font-size:14px; line-height:20px; overflow-wrap:anywhere; }
.history { display:flex; flex-direction:column; min-width:0; gap:12px; height:clamp(192px,42dvh,416px); overflow:auto; padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--background); }
.history:focus-visible,.file-button:focus-within { outline:2px solid var(--primary); outline-offset:2px; }
.history-action,.history-empty { text-align:center; }
.history-empty,.error,.feedback,.pending { font-size:13px; line-height:20px; }
.history-empty { margin:auto; color:var(--muted-foreground); }
.message { display:grid; gap:4px; min-width:0; max-width:85%; }
.own { align-self:flex-end; } .support { align-self:flex-start; }
.message-meta { flex-wrap:wrap; gap:4px 8px; } .own .message-meta { justify-content:end; }
.message-meta strong { font-weight:600; color:var(--foreground); }
.bubble { justify-self:start; min-width:0; max-width:100%; padding:8px 12px; border:1px solid var(--border); border-radius:8px; background:var(--card); color:var(--foreground); font-size:14px; line-height:20px; }
.own .bubble { justify-self:end; border-color:#dbeafe; background:var(--secondary); }
.bubble p,.pending-copy,.feedback,.error p { white-space:pre-wrap; overflow-wrap:anywhere; }
.bubble :deep(img) { display:block; width:auto; max-width:min(200px,100%); max-height:240px; object-fit:contain; }
.bubble :deep(a) { display:block; } .bubble :deep(a) + p { margin-top:4px; }
.pending { gap:8px; }
.pending-copy { padding-left:8px; border-left:2px solid var(--primary); }
.actions { flex-wrap:wrap; } .composer { gap:8px; }
.compose-field { color:var(--foreground); font-weight:600; }
.compose-field :deep(textarea) { min-width:0; min-height:60px; padding:8px; resize:vertical; font-family:inherit; font-size:14px; line-height:20px; font-weight:400; }
.file-button { position:relative; display:inline-flex; align-items:center; justify-content:center; min-height:24px; padding:0 8px; overflow:hidden; border:1px solid var(--border); border-radius:6px; color:var(--foreground); font-size:12px; line-height:16px; font-weight:600; }
.file-button input { position:absolute; inset:0; width:100%; height:100%; opacity:0; cursor:pointer; }
.file-button:has(input:disabled) { opacity:.5; }
.chat-page :deep([data-slot='button']),:global(.chat-dialog [data-slot='button']) { border:1px solid transparent; font-family:inherit; font-size:12px; line-height:16px; font-weight:600; box-shadow:none; }
.chat-page :deep([data-variant='outline']),:global(.chat-dialog [data-variant='outline']) { border-color:var(--border); color:var(--foreground); background:var(--card); }
.chat-page :deep(button:focus-visible),:global(.chat-dialog button:focus-visible) { outline:2px solid var(--primary); outline-offset:2px; }
@media(max-width:600px) { .chat-panel,.history { padding:8px; } .message { max-width:90%; } }
@media(max-width:600px),(pointer:coarse) { .chat-page :deep([data-slot='button']),:global(.chat-dialog [data-slot='button']),.file-button { min-height:44px; min-width:44px; } .compose-field :deep(textarea) { font-size:16px; } }
</style>
