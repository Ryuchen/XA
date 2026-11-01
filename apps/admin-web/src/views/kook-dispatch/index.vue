<template>
  <div v-loading="loading" class="kook-page">
    <el-alert v-if="loadError" title="配置读取失败，请重试；当前不能确认服务端配置" type="error" :closable="false" show-icon><el-button :disabled="loading || saving" @click="refresh">重新读取</el-button></el-alert>
    <el-alert v-if="reconcile" :title="reconcile" type="warning" :closable="false" show-icon><p>{{ reconcile === '保存结果未确认' ? '请求可能仍在处理，读取只是当前快照；请先确认请求状态。' : '请重新读取并核对最新配置。' }}读取前可保留当前草稿。</p><el-button :disabled="loading || saving" @click="refresh">重新读取</el-button></el-alert>
    <template v-if="loaded">
    <el-alert title="规则按顺序匹配，每笔订单只进入第一个命中的 KOOK 频道。保存后才能校验和发送测试。" type="info" :closable="false" />
    <el-card>
      <template #header><div class="card-heading"><span>机器人与发送方式</span><el-tag :type="savedReady ? 'success' : 'info'">{{ savedReady ? '读取状态：启用且配置完整' : '读取状态：未启用或配置待完善' }}</el-tag></div></template>
      <el-form label-position="top" :disabled="!editable">
        <el-form-item label="KOOK 派单"><el-switch v-model="form.enabled" /><span class="hint">关闭后保留频道与文案，停止新的投递</span></el-form-item>
        <el-form-item label="Bot Token"><div class="token-field"><el-input v-model="form.bot_token" type="password" autocomplete="new-password" :placeholder="tokenSaved ? '已保存；留空保持原 Token' : '填写机器人 Bot Token，不含 Bot 前缀'" /><el-checkbox v-if="tokenSaved" v-model="form.clear_token">清除已保存 Token</el-checkbox></div></el-form-item>
        <el-form-item v-if="tokenError"><el-alert title="已保存的 Token 无法读取，请重新填写并保存" type="error" :closable="false" /></el-form-item>
        <el-form-item label="自动发送"><el-checkbox v-model="form.auto_new_orders">新订单待接单</el-checkbox><el-checkbox v-model="form.auto_rejected_orders">拒单回池</el-checkbox><span class="hint">关闭自动发送后仍可在派单管理中手工发单</span></el-form-item>
        <el-form-item label="陪玩端链接"><el-input v-model="form.provider_order_url" placeholder="https://provider.xaesports.club" /><span class="hint">卡片按钮打开陪玩端，登录后按实际资格接单</span></el-form-item>
      </el-form>
    </el-card>
    <el-card>
      <template #header><div class="card-heading"><span>频道与派单文案</span><el-button v-if="canEdit" :disabled="!editable" @click="editRoute()">新增频道规则</el-button></div></template>
      <div class="route-mobile" aria-label="频道规则">
        <el-empty v-if="!form.routes.length" :image-size="60" description="暂无频道规则" />
        <article v-for="(row, index) in form.routes" :key="row.id" class="route-card">
          <div class="route-head"><strong>{{ index + 1 }}. {{ row.name }}</strong><el-switch v-model="row.enabled" :disabled="!editable" :aria-label="`${row.name}规则启用状态`" /></div>
          <p>{{ condition(row) }}</p><p class="route-meta">文字频道 {{ row.channel_id }}</p>
          <div v-if="canEdit" class="route-actions">
            <el-button :disabled="!editable" @click="editRoute(index)">编辑</el-button>
            <el-button :disabled="!editable || dirty || !tokenSaved || !!checking" @click="checkChannel(row)">校验频道</el-button>
            <el-button :disabled="!editable || dirty || !tokenSaved || !!testing || uncertain(row)" @click="sendTest(row)">{{ probeKeys[row.id] ? '核对上次测试' : '发送测试' }}</el-button>
            <el-button :disabled="!editable || index === 0" @click="move(index, -1)">上移</el-button><el-button :disabled="!editable || index === form.routes.length - 1" @click="move(index, 1)">下移</el-button>
            <el-button :disabled="!editable" type="danger" plain @click="removeRoute(index)">移除</el-button>
          </div>
        </article>
      </div>
      <el-table class="route-desktop" :data="form.routes" empty-text="添加一条规则，填写文字频道 ID 和对应派单文案">
        <el-table-column label="规则 / 顺序" min-width="150"><template #default="{ row, $index }">{{ $index + 1 }}. {{ row.name }}</template></el-table-column>
        <el-table-column label="匹配条件" min-width="170"><template #default="{ row }">{{ condition(row as Route) }}</template></el-table-column>
        <el-table-column prop="channel_id" label="文字频道 ID" min-width="180" />
        <el-table-column label="启用" width="80"><template #default="{ row }"><el-switch v-model="row.enabled" :disabled="!editable" :aria-label="`${row.name}规则启用状态`" /></template></el-table-column>
        <el-table-column label="操作" min-width="320"><template #default="{ row, $index }">
          <el-button v-if="canEdit" link type="primary" :disabled="!editable" @click="editRoute($index)">编辑文案</el-button>
          <el-button v-if="canEdit" link :disabled="!editable || dirty || !tokenSaved || !!checking" @click="checkChannel(row as Route)">校验频道</el-button>
          <el-button v-if="canEdit" link type="primary" :disabled="!editable || dirty || !tokenSaved || !!testing || uncertain(row as Route)" @click="sendTest(row as Route)">{{ probeKeys[row.id] ? '核对上次测试' : '发送测试' }}</el-button>
          <el-button v-if="canEdit" link :disabled="!editable || $index === 0" @click="move($index, -1)">上移</el-button>
          <el-button v-if="canEdit" link :disabled="!editable || $index === form.routes.length - 1" @click="move($index, 1)">下移</el-button>
          <el-button v-if="canEdit" link type="danger" :disabled="!editable" @click="removeRoute($index)">移除</el-button>
        </template></el-table-column>
      </el-table>
      <p class="hint">只填写文字频道 ID；服务器 ID 和语音频道不能发派单消息。按游戏 / 玩法分类的规则放前面，“全部订单”的兜底规则放最后。</p>
      <p v-if="!tokenSaved" class="hint">保存 Bot Token 后可校验频道和发送测试。</p>
      <div class="save-row"><el-button v-if="canEdit" type="primary" :loading="saving" :disabled="!editable || !dirty" @click="save">保存配置</el-button><el-button :disabled="loading || saving" @click="refresh">刷新</el-button><span v-if="dirty" class="unsaved">有未保存的修改，保存后才能校验和发送测试</span></div>
    </el-card>
    <el-card>
      <template #header>最近测试记录</template>
      <div class="route-mobile" aria-label="最近测试记录"><el-empty v-if="!tests.length" :image-size="60" description="尚未发送测试卡片" /><article v-for="row in tests" :key="row.id" class="route-card"><div class="route-head"><strong>频道 {{ row.channel_id }}</strong><el-tag :type="row.status === 'SENT' ? 'success' : 'warning'">{{ statusLabels[row.status] || row.status }}</el-tag></div><p class="route-meta">{{ formatDateTime(row.created_at) }}</p><p v-if="row.message_id">消息 ID：{{ row.message_id }}</p><p v-if="row.last_error">{{ row.last_error }}</p></article></div>
      <el-table class="route-desktop" :data="tests" empty-text="尚未发送测试卡片">
        <el-table-column prop="channel_id" label="目标频道" min-width="180" />
        <el-table-column label="结果" width="140"><template #default="{ row }"><el-tag :type="row.status === 'SENT' ? 'success' : 'warning'">{{ statusLabels[row.status] || row.status }}</el-tag></template></el-table-column>
        <el-table-column prop="message_id" label="KOOK 消息 ID" min-width="200" />
        <el-table-column prop="last_error" label="说明" min-width="240" />
        <el-table-column label="时间" width="170"><template #default="{ row }">{{ formatDateTime(row.created_at) }}</template></el-table-column>
      </el-table>
      <p class="hint">测试卡片标明“非真实订单”，不提醒身份组。真实订单的发送状态在“派单管理 → 一键发单”查看。结果未确认时先核对频道消息，避免重复发送。</p>
    </el-card>
    </template>
    <el-dialog v-model="dialog" title="频道与文案规则" width="800px" class="ap-modal" append-to-body :before-close="closeRoute" :close-on-click-modal="!previewing" :close-on-press-escape="!previewing">
      <el-form v-if="draft" :disabled="!editable || previewing" class="ap-modal-form ap-form-grid" label-position="top">
        <el-form-item label="规则名称"><el-input v-model="draft.name" maxlength="60" placeholder="例如：三角洲派单 / 默认派单" /></el-form-item>
        <el-form-item label="文字频道 ID"><el-input v-model="draft.channel_id" placeholder="右键目标文字频道 → 复制 ID" /></el-form-item>
        <el-form-item label="服务器 ID"><el-input v-model="draft.guild_id" placeholder="选填：用于核对频道所属服务器" /></el-form-item>
        <el-form-item label="游戏"><el-select v-model="draft.game_category_id" clearable placeholder="全部游戏"><el-option v-for="item in games" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item>
        <el-form-item label="玩法"><el-select v-model="draft.gameplay_id" clearable placeholder="全部玩法"><el-option v-for="item in gameplays" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item>
        <el-form-item label="平台"><el-select v-model="draft.platform" :empty-values="[null, undefined]"><el-option label="全部平台" value="" /><el-option label="PC端" value="PC" /><el-option label="手游端" value="MOBILE" /></el-select></el-form-item>
        <el-form-item label="提醒身份组"><el-input v-model="mentionInput" placeholder="可选：多个角色 ID 用英文逗号分隔" /></el-form-item>
        <el-form-item label="派单文案" class="ap-field--full"><el-input v-model="draft.template" type="textarea" :rows="10" maxlength="1200" show-word-limit /></el-form-item>
        <el-form-item label="可用变量" class="ap-field--full"><div class="variables"><el-button v-for="(label, field) in templateFields" :key="field" size="small" @click="draft!.template += '{' + field + '}'">{{ label }} {{ '{' + field + '}' }}</el-button></div></el-form-item>
        <el-form-item label="文案说明" class="ap-field--full"><span class="hint">必须保留 {order_no} 和 {provider_income}。只支持上列变量，打手实得来自订单的收益快照。</span></el-form-item>
        <el-form-item label="预览" class="ap-field--full"><div class="preview-box"><el-button :loading="previewing" @click="previewTemplate">预览文案</el-button><pre v-if="previewText">{{ previewText }}</pre></div></el-form-item>
      </el-form>
      <template #footer><el-button :disabled="previewing" @click="closeRoute()">取消</el-button><el-button v-if="canEdit" type="primary" :loading="previewing" @click="confirmRoute">应用到配置</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { request } from '@/api/request'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/format'

