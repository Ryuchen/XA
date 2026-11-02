<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Badge } from '@xa/shadcn-vue/badge'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import { Skeleton } from '@xa/shadcn-vue/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@xa/shadcn-vue/tabs'
import { Input } from '@xa/shadcn-vue/input'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { api, currentUser } from '../lib/session'

type PointRow = {id:number;amount:number;balance_after:number;remark:string;created_at:string}
type Binding = {enabled:boolean;connected:boolean;bound:boolean;nickname:string;online_seconds:number;online_points:number;checked_in:boolean;rules:string}
type Reward = {id:number;name:string;kind:string;point_cost:number;description:string;stock:number | null}
type Claim = {id:number;name_snapshot:string;kind_snapshot:string;tag_reviewed:boolean | null;tag_revision_required:boolean;tag_editable:boolean;tag_revision_submitted:boolean;expires_at:string | null;tag_suspension_reason:string;points_spent:number;points_returned:boolean;status:string;status_label:string;delivery_note:string;details:Record<string,string>}
type Pending = {rewardId:number;name:string;requestId:string;details:Record<string,string>}
const rewardKinds:Record<string,string> = {PHYSICAL:'实物寄送',VIRTUAL:'游戏内交付',SERVICE:'服务权益',NAMING:'冠名权益',TAG:'自定义 TAG',BALANCE:'余额兑换',POINTS:'积分'}
const fields:Record<string,{key:string;placeholder:string;max:number}[]> = {TAG:[{key:'tag',placeholder:'希望展示的 TAG',max:30}],NAMING:[{key:'provider_name',placeholder:'希望冠名的陪玩',max:50}],PHYSICAL:[{key:'recipient',placeholder:'收件人',max:50},{key:'phone',placeholder:'联系电话',max:20},{key:'address',placeholder:'完整收货地址',max:255}]}
const route = useRoute(), router = useRouter()
function routeClaim(value:unknown) { const raw = String(value || ''); const id = Number(raw); return /^[1-9]\d*$/.test(raw) && Number.isSafeInteger(id) ? id : null }
function routeTab(value:unknown) { return ['shop','claims','ledger'].includes(String(value)) ? String(value) : 'shop' }
const focus = ref(routeClaim(route.query.claim_id))
const points = ref(0), ledger = ref<PointRow[]>([]), next = ref<number | null>(null), binding = ref<Binding | null>(null), code = ref(''), rewards = ref<Reward[]>([]), claims = ref<Claim[]>([]), claimsNext = ref<number | null>(null), focused = ref<Claim | null>(null)
const deliveryOpen = ref(false)
const tab = ref(focus.value ? 'claims' : routeTab(route.query.tab)), loading = ref(true), busy = ref(false), error = ref(''), notice = ref('')
const uid = ref(''), note = ref(''), extra = ref<Record<string,string>>({}), editing = ref<Claim | null>(null), editDetails = ref<Record<string,string>>({}), editIssue = ref(''), redeeming = ref<Reward | null>(null)
const owner = currentUser.value?.id || '', pendingKey = `xa-points-redemption:${owner}`
function getPending():Pending | null { try { return (JSON.parse(localStorage.getItem(pendingKey) || 'null') as {data:Pending} | null)?.data || null } catch { return null } }
const pending = ref<Pending | null>(getPending()); let sequence = 0, routeRevision = 0, alive = true
const shownClaims = computed(() => focus.value ? focused.value ? [focused.value] : [] : claims.value)
async function load() {
  const current = ++sequence; loading.value = true; error.value = ''
  try {
    const [p,b,r,c,f] = await Promise.all([api<{points:number;list:PointRow[];next:number | null}>('/club/points/'),api<Binding>('/club/kook-binding/'),api<Reward[]>('/club/rewards/'),api<{items:Claim[];next:number | null}>('/club/claims/?page_size=20'),focus.value ? api<Claim>(`/club/claims/${focus.value}/`) : Promise.resolve(null)])
    if (!alive || current !== sequence) return
    points.value = p.points; ledger.value = p.list; next.value = p.next; binding.value = b; rewards.value = r; claims.value = c.items; claimsNext.value = c.next; focused.value = f
  } catch (cause) { if (alive && current === sequence) error.value = cause instanceof Error ? cause.message : '加载失败' }
  finally { if (alive && current === sequence) loading.value = false }
}
async function bind() {
  if (busy.value) return; busy.value = true
  try { const result = await api<{code:string}>('/club/kook-binding/',{method:'POST',data:{}}); code.value = result.code; try { await navigator.clipboard.writeText(`绑定 ${result.code}`); notice.value = '绑定码已复制' } catch { notice.value = '请手动复制绑定码' } }
  catch (cause) { notice.value = cause instanceof Error ? cause.message : '获取绑定码失败' } finally { busy.value = false }
}
async function sendRedemption(item:Pending) {
  const revision = routeRevision
  try {
    const claim = await api<Claim>('/club/redeem/',{method:'POST',data:{reward_id:item.rewardId,details:item.details,client_request_id:item.requestId}})
    localStorage.removeItem(pendingKey); pending.value = null
    if (!alive) return
    if (revision === routeRevision) await router.push({path:'/points',query:{claim_id:claim.id}})
    await load(); notice.value = '兑换已提交'
  } catch (cause) {
    const failure = cause as {code?:number;message?:string}
    if (failure.code && failure.code < 500 && failure.code !== 409 && failure.code !== 429) { try { localStorage.removeItem(pendingKey) } catch {} pending.value = null; notice.value = failure.message || '兑换未成功' }
    else notice.value = '兑换结果未确认，请重试原申请'
  }
}
async function redeem() {
  const reward = redeeming.value; if (!reward || busy.value || pending.value || !owner) return
  busy.value = true; redeeming.value = null
  try {
    const details:Record<string,string> = Object.fromEntries((fields[reward.kind] || []).map(field => [field.key,(extra.value[field.key] || '').trim()]))
    details.game_uid = ['SERVICE','VIRTUAL'].includes(reward.kind) ? uid.value.trim() : ''; details.note = note.value.trim()
    const item:Pending = {rewardId:reward.id,name:reward.name,requestId:`redeem-${Date.now()}-${Math.random().toString(36).slice(2)}`,details}
    localStorage.setItem(pendingKey,JSON.stringify({data:item})); pending.value = item; await sendRedemption(item)
  } catch (cause) { notice.value = cause instanceof Error ? cause.message : '兑换未提交' } finally { busy.value = false }
}
async function retry() { if (!pending.value || busy.value) return; busy.value = true; try { await sendRedemption(pending.value) } finally { busy.value = false } }
async function more(kind:'claims'|'ledger') {
  const cursor = kind === 'claims' ? claimsNext.value : next.value; if (!cursor || busy.value) return; busy.value = true
  try { if (kind === 'claims') { const data = await api<{items:Claim[];next:number | null}>(`/club/claims/?page_size=20&before=${cursor}`); const ids = new Set(claims.value.map(row => row.id)); claims.value.push(...data.items.filter(row => !ids.has(row.id))); claimsNext.value = data.next } else { const data = await api<{list:PointRow[];next:number | null}>(`/club/points/?before=${cursor}`); ledger.value.push(...data.list); next.value = data.next } }
  catch (cause) { notice.value = cause instanceof Error ? cause.message : '加载失败' } finally { busy.value = false }
}
function edit(claim:Claim) { editing.value = claim; editDetails.value = {...claim.details}; editIssue.value = '' }
async function refreshEditing() {
  if (!editing.value || busy.value) return; busy.value = true
  try { const latest = await api<Claim>(`/club/claims/${editing.value.id}/`); claims.value = claims.value.map(row => row.id === latest.id ? latest : row); if (focused.value?.id === latest.id) focused.value = latest; editing.value = latest; if (latest.status === 'PENDING' || latest.tag_editable) { editIssue.value = ''; notice.value = '可继续保存资料' } else notice.value = '客服尚未退回，请稍后再查' }
  catch (cause) { notice.value = cause instanceof Error ? cause.message : '刷新失败' } finally { busy.value = false }
}
async function save() {
  const claim = editing.value; if (!claim || busy.value || editIssue.value) return; busy.value = true
  try {
    const details = {...claim.details,...Object.fromEntries(Object.entries(editDetails.value).map(([key,value]) => [key,value.trim()]))}
    const result = await api<Claim>(`/club/claims/${claim.id}/`,{method:'PATCH',data:{details}})
    claims.value = claims.value.map(row => row.id === result.id ? result : row); if (focused.value?.id === result.id) focused.value = result; editing.value = null; notice.value = claim.tag_editable ? result.tag_revision_submitted ? '已提交复核' : '内容未改变' : '交付资料已保存'
  } catch (cause) {
    const failure = cause as {errorCode?:string;message?:string}
    if (failure.errorCode === 'CLAIM_NOT_EDITABLE') { editIssue.value = '权益状态已变化，刚填写的资料尚未保存，可复制后联系客服。'; try { const latest = await api<Claim>(`/club/claims/${claim.id}/`); claims.value = claims.value.map(row => row.id === latest.id ? latest : row); if (focused.value?.id === latest.id) focused.value = latest; editing.value = latest } catch {} }
    else notice.value = failure.message || '保存失败'
  } finally { busy.value = false }
}
function changeTab(value:unknown) { void router.push({path:'/points',query:{tab:routeTab(value)}}) }
watch(() => route.fullPath, () => {
  routeRevision++
  const previous = focus.value
  focus.value = routeClaim(route.query.claim_id)
  tab.value = focus.value ? 'claims' : routeTab(route.query.tab)
  if (previous !== focus.value) { focused.value = null; void load() }
}, {flush:'sync'})
onMounted(() => { void load() }); onUnmounted(() => { alive = false; sequence++ })
</script>
<template>
  <main class="page stack points-page"><header class="points-header"><div><p class="muted">兴安电竞 · 陪玩端</p><h1>我的积分</h1></div><Button size="xs" variant="outline" @click="router.push('/mine')">返回个人中心</Button></header>
    <Alert v-if="notice" role="status" class="notice"><AlertDescription>{{ notice }}</AlertDescription></Alert><Alert v-if="error" variant="destructive" role="alert"><AlertTitle>积分信息未完整更新</AlertTitle><AlertDescription>{{ error }} <Button size="xs" variant="outline" :disabled="loading" @click="load">重新加载</Button></AlertDescription></Alert>
    <Alert v-if="pending" class="pending-alert"><AlertTitle>兑换结果待核对</AlertTitle><AlertDescription><p>「{{ pending.name }}」兑换结果未确认，请重试原申请；不会重复扣除积分。</p><Button size="xs" variant="outline" :disabled="busy" @click="retry">重试原申请</Button></AlertDescription></Alert>
    <div class="points-overview">
      <Card class="panel balance-card"><div class="balance-heading"><h2>可用积分</h2><Button size="xs" variant="ghost" :disabled="busy || loading" @click="load">{{ loading ? '刷新中…' : '刷新' }}</Button></div><p class="balance-value" aria-live="polite"><strong class="total">{{ loading || error ? '—' : points }}</strong><span>积分</span></p><p class="balance-note">兑换项目与进度，随时查看</p></Card>
      <Card v-if="binding" class="panel stack kook-card"><div class="section-heading"><h2>KOOK 积分</h2><Badge variant="secondary">{{ !binding.enabled ? '未启用自动计分' : binding.connected ? '自动计分已连接' : '自动计分未连接' }}</Badge></div><p class="muted">{{ binding.rules }}</p><p class="binding-name">{{ binding.bound ? `已绑定：${binding.nickname || '本人账号'}` : '尚未绑定 KOOK 账号' }}</p><template v-if="binding.bound"><dl class="kook-stats"><div><dt>累计在线</dt><dd>{{ Math.floor(binding.online_seconds / 3600) }} <span>小时</span> {{ Math.floor(binding.online_seconds % 3600 / 60) }} <span>分钟</span></dd></div><div><dt>在线获得</dt><dd>{{ binding.online_points }} <span>积分</span></dd></div></dl><p class="muted">{{ binding.checked_in ? '今日已签到' : '请在 KOOK 签到大厅发送“签到”，每天获得 5 积分。' }}</p></template><template v-else><div><Button size="xs" :disabled="busy || !binding.enabled" @click="bind">获取并复制绑定码</Button></div><p v-if="code" class="muted binding-code">请用本人 KOOK 账号在签到大厅发送“绑定 {{ code }}”。验证码 10 分钟有效，发送后点击刷新。</p></template></Card>
      <Card v-else-if="loading" class="panel" aria-label="正在加载 KOOK 积分"><Skeleton class="kook-skeleton"/></Card>
    </div>
    <Tabs :model-value="tab" class="points-tabs" @update:model-value="changeTab"><TabsList class="tabs" aria-label="积分功能"><TabsTrigger v-for="item in [{id:'shop',label:'积分兑换'},{id:'claims',label:'兑换记录'},{id:'ledger',label:'积分明细'}]" :key="item.id" :value="item.id">{{ item.label }}</TabsTrigger></TabsList>
    <div v-if="loading" class="loading-grid" aria-label="正在加载积分项目"><Skeleton v-for="n in 3" :key="n" class="reward-skeleton"/></div><template v-else-if="!error">
      <TabsContent value="shop" class="tab-panel"><Card class="panel delivery-summary"><div><h2>交付资料</h2><p class="muted">游戏账号、收件信息与权益要求</p></div><Button size="xs" variant="outline" @click="deliveryOpen = true">填写资料</Button></Card><div class="grid"><Card v-for="reward in rewards" :key="reward.id" class="panel stack reward-card"><Badge variant="secondary">{{ rewardKinds[reward.kind] || reward.kind }}</Badge><h2>{{ reward.name }}</h2><p class="muted reward-description">{{ reward.description }}</p><p class="reward-cost"><strong>{{ reward.point_cost }}</strong> 积分</p><div class="reward-actions"><Button size="xs" :disabled="busy || !!pending || points < reward.point_cost || reward.stock === 0" @click="redeeming = reward">{{ reward.stock === 0 ? '已兑完' : points < reward.point_cost ? '积分不足' : '兑换' }}</Button></div></Card></div><p v-if="!rewards.length" class="muted">暂无可兑换项目</p></TabsContent>
      <TabsContent value="claims" class="tab-panel"><div v-if="focus" class="row"><p class="muted">正在查看此条权益</p><Button size="xs" variant="outline" @click="changeTab('claims')">查看全部记录</Button></div><Card v-for="claim in shownClaims" :key="claim.id" class="panel stack claim-card"><div class="section-heading"><h2>{{ claim.name_snapshot }}</h2><Badge variant="secondary">{{ claim.status_label }}</Badge></div><p v-if="claim.delivery_note" class="muted">{{ claim.delivery_note }}</p><p v-if="claim.kind_snapshot === 'TAG' && claim.status === 'PENDING' && claim.tag_revision_required" class="muted">请按客服要求修改 TAG 后保存。</p><p v-if="claim.kind_snapshot === 'TAG' && claim.status === 'COMPLETED' && claim.tag_reviewed === false" class="muted">{{ claim.tag_suspension_reason ? '标签已暂停展示：' + claim.tag_suspension_reason : '标签待客服复核，暂不展示。' }}</p><p v-if="claim.points_returned" class="muted">已退回 {{ claim.points_spent }} 积分。</p><p v-if="['SERVICE','VIRTUAL'].includes(claim.kind_snapshot)" class="muted">游戏 UID：{{ claim.details.game_uid || '待补充' }}</p><p v-for="field in fields[claim.kind_snapshot] || []" :key="field.key" class="muted">{{ field.placeholder }}：{{ claim.details[field.key] || '待补充' }}</p><p v-if="claim.tag_suspension_reason && claim.expires_at" class="muted">{{ new Date(claim.expires_at).getTime() <= Date.now() ? '已到期' : '有效至 ' + new Date(claim.expires_at).toLocaleString() }}</p><p v-if="claim.tag_editable" class="muted">{{ claim.tag_revision_submitted ? '已提交修改，等待客服复核。' : '可修改后提交复核，有效期不变。' }}</p><div v-if="claim.status === 'PENDING' || claim.tag_editable"><Button size="xs" variant="outline" :disabled="!!editing || busy" @click="edit(claim)">{{ claim.tag_editable ? '修改 TAG' : '补充交付资料' }}</Button></div></Card><p v-if="!shownClaims.length" class="muted">暂无兑换记录</p><Button size="xs" v-if="!focus && claimsNext" variant="outline" :disabled="busy" @click="more('claims')">加载更多记录</Button></TabsContent>
      <TabsContent value="ledger" class="tab-panel"><Card class="panel list"><div v-for="row in ledger" :key="row.id" class="row record"><div><strong>{{ row.remark }}</strong><p class="muted">{{ row.created_at.slice(0,16).replace('T',' ') }}</p></div><span class="ledger-value"><strong>{{ row.amount > 0 ? '+' : '' }}{{ row.amount }}</strong><span class="muted">余额 {{ row.balance_after }}</span></span></div><p v-if="!ledger.length" class="muted">暂无积分明细</p></Card><Button size="xs" v-if="next" variant="outline" :disabled="busy" @click="more('ledger')">加载更多</Button></TabsContent>
    </template>
    </Tabs>
    <Dialog v-model:open="deliveryOpen"><DialogContent class="points-dialog delivery-dialog"><DialogHeader><DialogTitle>交付资料</DialogTitle><DialogDescription>填写后，随下一次兑换申请提交。</DialogDescription></DialogHeader><div class="delivery-fields"><label v-if="rewards.some(row => ['SERVICE','VIRTUAL'].includes(row.kind))" class="field">游戏 UID<Input v-model="uid" maxlength="80" placeholder="可在兑换后补充" /></label><p v-if="rewards.some(row => row.kind === 'TAG')" class="muted">TAG 由客服审核，通过后最多展示最近 3 个。</p><template v-for="kind in [...new Set(rewards.map(row => row.kind))]" :key="kind"><label v-for="field in fields[kind] || []" :key="field.key" class="field">{{ field.placeholder }}<Input v-model="extra[field.key]" :maxlength="field.max" :placeholder="field.placeholder" /></label></template><label class="field">交付要求<Input v-model="note" maxlength="255" placeholder="可选" /></label></div><DialogFooter><Button size="xs" @click="deliveryOpen = false">返回兑换</Button></DialogFooter></DialogContent></Dialog>
    <Dialog :open="!!redeeming" @update:open="value => { if (!value && !busy) redeeming = null }"><DialogContent class="points-dialog"><DialogHeader><DialogTitle>确认兑换</DialogTitle><DialogDescription>使用 {{ redeeming?.point_cost }} 积分兑换「{{ redeeming?.name }}」，交付由客服办理。</DialogDescription></DialogHeader><DialogFooter><Button size="xs" variant="outline" @click="redeeming = null">取消</Button><Button size="xs" :disabled="busy" @click="redeem">确认兑换</Button></DialogFooter></DialogContent></Dialog>
    <Dialog :open="!!editing" @update:open="value => { if (!value && !busy) editing = null }"><DialogContent class="points-dialog"><DialogHeader><DialogTitle>补充资料 · {{ editing?.name_snapshot }}</DialogTitle><DialogDescription>{{ editing?.tag_editable ? '修改后由客服复核，有效期不变。' : '请填写交付资料。' }}</DialogDescription></DialogHeader><template v-if="editing"><p v-if="editIssue" role="alert" class="error">{{ editIssue }}</p><label v-if="['SERVICE','VIRTUAL'].includes(editing.kind_snapshot)" class="field">游戏 UID<Input v-model="editDetails.game_uid" maxlength="80" /></label><label v-for="field in fields[editing.kind_snapshot] || []" :key="field.key" class="field">{{ field.placeholder }}<Input v-model="editDetails[field.key]" :maxlength="field.max" /></label><label v-if="!editing.tag_editable" class="field">交付说明<Input v-model="editDetails.note" maxlength="255" /></label><Button size="xs" v-if="editIssue" variant="outline" :disabled="busy" @click="refreshEditing">刷新交付状态</Button></template><DialogFooter><Button size="xs" variant="outline" :disabled="busy" @click="editing = null">关闭</Button><Button size="xs" v-if="!editIssue" :disabled="busy" @click="save">{{ editing?.tag_editable ? '提交复核' : '保存资料' }}</Button></DialogFooter></DialogContent></Dialog>
  </main>
