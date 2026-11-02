<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { formatXaCoin } from '@xa/money'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Badge } from '@xa/shadcn-vue/badge'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import { Skeleton } from '@xa/shadcn-vue/skeleton'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { api, clearSession, currentUser } from '../lib/session'
import { fundRequest } from '../lib/fund'
import { coalesced, onRealtime } from '../lib/realtime'

type Profile = { display_name:string; status:string; is_verified:boolean; rating_avg:number; rating_count:number; completed_order_count:number; display_tags:{id:number;title:string;highlighted:boolean}[] }
type Deposit = { deposit_mode?:'CASH'|'EARNINGS'; deposit_percent?:number; hold_hours?:number; deposit_required:number; deposit_paid:number; deposit_remaining:number; balance:number }
type Evaluation = { id:number; reply_content:string }
const labels:Record<string,string> = { AVAILABLE:'接单中', BUSY:'忙碌', OFFLINE:'离线' }
const workLinks = [
  { path:'/profile', label:'资料与档期', detail:'展示资料与每周可接单时段' },
  { path:'/skills', label:'我的技能', detail:'查看技能与接单资格' },
  { path:'/evaluations', label:'评价管理', detail:'查看评价与回复' },
  { path:'/reservations', label:'我的预约', detail:'跟进预约安排' },
]
const accountLinks = [
  { path:'/wallet', label:'我的钱包', detail:'余额、收益与资金流水' },
  { path:'/points', label:'我的积分与兑换', detail:'查看积分与兑换记录' },
  { path:'/support-chat', label:'联系客服', detail:'咨询服务与账户问题' },
]
const router = useRouter(), profile = ref<Profile | null>(null), deposit = ref<Deposit | null>(null), evaluations = ref<Evaluation[]>([])
const loading = ref(true), error = ref(''), notice = ref(''), statusBusy = ref(false), depositBusy = ref(false), depositDialog = ref(false), logoutDialog = ref(false), depositIssue = ref('')
let sequence = 0, reviewSequence = 0, depositSequence = 0, statusVersion = 0, alive = true
let statusIntent:string | null = null, pendingStatusRefresh = false
async function load() {
  const current = ++sequence, review = ++reviewSequence, depositVersion = ++depositSequence, statusAtStart = statusVersion; loading.value = true; error.value = ''
  const results = await Promise.allSettled([api<Profile>('/users/escorts/me/'), api<Deposit>('/wallet/deposit/'), api<{list:Evaluation[]}>('/orders/evaluations/mine/')])
  if (!alive || current !== sequence) return
  if (review === reviewSequence && results[0].status === 'fulfilled') {
    const latest = results[0].value
    if (statusIntent !== null) latest.status = statusIntent
    else if (statusAtStart !== statusVersion && profile.value) latest.status = profile.value.status
    profile.value = latest
  }
  if (depositVersion === depositSequence && results[1].status === 'fulfilled') deposit.value = results[1].value
  if (review === reviewSequence && results[2].status === 'fulfilled') evaluations.value = results[2].value.list || []
  if (results.some(item => item.status === 'rejected')) error.value = '部分资料加载失败，请刷新后重试'
  loading.value = false
}
async function refreshReviews() {
  const current = ++reviewSequence, statusAtStart = statusVersion
  const results = await Promise.allSettled([api<Profile>('/users/escorts/me/'), api<{list:Evaluation[]}>('/orders/evaluations/mine/')])
  if (!alive || current !== reviewSequence) return
  if (results[0].status === 'fulfilled') {
    const latest = results[0].value
    if (profile.value) {
      Object.assign(profile.value, { rating_avg:latest.rating_avg, rating_count:latest.rating_count, completed_order_count:latest.completed_order_count })
      if (statusIntent !== null) pendingStatusRefresh = true
      else if (statusAtStart === statusVersion) profile.value.status = latest.status
    } else profile.value = latest
  }
  if (results[1].status === 'fulfilled') evaluations.value = results[1].value.list || []
  if (results.some(item => item.status === 'rejected')) error.value = '部分资料加载失败，请刷新后重试'
}
async function refreshDeposit() {
  const current = ++depositSequence
  try { const latest = await api<Deposit>('/wallet/deposit/'); if (alive && current === depositSequence) deposit.value = latest }
  catch { if (alive && current === depositSequence) error.value = '部分资料加载失败，请刷新后重试' }
}
const depositRefresh = coalesced(refreshDeposit)
const offWallet = onRealtime('wallet_update', data => {
  const id = (data as {account_id?:unknown} | null)?.account_id
  if (!document.hidden && id != null && String(id) === String(currentUser.value?.id)) depositRefresh.request()
})
async function refreshStatus() {
  if (statusIntent !== null) { pendingStatusRefresh = true; return }
  const current = ++statusVersion
  try {
    const latest = await api<Profile>('/users/escorts/me/')
    if (!alive || current !== statusVersion) return
    if (statusIntent !== null) { pendingStatusRefresh = true; return }
    if (profile.value) profile.value.status = latest.status
    else profile.value = latest
  } catch { if (alive && current === statusVersion) error.value = '部分资料加载失败，请刷新后重试' }
}
const statusRefresh = coalesced(refreshStatus)
const reviewRefresh = coalesced(refreshReviews)
const offOrder = onRealtime('order_status_update', data => {
  if (document.hidden || !data || typeof data !== 'object' || !Object.prototype.hasOwnProperty.call(data, 'provider')) return
  if ((data as {status?:string}).status === 'COMPLETED') reviewRefresh.request()
  else statusRefresh.request()
})
const offConnected = onRealtime('connected', () => { if (!document.hidden) { reviewRefresh.request(); depositRefresh.request() } })
async function status(value:unknown) {
  const next = String(value)
  if (!profile.value || statusBusy.value || next === profile.value.status || !['AVAILABLE','BUSY','OFFLINE'].includes(next)) return
  statusVersion++; statusIntent = next; statusBusy.value = true; notice.value = ''
  try { await api('/users/escorts/status/', {method:'POST',data:{status:next}}); if (profile.value) profile.value.status = next; notice.value = '状态已更新' }
  catch (cause) { statusIntent = null; notice.value = cause instanceof Error ? cause.message : '状态更新失败'; await load() }
  finally { statusIntent = null; statusBusy.value = false; if (pendingStatusRefresh) { pendingStatusRefresh = false; statusRefresh.request() } }
}
async function pay() {
  const data = deposit.value
  if (!data || depositBusy.value || depositIssue.value || data.deposit_remaining <= 0 || data.balance < data.deposit_remaining) return
  const current = ++depositSequence; depositBusy.value = true
  try { const result = await fundRequest<Deposit>('/wallet/deposit/', {amount:data.deposit_remaining}); if (alive) { if (current === depositSequence) deposit.value = result; depositDialog.value = false; notice.value = '押金缴纳成功' } }
  catch (cause) {
    if (!alive) return
    const failure = cause as {code?:number;message?:string}
    depositIssue.value = !failure.code || failure.code >= 500 || failure.code === 409 || failure.code === 429 ? '缴纳结果未确认，请查看钱包流水或联系客服核对。' : failure.message || '缴纳失败'
  } finally { if (alive) depositBusy.value = false }
}
function exit() { clearSession(); void router.replace('/login') }
function visible() { if (!document.hidden && !depositBusy.value && !statusBusy.value) void load() }
onMounted(() => { void load(); document.addEventListener('visibilitychange',visible) })
onUnmounted(() => { alive = false; sequence++; reviewSequence++; depositSequence++; statusVersion++; offOrder(); offWallet(); offConnected(); reviewRefresh.dispose(); depositRefresh.dispose(); statusRefresh.dispose(); document.removeEventListener('visibilitychange',visible) })
</script>
<template>
  <main class="page mine-page">
    <header class="mine-header"><div><p class="muted">兴安电竞 · 陪玩端</p><h1>个人中心</h1></div><Button size="xs" variant="outline" @click="router.push('/orders')">返回订单</Button></header>
    <Alert v-if="notice" class="mine-notice" role="status"><AlertDescription>{{ notice }}</AlertDescription></Alert>
    <Alert v-if="error" variant="destructive" class="mine-alert" role="alert"><AlertTitle>资料未完整更新</AlertTitle><AlertDescription><span>{{ error }}</span><Button size="xs" variant="outline" :disabled="loading" @click="load">{{ loading ? '刷新中…' : '重新加载' }}</Button></AlertDescription></Alert>

    <Card class="mine-card identity" :aria-busy="loading">
      <div class="identity-main">
        <div class="identity-person"><span class="identity-avatar" aria-hidden="true">{{ (profile?.display_name || currentUser?.nickname || '陪').slice(0,1) }}</span><div class="identity-copy"><h2 class="name">{{ profile?.display_name || currentUser?.nickname || '陪玩用户' }}</h2><p class="account-name">账号：{{ currentUser?.username || '—' }}</p><div class="identity-badges"><Badge v-if="profile" variant="secondary">{{ profile.is_verified ? '已认证' : '未认证' }}</Badge><Badge v-for="tag in (profile?.display_tags || []).slice(0,3)" :key="tag.id" variant="outline" :class="['identity-tag', { highlight:tag.highlighted }]">{{ tag.title }}</Badge></div></div></div>
        <label v-if="profile" class="status-field"><span><span class="status-dot" :data-status="profile.status" aria-hidden="true"/>接单状态</span><NativeSelect :model-value="profile.status" :disabled="statusBusy" @update:model-value="status"><NativeSelectOption v-for="(label,value) in labels" :key="value" :value="value">{{ label }}</NativeSelectOption></NativeSelect><span v-if="statusBusy" class="muted" role="status">正在更新…</span></label>
        <Skeleton v-else-if="loading" class="status-skeleton" aria-label="正在加载接单状态"/>
        <span v-else class="muted">接单状态暂不可用</span>
      </div>
      <dl v-if="profile" class="profile-stats" aria-label="服务概况"><div><dt>评分</dt><dd>{{ Number(profile.rating_avg || 0).toFixed(1) }}</dd></div><div><dt>评价数</dt><dd>{{ profile.rating_count }}</dd></div><div><dt>完成单</dt><dd>{{ profile.completed_order_count }}</dd></div></dl>
      <div v-else-if="loading" class="stats-loading" aria-label="正在加载服务概况"><Skeleton v-for="n in 3" :key="n" class="stat-skeleton"/></div>
    </Card>

    <div class="mine-columns">
      <Card class="mine-card menu-card work-card"><h2>接单管理</h2><nav aria-label="接单管理"><Button size="xs" v-for="link in workLinks" :key="link.path" variant="ghost" class="menu-link" @click="router.push(link.path)"><span class="menu-copy"><span class="menu-label">{{ link.label }}</span><span class="menu-detail">{{ link.detail }}</span><Badge v-if="link.path === '/evaluations' && evaluations.some(row => !row.reply_content)" variant="secondary" class="reply-badge">待回复 {{ evaluations.filter(row => !row.reply_content).length }}</Badge></span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg></Button></nav></Card>
      <div class="mine-column">
        <Card v-if="deposit" class="mine-card deposit-card"><div class="section-heading"><h2>押金</h2><Badge v-if="deposit.deposit_mode" variant="secondary">{{ deposit.deposit_mode === 'EARNINGS' ? '单抵押金' : '现金押金' }}</Badge></div><div class="deposit-primary"><div><p class="muted">待缴金额</p><p class="deposit-amount">{{ formatXaCoin(deposit.deposit_remaining) }} <span>币</span></p></div><Button size="xs" v-if="deposit.deposit_remaining > 0 && deposit.deposit_mode !== 'EARNINGS'" :disabled="depositBusy" @click="depositIssue = ''; depositDialog = true">去缴纳</Button></div><dl class="deposit-breakdown"><div><dt>应缴</dt><dd>{{ formatXaCoin(deposit.deposit_required) }} 币</dd></div><div><dt>已缴</dt><dd>{{ formatXaCoin(deposit.deposit_paid) }} 币</dd></div></dl><p v-if="deposit.deposit_mode === 'EARNINGS'" class="deposit-note">单抵押金为现金的 2 倍：净收益 {{ deposit.deposit_percent ?? 60 }}% 抵押金，抵满后全部结款；结款部分{{ deposit.hold_hours ? `冻结 ${deposit.hold_hours} 小时后解冻` : '按订单规则入账' }}。</p></Card>
        <Card v-else-if="loading" class="mine-card" aria-label="正在加载押金"><Skeleton class="section-skeleton"/><Skeleton class="deposit-skeleton"/></Card>
        <Card class="mine-card menu-card"><h2>账户服务</h2><nav aria-label="账户服务"><Button size="xs" v-for="link in accountLinks" :key="link.path" variant="ghost" class="menu-link" @click="router.push(link.path)"><span class="menu-copy"><span class="menu-label">{{ link.label }}</span><span class="menu-detail">{{ link.detail }}</span></span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg></Button></nav></Card>
      </div>
    </div>
    <footer class="mine-footer"><span class="muted">账号与登录</span><Button size="xs" variant="ghost" @click="logoutDialog = true">退出登录</Button></footer>
    <Dialog :open="depositDialog" @update:open="value => { if (!depositBusy) depositDialog = value }"><DialogContent class="mine-dialog" :show-close-button="!depositBusy"><DialogHeader><DialogTitle>缴纳押金</DialogTitle><DialogDescription>将从钱包余额扣除 {{ deposit ? formatXaCoin(deposit.deposit_remaining) : '—' }} 兴安币缴纳押金。</DialogDescription></DialogHeader><p v-if="deposit && deposit.balance < deposit.deposit_remaining" class="error">钱包余额不足，请联系客服充值。</p><p v-if="depositIssue" role="alert" class="error">{{ depositIssue }}</p><DialogFooter><Button size="xs" variant="outline" :disabled="depositBusy" @click="depositDialog = false">取消</Button><Button size="xs" :disabled="depositBusy || !!depositIssue || !deposit || deposit.balance < deposit.deposit_remaining" @click="pay">{{ depositBusy ? '缴纳中…' : '确认缴纳' }}</Button></DialogFooter></DialogContent></Dialog>
    <Dialog v-model:open="logoutDialog"><DialogContent class="mine-dialog"><DialogHeader><DialogTitle>退出登录</DialogTitle><DialogDescription>确认退出当前账号？</DialogDescription></DialogHeader><DialogFooter><Button size="xs" variant="outline" @click="logoutDialog = false">取消</Button><Button size="xs" @click="exit">确认退出</Button></DialogFooter></DialogContent></Dialog>
  </main>
