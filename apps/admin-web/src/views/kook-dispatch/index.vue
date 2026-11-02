<template>
  <div :aria-busy="loading" class="kook-page">
    <p v-if="loading" role="status" class="hint">正在读取配置…</p>
    <Alert v-if="loadError" variant="destructive"><AlertDescription>配置读取失败，请重试；当前不能确认服务端配置<Button size="xs" :disabled="loading || saving" @click="refresh">重新读取</Button></AlertDescription></Alert>
    <Alert v-if="reconcile" ><AlertDescription>{{ reconcile }}<p>{{ reconcile === '保存结果未确认' ? '请求可能仍在处理，读取只是当前快照；请先确认请求状态。' : '请重新读取并核对最新配置。' }}读取前可保留当前草稿。</p><Button size="xs" :disabled="loading || saving" @click="refresh">重新读取</Button></AlertDescription></Alert>
    <template v-if="loaded">
    <Alert  ><AlertDescription>规则按顺序匹配，每笔订单只进入第一个命中的 KOOK 频道。保存后才能校验和发送测试。</AlertDescription></Alert>
    <section class="kook-card">
      <div class="kook-card-header"><div class="card-heading"><span>机器人与发送方式</span><Badge variant="outline">{{ savedReady ? '读取状态：启用且配置完整' : '读取状态：未启用或配置待完善' }}</Badge></div></div>
      <fieldset  :disabled="!editable">
        <label class="kook-field"><span>KOOK 派单</span><Switch v-model="form.enabled" :disabled="!editable" aria-label="KOOK 派单" /><span class="hint">关闭后保留频道与文案，停止新的投递</span></label>
        <div class="kook-field"><label for="kook-token">Bot Token</label><div class="token-field"><Input id="kook-token" v-model="form.bot_token" type="password" autocomplete="new-password" :placeholder="tokenSaved ? '已保存；留空保持原 Token' : '填写机器人 Bot Token，不含 Bot 前缀'" /><label v-if="tokenSaved" class="kook-toggle"><Checkbox v-model="form.clear_token" :disabled="!editable" />清除已保存 Token</label></div></div>
        <div v-if="tokenError" class="kook-field"><Alert variant="destructive" ><AlertDescription>已保存的 Token 无法读取，请重新填写并保存</AlertDescription></Alert></div>
        <div class="kook-field"><span>自动发送</span><label class="kook-toggle"><Checkbox v-model="form.auto_new_orders" :disabled="!editable" />新订单待接单</label><label class="kook-toggle"><Checkbox v-model="form.auto_rejected_orders" :disabled="!editable" />拒单回池</label><span class="hint">关闭自动发送后仍可在派单管理中手工发单</span></div>
        <label class="kook-field"><span>陪玩端链接</span><Input v-model="form.provider_order_url" placeholder="https://provider.xaesports.club" /><span class="hint">卡片按钮打开陪玩端，登录后按实际资格接单</span></label>
      </fieldset>
    </section>
    <section class="kook-card">
      <div class="kook-card-header"><div class="card-heading"><span>频道与派单文案</span><Button size="xs" v-if="canEdit" :disabled="!editable" @click="editRoute()">新增频道规则</Button></div></div>
      <Alert v-if="legacySettlementRoutes.length" ><AlertDescription><strong>部分自定义文案包含旧结算说明</strong>
        <p>{{ legacySettlementRoutes.join('、') }}：报单通过后仍可能需要等待订单结算或收益解冻，请编辑文案核对。</p>
      </AlertDescription></Alert>
      <div class="route-mobile" aria-label="频道规则">
        <p v-if="!form.routes.length" class="empty-state">暂无频道规则</p>
        <article v-for="(row, index) in form.routes" :key="row.id" class="route-card">
          <div class="route-head"><strong>{{ index + 1 }}. {{ row.name }}</strong><Switch v-model="row.enabled" :disabled="!editable" :aria-label="`${row.name}规则启用状态`" /></div>
          <p>{{ condition(row) }}</p><p class="route-meta">文字频道 {{ row.channel_id }}</p>
          <div v-if="canEdit" class="route-actions">
            <Button size="xs" :disabled="!editable" @click="editRoute(index)">编辑</Button>
            <Button size="xs" :disabled="!editable || dirty || !tokenSaved || !!checking" @click="checkChannel(row)">校验频道</Button>
            <Button size="xs" :disabled="!editable || dirty || !tokenSaved || !!testing || uncertain(row)" @click="sendTest(row)">{{ probeKeys[row.id] ? '核对上次测试' : '发送测试' }}</Button>
            <Button size="xs" :disabled="!editable || index === 0" @click="move(index, -1)">上移</Button><Button size="xs" :disabled="!editable || index === form.routes.length - 1" @click="move(index, 1)">下移</Button>
            <Button size="xs" :disabled="!editable" variant="destructive" @click="removeRoute(index)">移除</Button>
          </div>
        </article>
      </div>
      <div class="route-desktop"><Table><TableHeader><TableRow><TableHead>规则 / 顺序</TableHead><TableHead>匹配条件</TableHead><TableHead>文字频道 ID</TableHead><TableHead>启用</TableHead><TableHead>操作</TableHead></TableRow></TableHeader><TableBody><TableRow v-if="!form.routes.length"><TableCell :colspan="5" class="empty-state">添加一条规则，填写文字频道 ID 和对应派单文案</TableCell></TableRow><TableRow v-for="(row, index) in form.routes" :key="row.id"><TableCell>{{ index + 1 }}. {{ row.name }}</TableCell><TableCell>{{ condition(row) }}</TableCell><TableCell>{{ row.channel_id }}</TableCell><TableCell><Switch v-model="row.enabled" :disabled="!editable" :aria-label="row.name + '规则启用状态'" /></TableCell><TableCell><div v-if="canEdit" class="route-actions"><Button size="xs" variant="link" :disabled="!editable" @click="editRoute(index)">编辑文案</Button><Button size="xs" variant="link" :disabled="!editable || dirty || !tokenSaved || !!checking" @click="checkChannel(row)">校验频道</Button><Button size="xs" variant="link" :disabled="!editable || dirty || !tokenSaved || !!testing || uncertain(row)" @click="sendTest(row)">{{ probeKeys[row.id] ? '核对上次测试' : '发送测试' }}</Button><Button size="xs" variant="link" :disabled="!editable || index === 0" @click="move(index, -1)">上移</Button><Button size="xs" variant="link" :disabled="!editable || index === form.routes.length - 1" @click="move(index, 1)">下移</Button><Button size="xs" variant="link" :disabled="!editable" @click="removeRoute(index)">移除</Button></div></TableCell></TableRow></TableBody></Table></div>
      <p class="hint">只填写文字频道 ID；服务器 ID 和语音频道不能发派单消息。按游戏 / 玩法分类的规则放前面，“全部订单”的兜底规则放最后。</p>
      <p v-if="!tokenSaved" class="hint">保存 Bot Token 后可校验频道和发送测试。</p>
      <div class="save-row"><Button size="xs" v-if="canEdit"  :disabled="saving || !editable || !dirty" @click="save">保存配置</Button><Button size="xs" :disabled="loading || saving" @click="refresh">刷新</Button><span v-if="dirty" class="unsaved">有未保存的修改，保存后才能校验和发送测试</span></div>
    </section>
    <section class="kook-card">
      <div class="kook-card-header">最近测试记录</div>
      <div class="route-mobile" aria-label="最近测试记录"><p v-if="!tests.length" class="empty-state">尚未发送测试卡片</p><article v-for="row in tests" :key="row.id" class="route-card"><div class="route-head"><strong>频道 {{ row.channel_id }}</strong><Badge variant="outline">{{ statusLabels[row.status] || row.status }}</Badge></div><p class="route-meta">{{ formatDateTime(row.created_at) }}</p><p v-if="row.message_id">消息 ID：{{ row.message_id }}</p><p v-if="row.last_error">{{ row.last_error }}</p></article></div>
      <div class="route-desktop"><Table><TableHeader><TableRow><TableHead>目标频道</TableHead><TableHead>结果</TableHead><TableHead>KOOK 消息 ID</TableHead><TableHead>说明</TableHead><TableHead>时间</TableHead></TableRow></TableHeader><TableBody><TableRow v-if="!tests.length"><TableCell :colspan="5" class="empty-state">尚未发送测试卡片</TableCell></TableRow><TableRow v-for="row in tests" :key="row.id"><TableCell>{{ row.channel_id }}</TableCell><TableCell><Badge variant="outline">{{ statusLabels[row.status] || row.status }}</Badge></TableCell><TableCell>{{ row.message_id }}</TableCell><TableCell>{{ row.last_error }}</TableCell><TableCell>{{ formatDateTime(row.created_at) }}</TableCell></TableRow></TableBody></Table></div>
      <p class="hint">测试卡片标明“非真实订单”，不提醒身份组。真实订单的发送状态在“派单管理 → 一键发单”查看。结果未确认时先核对频道消息，避免重复发送。</p>
    </section>
    </template>
    <Dialog :open="dialog" @update:open="value => { if (!value) void closeRoute() }"><DialogContent class="kook-dialog" :show-close-button="!previewing" @interact-outside.prevent="closeRoute()" @escape-key-down.prevent="closeRoute()"><DialogHeader><DialogTitle>频道与文案规则</DialogTitle><DialogDescription>编辑频道匹配条件与派单文案，应用到配置后仍需保存。</DialogDescription></DialogHeader>
      <fieldset v-if="draft" :disabled="!editable || previewing" class="route-form" >
        <label class="kook-field"><span>规则名称</span><Input v-model="draft.name" maxlength="60" placeholder="例如：三角洲派单 / 默认派单" /></label>
        <label class="kook-field"><span>文字频道 ID</span><Input v-model="draft.channel_id" placeholder="右键目标文字频道 → 复制 ID" /></label>
        <label class="kook-field"><span>服务器 ID</span><Input v-model="draft.guild_id" placeholder="选填：用于核对频道所属服务器" /></label>
        <label class="kook-field"><span>游戏</span><NativeSelect :model-value="String(draft.game_category_id || '')" @update:model-value="value => { draft!.game_category_id = value ? Number(value) : null }"><NativeSelectOption value="">全部游戏</NativeSelectOption><NativeSelectOption v-for="item in games" :key="item.id" :value="String(item.id)">{{ item.name }}</NativeSelectOption></NativeSelect></label>
        <label class="kook-field"><span>玩法</span><NativeSelect :model-value="String(draft.gameplay_id || '')" @update:model-value="value => { draft!.gameplay_id = value ? Number(value) : null }"><NativeSelectOption value="">全部玩法</NativeSelectOption><NativeSelectOption v-for="item in gameplays" :key="item.id" :value="String(item.id)">{{ item.name }}</NativeSelectOption></NativeSelect></label>
        <label class="kook-field"><span>平台</span><NativeSelect v-model="draft.platform"><NativeSelectOption value="">全部平台</NativeSelectOption><NativeSelectOption value="PC">PC端</NativeSelectOption><NativeSelectOption value="MOBILE">手游端</NativeSelectOption></NativeSelect></label>
        <label class="kook-field"><span>提醒身份组</span><Input v-model="mentionInput" placeholder="可选：多个角色 ID 用英文逗号分隔" /></label>
        <label class="ap-field--full kook-field"><span>派单文案</span><Textarea v-model="draft.template"  :rows="10" maxlength="1200"   /></label>
        <label v-if="hasLegacySettlementText(draft.template)" class="ap-field--full kook-field"><Alert ><AlertDescription><strong>请核对旧结算说明</strong><p>报单通过后仍可能需要等待订单结算或收益解冻。</p></AlertDescription></Alert></label>
        <div class="ap-field--full kook-field"><span>可用变量</span><div class="variables"><Button v-for="(label, field) in templateFields" :key="field" size="xs" @click="draft!.template += '{' + field + '}'">{{ label }} {{ '{' + field + '}' }}</Button></div></div>
        <label class="ap-field--full kook-field"><span>文案说明</span><span class="hint">必须保留 {order_no} 和 {provider_income}。只支持上列变量，打手实得来自订单的收益快照。</span></label>
        <div class="ap-field--full kook-field"><span>预览</span><div class="preview-box"><Button size="xs" :disabled="previewing" @click="previewTemplate">预览文案</Button><pre v-if="previewText">{{ previewText }}</pre></div></div>
      </fieldset>
      <DialogFooter><Button size="xs" :disabled="previewing" @click="closeRoute()">取消</Button><Button size="xs" v-if="canEdit"  :disabled="previewing" @click="confirmRoute">应用到配置</Button></DialogFooter>
    </DialogContent></Dialog>
<ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Switch } from '@xa/shadcn-vue/switch'
import { Checkbox } from '@xa/shadcn-vue/checkbox'
import { Badge } from '@xa/shadcn-vue/badge'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { showNotice } from '@/utils/notice'
import { request } from '@/api/request'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/format'

type Option = { id: number; name: string }
type Route = { id: string; name: string; enabled: boolean; channel_id: string; guild_id: string; game_category_id: number | null; gameplay_id: number | null; platform: string; template: string; mention_role_ids: string[] }
type Probe = { id: string; channel_id: string; status: string; message_id: string; last_error: string; created_at: string }
function successNotice(message: string) { showNotice(message, 'success') }
const auth = useAuthStore()
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const canEdit = computed(() => auth.hasPerm('dispatch_config:edit'))
const form = reactive({ revision: 0, enabled: false, bot_token: '', clear_token: false, auto_new_orders: true, auto_rejected_orders: true, provider_order_url: 'https://provider.xaesports.club', routes: [] as Route[] })
const hasLegacySettlementText = (text: string) => text.includes('报单审核通过后结算')
const legacySettlementRoutes = computed(() => form.routes.filter(route => hasLegacySettlementText(route.template)).map(route => route.name || '未命名规则'))
const loading = ref(false), saving = ref(false), tokenSaved = ref(false), tokenError = ref(false), savedReady = ref(false)
const loaded = ref(false), loadError = ref(false), reconcile = ref('')
const games = ref<Option[]>([]), gameplays = ref<Option[]>([]), tests = ref<Probe[]>([])
const templateFields = ref<Record<string, string>>({}), defaultTemplate = ref(''), savedState = ref('')
const dirty = computed(() => loaded.value && JSON.stringify(form) !== savedState.value)
const dialog = ref(false), draft = ref<Route>(), editing = ref(-1), mentionInput = ref(''), previewText = ref(''), previewing = ref(false)
const draftBaseline = ref('')
const draftDirty = computed(() => dialog.value && JSON.stringify([draft.value, mentionInput.value]) !== draftBaseline.value)
const checking = ref(''), testing = ref(''), probeKeys = reactive<Record<string, string>>({})
const editable = computed(() => canEdit.value && loaded.value && !loading.value && !saving.value && !loadError.value && !reconcile.value && !checking.value && !testing.value)
const statusLabels: Record<string, string> = { SENT: '发送成功', FAILED: '明确失败', UNKNOWN: '结果待核对', SENDING: '发送中 / 待核对' }
const api = '/config/kook-dispatch/'
let sequence = 0, disposed = false