</template>
<style scoped>
.points-page { gap:16px; } .points-header,.section-heading,.balance-heading { display:flex; align-items:center; justify-content:space-between; gap:12px; } .points-header { margin-bottom:8px; } .points-header>div { min-width:0; } .points-header>button { flex-shrink:0; }
h1 { margin:4px 0 0; font-size:24px; line-height:1.35; letter-spacing:-.5px; } h2 { margin:0; font-size:16px; line-height:1.5; font-weight:600; overflow-wrap:anywhere; } p { margin:0; overflow-wrap:anywhere; } .section-heading { flex-wrap:wrap; } .section-heading :deep([data-slot='badge']) { max-width:100%; white-space:normal; }
.points-page .panel { padding:16px; border-radius:16px; gap:16px; min-width:0; box-shadow:0 1px 4px rgb(15 23 42 / 3%); } .points-overview { display:grid; grid-template-columns:minmax(0,.8fr) minmax(0,1.2fr); gap:16px; align-items:stretch; }
.points-page .balance-card { background:linear-gradient(135deg,#1e3a8a,#2563eb); color:#fff; border-color:transparent; justify-content:space-between; } .balance-card h2 { font-size:14px; font-weight:500; } .balance-value { display:flex; align-items:baseline; flex-wrap:wrap; gap:8px; } .total { font-size:40px; line-height:1.2; font-weight:600; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; } .balance-value>span,.balance-note { font-size:12px; color:#dbeafe; }
.points-page .kook-card { gap:12px; } .binding-name { font-size:14px; overflow-wrap:anywhere; } .kook-stats { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin:0; padding:12px 0; border-top:1px solid var(--border); border-bottom:1px solid var(--border); } .kook-stats div { display:grid; gap:4px; min-width:0; } .kook-stats dt { font-size:12px; color:var(--muted-foreground); } .kook-stats dd { margin:0; font-size:20px; font-weight:500; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; } .kook-stats dd span { font-size:12px; font-weight:400; color:var(--muted-foreground); } .binding-code { padding:12px; border-radius:8px; background:var(--secondary); }
.notice { background:var(--secondary); color:var(--primary); } .notice :deep([data-slot='alert-description']) { color:inherit; } .pending-alert :deep([data-slot='alert-description']) { display:grid; gap:12px; } .pending-alert :deep(button) { justify-self:start; }
.points-tabs { gap:16px; min-width:0; } .tabs { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:4px; width:100%; height:auto; padding:4px; border:1px solid var(--border); border-radius:12px; background:var(--muted); }
.tabs :deep([data-slot='tabs-trigger']) { min-width:0; min-height:44px; padding:8px; border:1px solid transparent; border-radius:8px; background:transparent; color:var(--muted-foreground); font:inherit; font-size:14px; white-space:normal; cursor:pointer; }
.tabs :deep([data-slot='tabs-trigger'][data-state='active']) { background:var(--card); color:var(--primary); border-color:var(--border); box-shadow:0 2px 4px rgb(15 23 42 / 4%); } .tabs :deep([data-slot='tabs-trigger']:focus-visible) { outline:2px solid var(--primary); outline-offset:2px; }
.tab-panel { display:flex; flex-direction:column; gap:16px; min-width:0; } .tab-panel[data-state='inactive'] { display:none; }
.points-page .delivery-summary { flex-direction:row; justify-content:space-between; align-items:center; gap:12px; } .delivery-summary>div { min-width:0; } .delivery-summary p { margin-top:4px; } .delivery-summary>button { flex-shrink:0; }
:global(.points-dialog .delivery-fields) { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; } :global(.points-dialog .delivery-fields>p) { grid-column:1/-1; } :global(.points-dialog .field) { min-width:0; font-size:14px; }
.grid,.loading-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr)); gap:16px; } .reward-card { min-width:0; } .reward-description { flex:1; } .reward-cost { font-size:12px; color:var(--muted-foreground); } .reward-cost strong { font-size:24px; color:var(--foreground); font-variant-numeric:tabular-nums; } .reward-actions { display:flex; justify-content:flex-end; padding-top:12px; border-top:1px solid var(--border); }
.points-page .claim-card { gap:12px; } .claim-card>.section-heading { padding-bottom:12px; border-bottom:1px solid var(--border); } .points-page .list { gap:0; } .record { padding:16px 0; border-bottom:1px solid var(--border); gap:16px; } .record:first-child { padding-top:0; } .record:last-child { border:0; padding-bottom:0; } .record>div { min-width:0; } .record strong { font-size:14px; overflow-wrap:anywhere; } .record>div>p { margin-top:4px; } .ledger-value { display:grid; gap:4px; text-align:right; min-width:88px; font-variant-numeric:tabular-nums; } .ledger-value>strong { font-size:20px; } .ledger-value .muted { font-size:12px; white-space:nowrap; }
.kook-skeleton { min-height:160px; } .reward-skeleton { min-height:160px; }
.points-page :deep([data-slot='button']), :global(.points-dialog [data-slot='button']) { min-height:var(--compact-control-height,24px); border:1px solid transparent; background:var(--primary); color:var(--primary-foreground); font-family:inherit; font-size:12px; box-shadow:none; }
.points-page :deep([data-slot='button'][data-variant='outline']), :global(.points-dialog [data-slot='button'][data-variant='outline']) { background:var(--card); color:var(--foreground); border-color:var(--border); }
.points-page :deep([data-slot='button'][data-variant='ghost']) { background:transparent; color:var(--foreground); } .points-page .balance-card :deep([data-slot='button']) { color:white; background:rgb(255 255 255 / 12%); }
.points-page :deep([data-slot='button']:focus-visible), :global(.points-dialog [data-slot='button']:focus-visible) { outline:2px solid var(--primary); outline-offset:4px; } .points-page .balance-card :deep([data-slot='button']:focus-visible) { outline-color:white; }
:global(.points-dialog) { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; border-color:var(--border); border-radius:16px; padding:24px; max-height:calc(100dvh - 32px); overflow-y:auto; }
:global(.points-dialog [data-slot='dialog-close']) { display:grid; place-items:center; width:44px; height:44px; right:8px; top:8px; padding:12px; border:0; border-radius:12px; background:transparent; color:var(--muted-foreground); }
:global(.points-dialog [data-slot='dialog-title']) { padding-right:32px; font-size:18px; } :global(.points-dialog input) { min-height:44px; }
@media(max-width:600px) { .points-page { gap:12px; } .points-header { gap:8px; margin-bottom:4px; } .points-overview { grid-template-columns:minmax(0,1fr); gap:12px; } .points-page .panel { padding:16px; border-radius:16px; } .points-page .balance-card { min-height:160px; } .points-tabs,.tab-panel { gap:12px; } .grid { gap:12px; } .tabs :deep([data-slot='tabs-trigger']) { padding:8px 4px; } :global(.points-dialog .delivery-fields) { grid-template-columns:minmax(0,1fr); gap:12px; } }
.points-page { --compact-control-height:24px; }
.points-page .panel { gap:12px; } .points-page .balance-card { justify-content:space-between; } .tabs :deep([data-slot='tabs-trigger']) { min-height:28px; padding:4px 8px; font-size:12px; }
:global(.points-dialog) { --compact-control-height:24px; padding:16px; }
:global(.points-dialog input) { min-height:28px; height:28px; padding:4px 8px; font-size:12px; }
@media(max-width:600px),(pointer:coarse) { .points-page :deep([data-slot='button']),:global(.points-dialog [data-slot='button']) { min-width:44px; } .points-page { --compact-control-height:44px; } .tabs :deep([data-slot='tabs-trigger']) { min-height:44px; padding:8px 4px; font-size:14px; } :global(.points-dialog) { --compact-control-height:44px; } :global(.points-dialog input) { min-height:44px; height:44px; font-size:14px; } }
@media(prefers-reduced-motion:reduce) { .points-page :deep(*) { transition:none; animation:none; } }
</style>