</template>
<style scoped>
.mine-page { display:grid; gap:16px; padding-top:24px; }
.mine-header, .section-heading, .deposit-primary, .mine-footer { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.mine-header { margin-bottom:8px; } h1 { margin:4px 0 0; font-size:24px; line-height:1.35; letter-spacing:-.5px; }
h2, p, dl, dd { margin:0; } h2 { font-size:16px; line-height:1.5; font-weight:600; }
.mine-card { gap:16px; padding:16px; border:1px solid var(--border); border-radius:16px; box-shadow:0 1px 4px rgb(15 23 42 / 3%); min-width:0; }
.mine-notice { color:var(--primary); background:var(--secondary); } .mine-notice :deep([data-slot='alert-description']) { color:inherit; }
.mine-alert :deep([data-slot='alert-description']) { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px; }
.identity { padding-bottom:0; } .identity-main { display:flex; align-items:center; justify-content:space-between; gap:24px; min-width:0; }
.identity-person { display:flex; align-items:center; gap:16px; flex:1; min-width:0; }
.identity-avatar { display:grid; place-items:center; width:56px; height:56px; flex:0 0 56px; border-radius:16px; color:var(--primary-foreground); background:var(--primary); font-size:24px; font-weight:600; }
.identity-copy { min-width:0; } .name { font-size:20px; overflow-wrap:anywhere; } .account-name { margin-top:4px; color:var(--muted-foreground); font-size:12px; overflow-wrap:anywhere; }
.identity-badges { display:flex; flex-wrap:wrap; gap:4px; margin-top:8px; } .identity-tag { max-width:100%; white-space:normal; overflow-wrap:anywhere; text-align:left; border-color:var(--border); } .identity-tag.highlight { background:var(--secondary); color:var(--primary); border-color:transparent; }
.status-field { display:grid; gap:8px; flex:0 0 144px; font-size:12px; color:var(--muted-foreground); } .status-field>span:first-child { display:flex; align-items:center; gap:8px; }
.status-field :deep([data-slot='native-select-wrapper']) { width:100%; } .status-field :deep(select) { min-height:44px; font-size:14px; color:var(--foreground); background:var(--card); }
.status-dot { width:8px; height:8px; border-radius:50%; background:var(--muted-foreground); } .status-dot[data-status='AVAILABLE'] { background:var(--primary); }
.profile-stats { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); border-top:1px solid var(--border); padding:16px 0; }
.profile-stats>div { display:flex; flex-direction:column; gap:4px; padding:0 16px; min-width:0; } .profile-stats>div:first-child { padding-left:0; } .profile-stats>div+div { border-left:1px solid var(--border); }
.profile-stats dt { color:var(--muted-foreground); font-size:12px; } .profile-stats dd { order:-1; font-size:24px; line-height:1.35; font-weight:600; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
.mine-columns { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; align-items:start; } .mine-column { display:grid; gap:16px; min-width:0; }
.deposit-primary { flex-wrap:wrap; } .deposit-amount { margin-top:4px; font-size:28px; line-height:1.35; font-weight:600; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; } .deposit-amount span { font-size:14px; font-weight:400; color:var(--muted-foreground); }
.deposit-breakdown { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; padding-top:16px; border-top:1px solid var(--border); } .deposit-breakdown div { display:grid; gap:4px; min-width:0; } .deposit-breakdown dt { color:var(--muted-foreground); font-size:12px; } .deposit-breakdown dd { font-size:14px; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
.deposit-note { padding:12px; border-radius:12px; background:var(--secondary); color:var(--muted-foreground); font-size:13px; line-height:1.6; }
.menu-card { gap:8px; } .menu-card h2 { padding:0 0 4px; } .menu-card nav { display:grid; }
.menu-link { display:flex; align-items:center; justify-content:space-between; gap:12px; width:100%; height:auto; min-height:64px; padding:12px 0; border-radius:8px; text-align:left; white-space:normal; }
.menu-link+.menu-link { border-top:1px solid var(--border); border-top-left-radius:0; border-top-right-radius:0; } .menu-link:hover { background:var(--secondary); } .menu-link:focus-visible { outline:2px solid var(--primary); outline-offset:4px; }
.menu-copy { display:grid; gap:4px; min-width:0; } .menu-label { font-size:14px; font-weight:500; overflow-wrap:anywhere; } .menu-detail { color:var(--muted-foreground); font-size:12px; font-weight:400; overflow-wrap:anywhere; } .menu-link svg { width:16px; height:16px; flex-shrink:0; color:var(--muted-foreground); } .reply-badge { margin-top:4px; max-width:100%; white-space:normal; }
.mine-footer { padding:0 4px; } .mine-footer :deep(button) { color:var(--muted-foreground); }
.status-skeleton { width:144px; height:64px; } .stats-loading { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; padding-bottom:16px; } .stat-skeleton { height:56px; } .section-skeleton { width:80px; height:24px; } .deposit-skeleton { height:112px; }
.mine-page :deep([data-slot='button']), :global(.mine-dialog [data-slot='button']) { min-height:var(--compact-control-height,24px); border:1px solid transparent; background:var(--primary); color:var(--primary-foreground); font-family:inherit; font-size:12px; box-shadow:none; }
.mine-page :deep([data-slot='button'][data-variant='outline']), :global(.mine-dialog [data-slot='button'][data-variant='outline']) { background:var(--card); color:var(--foreground); border-color:var(--border); }
.mine-page :deep([data-slot='button'][data-variant='ghost']) { background:transparent; color:var(--foreground); }
.mine-page :deep([data-slot='button']:hover:not(:disabled)), :global(.mine-dialog [data-slot='button']:hover:not(:disabled)) { filter:brightness(.98); }
.mine-page :deep([data-slot='button'][data-variant='ghost']:hover) { background:var(--secondary); }
.mine-page :deep([data-slot='button']:focus-visible), :global(.mine-dialog [data-slot='button']:focus-visible) { outline:2px solid var(--primary); outline-offset:4px; }
.mine-page :deep([data-slot='badge'][data-variant='outline']) { border-color:var(--border); }
.mine-page :deep(.menu-link+.menu-link) { border-top-color:var(--border); }
:global(.mine-dialog) { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; border-color:var(--border); border-radius:16px; padding:24px; max-height:calc(100dvh - 32px); overflow-y:auto; }
:global(.mine-dialog [data-slot='dialog-close']) { display:grid; place-items:center; width:44px; height:44px; right:8px; top:8px; padding:12px; border:0; border-radius:12px; background:transparent; color:var(--muted-foreground); }
:global(.mine-dialog [data-slot='dialog-title']) { padding-right:32px; font-size:18px; }
@media (max-width:600px) { .mine-page { gap:12px; padding-top:16px; } .mine-header { gap:8px; margin-bottom:4px; } .mine-header>div { min-width:0; } .mine-header>button { flex-shrink:0; } .mine-card { padding:16px; border-radius:16px; } .identity { padding-bottom:0; } .identity-main { flex-wrap:wrap; gap:16px; } .identity-person { flex-basis:100%; gap:12px; } .identity-avatar { width:48px; height:48px; flex-basis:48px; border-radius:16px; font-size:20px; } .name { font-size:20px; } .status-field { width:100%; flex-basis:100%; grid-template-columns:minmax(0,1fr) 144px; align-items:center; } .status-field>.muted { grid-column:1/-1; } .mine-columns { grid-template-columns:minmax(0,1fr); gap:12px; } .mine-column { gap:12px; } .profile-stats>div { padding:0 12px; } .profile-stats dd { font-size:24px; } }
.mine-page { --compact-control-height:24px; }
.status-field :deep(select) { min-height:28px; height:28px; padding:4px 28px 4px 8px; font-size:12px; }
.mine-card { gap:12px; } .identity-main { gap:16px; } .menu-link { padding:8px 0; }
:global(.mine-dialog) { --compact-control-height:24px; padding:16px; }
@media(max-width:600px),(pointer:coarse) { .mine-page { --compact-control-height:44px; } .status-field :deep(select) { min-height:44px; height:44px; font-size:14px; } :global(.mine-dialog) { --compact-control-height:44px; } }
@media (prefers-reduced-motion:reduce) { .mine-page :deep(*) { transition:none; animation:none; } }
</style>