function apply(data: any) {
  Object.assign(form, { revision: data.revision, enabled: data.enabled, bot_token: '', clear_token: false, auto_new_orders: data.auto_new_orders, auto_rejected_orders: data.auto_rejected_orders, provider_order_url: data.provider_order_url, routes: data.routes.map((r: Route) => ({ ...r, guild_id: r.guild_id || '' })) })
  tokenSaved.value = data.token_saved; tokenError.value = data.token_error; savedReady.value = data.ready
  games.value = data.games; gameplays.value = data.gameplays; tests.value = data.tests
  templateFields.value = data.template_fields; defaultTemplate.value = data.default_template
  savedState.value = JSON.stringify(form)
}
async function load() {
  const id = ++sequence, session = auth.sessionRevision
  loading.value = true; loadError.value = false
  try {
    const result = await request<any>({ url: api, silent: true })
    if (disposed || id !== sequence || session !== auth.sessionRevision) return
    apply(result.data); loaded.value = true; reconcile.value = ''
  } catch {
    if (!disposed && id === sequence && session === auth.sessionRevision) loadError.value = true
  } finally {
    if (!disposed && id === sequence && session === auth.sessionRevision) loading.value = false
  }
}
function confirm(description: string, title: string) { return askConfirm({ title, description }) }
async function refresh() {
  if (loading.value || saving.value) return
  const pending = reconcile.value === '保存结果未确认'
  if ((dirty.value || pending) && !await confirm(`${pending ? '保存请求可能仍在处理，读取只是当前快照；请先确认请求状态。' : ''}${dirty.value ? '重新读取将放弃尚未保存的修改。' : ''}是否继续？`, '重新读取配置')) return
  await load()
}
async function save() {
  if (!editable.value || !dirty.value) return
  const session = auth.sessionRevision, id = sequence
  saving.value = true
  try {
    const result = await request<any>({ url: api, method: 'put', data: form, silent: true })
    if (disposed || id !== sequence || session !== auth.sessionRevision) return
    apply(result.data); successNotice('KOOK 配置已保存')
  } catch (error: any) {
    if (disposed || id !== sequence || session !== auth.sessionRevision) return
    const status = error?.response?.status
    if (status === 409) reconcile.value = '配置已被其他管理员修改'
    else if (!status || status >= 500) reconcile.value = '保存结果未确认'
    else showNotice(error?.response?.data?.msg || error?.response?.data?.detail || '保存未通过，请检查配置后重试')
  } finally { if (!disposed && id === sequence && session === auth.sessionRevision) saving.value = false }
}
function condition(route: Route) { return [route.game_category_id ? games.value.find(x => x.id === route.game_category_id)?.name || `游戏 #${route.game_category_id}` : '', route.gameplay_id ? gameplays.value.find(x => x.id === route.gameplay_id)?.name || `玩法 #${route.gameplay_id}` : '', route.platform === 'PC' ? 'PC端' : route.platform === 'MOBILE' ? '手游端' : route.platform || ''].filter(Boolean).join(' · ') || '全部订单（兜底）' }
function editRoute(index = -1) { if (!editable.value) return; editing.value = index; draft.value = index < 0 ? { id: crypto.randomUUID(), name: '', enabled: true, channel_id: '', guild_id: '', game_category_id: null, gameplay_id: null, platform: '', template: defaultTemplate.value, mention_role_ids: [] } : JSON.parse(JSON.stringify(form.routes[index])); mentionInput.value = draft.value!.mention_role_ids.join(','); draftBaseline.value = JSON.stringify([draft.value, mentionInput.value]); previewText.value = ''; dialog.value = true }
async function closeRoute(done?: () => void) { if (previewing.value) return; if (draftDirty.value && !await confirm('关闭后会放弃这条规则尚未应用的修改，是否继续？', '放弃规则修改')) return; if (done) done(); else dialog.value = false }
function move(index: number, delta: number) { const [row] = form.routes.splice(index, 1); if (row) form.routes.splice(index + delta, 0, row) }
async function removeRoute(index: number) { if (await confirm('移除后，这条频道规则将不再用于新的派单。保存配置后生效。', '移除规则')) form.routes.splice(index, 1) }
async function previewTemplate() { if (!draft.value) return; previewing.value = true; try { previewText.value = (await request<{ content: string }>({ url: api + 'preview/', method: 'post', data: { template: draft.value.template } })).data.content } finally { previewing.value = false } }
async function confirmRoute() {
  if (!editable.value || previewing.value) return
  if (!draft.value || !draft.value.name.trim() || !/^[0-9]{1,64}$/.test(draft.value.channel_id)) { showNotice('请填写规则名称和数字格式的文字频道 ID'); return }
  const ids = mentionInput.value.split(',').map(x => x.trim()).filter(Boolean)
  if (ids.some(x => !/^[0-9]{1,64}$/.test(x)) || ids.length > 10) { showNotice('身份组 ID 应为数字，最多 10 个，使用英文逗号分隔'); return }
  try { await previewTemplate() } catch { return }
  const route = { ...draft.value, game_category_id: draft.value.game_category_id || null, gameplay_id: draft.value.gameplay_id || null, mention_role_ids: ids }
  if (editing.value < 0) form.routes.push(route); else form.routes[editing.value] = route
  dialog.value = false
}
async function checkChannel(route: Route) { if (!editable.value || dirty.value || !tokenSaved.value) return; checking.value = route.id; try { const data = (await request<{ name: string; guild_id: string }>({ url: api + 'check/', method: 'post', data: { route_id: route.id } })).data; successNotice(`已确认文字频道「${data.name}」，服务器 ${data.guild_id}；发送权限以测试卡片为准`) } catch { /* 请求层显示校验错误。 */ } finally { checking.value = '' } }
function uncertain(route: Route) { const last = tests.value.find(x => x.channel_id === route.channel_id); return !!last && ['UNKNOWN', 'SENDING'].includes(last.status) }
async function sendTest(route: Route) {
  if (!editable.value || dirty.value || !tokenSaved.value || uncertain(route)) return
  const session = auth.sessionRevision, revision = form.revision, snapshot = JSON.stringify(route)
  if (!probeKeys[route.id]) {
    if (!await confirm(`将向 ${route.name}（${route.channel_id}）发送一条标明测试的卡片，不创建订单、不提醒身份组。`, '发送 KOOK 测试')) return
    if (!editable.value || dirty.value || session !== auth.sessionRevision || revision !== form.revision || JSON.stringify(form.routes.find(item => item.id === route.id)) !== snapshot) { showNotice('配置已变化，请重新核对频道'); return }
    probeKeys[route.id] = crypto.randomUUID()
  }
  testing.value = route.id
  try {
    const result = (await request<Probe>({ url: api + 'test/', method: 'post', data: { route_id: route.id, request_id: probeKeys[route.id], confirm: true } })).data
    tests.value = [result, ...tests.value.filter(x => x.id !== result.id)].slice(0, 10)
    if (result.status === 'SENT') { successNotice('测试卡片已发送，请查看 KOOK 频道'); delete probeKeys[route.id] }
    else { showNotice(result.last_error || '结果待核对，请查看测试记录'); if (result.status === 'FAILED') delete probeKeys[route.id] }
  } catch { /* 保留请求 ID，网络失败时只核对原请求，不重复投递。 */ } finally { testing.value = '' }
}
let closeCheck: Promise<boolean> | null = null
function confirmClose(): Promise<boolean> {
  if (saving.value || loading.value || previewing.value || checking.value || testing.value) { showNotice('操作进行中，请稍后离开'); return Promise.resolve(false) }
  if (!dirty.value && !draftDirty.value) return Promise.resolve(true)
  if (closeCheck) return closeCheck
  const session = auth.sessionRevision
  closeCheck = confirm('离开将放弃尚未保存的修改，是否继续？', '放弃修改')
    .then(confirmed => confirmed && !disposed && session === auth.sessionRevision && !saving.value && !loading.value && !previewing.value && !checking.value && !testing.value)
    .finally(() => { closeCheck = null })
  return closeCheck
}
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value || draftDirty.value || saving.value) { event.preventDefault(); event.returnValue = '' } }
onBeforeRouteLeave(confirmClose)
defineExpose({ confirmClose })
watch(() => auth.sessionRevision, () => {
  ++sequence; loaded.value = false; loadError.value = false; reconcile.value = ''; loading.value = saving.value = false
  form.bot_token = ''; dialog.value = false; draft.value = undefined; savedState.value = ''
  if (auth.token && (auth.hasPerm('dispatch_config:view') || canEdit.value)) void load()
}, { flush: 'sync' })
onMounted(() => { window.addEventListener('beforeunload', beforeUnload); void load() })
onBeforeUnmount(() => { disposed = true; ++sequence; form.bot_token = ''; window.removeEventListener('beforeunload', beforeUnload) })
</script>