type Option = { id: number; name: string }
type Route = { id: string; name: string; enabled: boolean; channel_id: string; guild_id: string; game_category_id: number | null; gameplay_id: number | null; platform: string; template: string; mention_role_ids: string[] }
type Probe = { id: string; channel_id: string; status: string; message_id: string; last_error: string; created_at: string }
const auth = useAuthStore()
const canEdit = computed(() => auth.hasPerm('dispatch_config:edit'))
const form = reactive({ revision: 0, enabled: false, bot_token: '', clear_token: false, auto_new_orders: true, auto_rejected_orders: true, provider_order_url: 'https://provider.xaesports.club', routes: [] as Route[] })
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
async function confirm(message: string, title: string) { try { await ElMessageBox.confirm(message, title); return true } catch { return false } }
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
    apply(result.data); ElMessage.success('KOOK 配置已保存')
  } catch (error: any) {
    if (disposed || id !== sequence || session !== auth.sessionRevision) return
    const status = error?.response?.status
    if (status === 409) reconcile.value = '配置已被其他管理员修改'
    else if (!status || status >= 500) reconcile.value = '保存结果未确认'
    else ElMessage.error(error?.response?.data?.msg || error?.response?.data?.detail || '保存未通过，请检查配置后重试')
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
  if (!draft.value || !draft.value.name.trim() || !/^[0-9]{1,64}$/.test(draft.value.channel_id)) { ElMessage.warning('请填写规则名称和数字格式的文字频道 ID'); return }
  const ids = mentionInput.value.split(',').map(x => x.trim()).filter(Boolean)
  if (ids.some(x => !/^[0-9]{1,64}$/.test(x)) || ids.length > 10) { ElMessage.warning('身份组 ID 应为数字，最多 10 个，使用英文逗号分隔'); return }
  try { await previewTemplate() } catch { return }
  const route = { ...draft.value, game_category_id: draft.value.game_category_id || null, gameplay_id: draft.value.gameplay_id || null, mention_role_ids: ids }
  if (editing.value < 0) form.routes.push(route); else form.routes[editing.value] = route
  dialog.value = false
}
async function checkChannel(route: Route) { if (!editable.value || dirty.value || !tokenSaved.value) return; checking.value = route.id; try { const data = (await request<{ name: string; guild_id: string }>({ url: api + 'check/', method: 'post', data: { route_id: route.id } })).data; ElMessage.success(`已确认文字频道「${data.name}」，服务器 ${data.guild_id}；发送权限以测试卡片为准`) } catch { /* 请求层显示校验错误。 */ } finally { checking.value = '' } }
function uncertain(route: Route) { const last = tests.value.find(x => x.channel_id === route.channel_id); return !!last && ['UNKNOWN', 'SENDING'].includes(last.status) }
async function sendTest(route: Route) {
  if (!editable.value || dirty.value || !tokenSaved.value || uncertain(route)) return
  const session = auth.sessionRevision, revision = form.revision, snapshot = JSON.stringify(route)
  if (!probeKeys[route.id]) {
    if (!await confirm(`将向 ${route.name}（${route.channel_id}）发送一条标明测试的卡片，不创建订单、不提醒身份组。`, '发送 KOOK 测试')) return
    if (!editable.value || dirty.value || session !== auth.sessionRevision || revision !== form.revision || JSON.stringify(form.routes.find(item => item.id === route.id)) !== snapshot) { ElMessage.warning('配置已变化，请重新核对频道'); return }
    probeKeys[route.id] = crypto.randomUUID()
  }
  testing.value = route.id
  try {
    const result = (await request<Probe>({ url: api + 'test/', method: 'post', data: { route_id: route.id, request_id: probeKeys[route.id], confirm: true } })).data
    tests.value = [result, ...tests.value.filter(x => x.id !== result.id)].slice(0, 10)
    if (result.status === 'SENT') { ElMessage.success('测试卡片已发送，请查看 KOOK 频道'); delete probeKeys[route.id] }
    else { ElMessage.warning(result.last_error || '结果待核对，请查看测试记录'); if (result.status === 'FAILED') delete probeKeys[route.id] }
  } catch { /* 保留请求 ID，网络失败时只核对原请求，不重复投递。 */ } finally { testing.value = '' }
}
let closeCheck: Promise<boolean> | null = null
function confirmClose(): Promise<boolean> {
  if (saving.value || loading.value || previewing.value || checking.value || testing.value) { ElMessage.warning('操作进行中，请稍后离开'); return Promise.resolve(false) }
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
.kook-page { display: grid; gap: 20px; }
.card-heading, .save-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.save-row { justify-content: flex-start; margin-top: 18px; flex-wrap: wrap; }
.hint { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.7; margin-left: 12px; }
.token-field, .preview-box { width: 100%; }
.variables { display: flex; flex-wrap: wrap; gap: 6px; }
.variables .el-button { margin: 0; }
.unsaved { color: var(--el-color-warning); font-size: 13px; }
.route-mobile { display: none; }
.route-card { padding: 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--card); overflow-wrap: anywhere; }
.route-card + .route-card { margin-top: 10px; }
.route-card p { margin: 8px 0 0; }
.route-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.route-meta { color: var(--muted-foreground); font-size: 12px; }
.route-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.route-actions .el-button { margin: 0; }
@media (max-width: 720px) { .route-mobile { display: block; } .route-desktop { display: none; } .card-heading { flex-wrap: wrap; } .hint { margin-left: 0; } }
pre { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--el-fill-color-light); padding: 16px; border-radius: 6px; font-family: inherit; line-height: 1.7; }
</style>