<style scoped>
.kook-card { min-width: 0; padding: 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.kook-card-header { margin-bottom: 16px; font-weight: 650; }
fieldset { border: 0; padding: 0; margin: 0; min-width: 0; display: grid; gap: 16px; }
.kook-field { display: grid; gap: 8px; min-width: 0; font-size: 13px; }
.kook-toggle { display: inline-flex; align-items: center; gap: 8px; min-height: 32px; }
.route-form { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.ap-field--full { grid-column: 1 / -1; }
:global(.kook-dialog) { max-width: min(800px, calc(100vw - 24px)); max-height: calc(100dvh - 24px); overflow-y: auto; }
.empty-state { padding: 24px; color: var(--muted-foreground); text-align: center; }
.route-desktop { overflow-x: auto; }
.route-desktop :deep(table) { min-width: 880px; }
.kook-page { display: grid; gap: 16px; }
.card-heading, .save-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.save-row { justify-content: flex-start; margin-top: 16px; flex-wrap: wrap; }
.hint { color: var(--muted-foreground); font-size: 13px; line-height: 1.7; margin-left: 12px; }
.token-field, .preview-box { width: 100%; }
.variables { display: flex; flex-wrap: wrap; gap: 8px; }
.variables button { margin: 0; }
.unsaved { color: var(--muted-foreground); font-size: 13px; }
.route-mobile { display: none; }
.route-card { padding: 12px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); overflow-wrap: anywhere; }
.route-card + .route-card { margin-top: 12px; }
.route-card p { margin: 8px 0 0; }
.route-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.route-meta { color: var(--muted-foreground); font-size: 12px; }
.route-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.route-actions button { margin: 0; }
@media (max-width: 720px) { .route-form { grid-template-columns: 1fr; } .route-mobile { display: block; } .route-desktop { display: none; } .card-heading { flex-wrap: wrap; } .hint { margin-left: 0; } }
pre { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--muted); padding: 16px; border-radius: 8px; font-family: inherit; line-height: 1.7; }
</style>
