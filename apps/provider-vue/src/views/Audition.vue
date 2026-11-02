<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import { Badge } from '@xa/shadcn-vue/badge'
import { Skeleton } from '@xa/shadcn-vue/skeleton'
import { Input } from '@xa/shadcn-vue/input'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { accessToken, api, persistLogin, sessionRevision } from '../lib/session'

type Info = {id:number;title:string;remark:string;expire_at:string | null;is_active:boolean;role:'provider'}
type Signup = {id:number;link:number;link_title:string;contact:string;game:string;status:string;status_display:string;audit_remark:string;remark?:string;audited_at?:string|null;created_at?:string}
type Exchange = {token:string;userInfo:{id:string;username?:string;nickname?:string;role?:string;phone?:string}}
const route = useRoute(), router = useRouter(), token = String(route.query.token || ''), role = String(route.query.role || 'provider')
const info = ref<Info | null>(null), rows = ref<Signup[]>([]), loading = ref(true), busy = ref(false), error = ref(''), notice = ref(''), contact = ref(''), game = ref(''), remark = ref(''), logged = ref(!!accessToken())
const currentSignup = computed(() => rows.value.find(row => row.link === info.value?.id))
let alive = true
async function anonymous<T>(path:string, data?:unknown):Promise<T> {
  const response = await fetch('/api'+path,{method:data ? 'POST':'GET',headers:data ? {'Content-Type':'application/json'}:undefined,body:data ? JSON.stringify(data):undefined})
  const body = await response.json() as {code:number;msg?:string;data?:T}
  if (!response.ok || body.code !== 0 || !body.data) throw new Error(body.msg || '链接已失效')
  return body.data
}
async function load() {
  loading.value = true; error.value = ''
  if (!token || role !== 'provider') { error.value = '链接参数缺失或无效'; loading.value = false; return }
  try {
    info.value = await anonymous<Info>(`/audition/info?token=${encodeURIComponent(token)}&role=provider`)
    if (!accessToken()) {
      const revision = sessionRevision()
      try { const data = await anonymous<Exchange>('/audition/exchange',{token,role:'provider'}); if (alive && revision === sessionRevision() && !accessToken()) persistLogin(data) } catch { /* regular links require an existing provider login */ }
    }
    logged.value = !!accessToken()
    if (logged.value) rows.value = await api<Signup[]>('/audition/my-signups')
  } catch (cause) { if (alive) error.value = cause instanceof Error ? cause.message : '网络异常，请稍后再试' }
  finally { if (alive) loading.value = false }
}
function login() { void router.push({path:'/login',query:{redirect:`/audition?token=${encodeURIComponent(token)}&role=provider`}}) }
async function submit() {
  if (!logged.value) { login(); return }
  if (busy.value || !contact.value.trim() || !game.value.trim()) return
  busy.value = true; notice.value = ''
  try { await api('/audition/signup',{method:'POST',data:{token,contact:contact.value.trim(),game:game.value.trim(),remark:remark.value.trim()}}); rows.value = await api<Signup[]>('/audition/my-signups'); notice.value = '报名已提交' }
  catch (cause) { const failure = cause as {status?:number;message?:string}; notice.value = failure.status && failure.status < 500 ? failure.message || '报名失败' : '报名结果未确认，请刷新记录核对'; try { rows.value = await api<Signup[]>('/audition/my-signups') } catch {} }
  finally { busy.value = false }
}
onMounted(() => { void load() }); onUnmounted(() => { alive = false })
</script>
<template>
  <main class="page stack audition-page">
    <header class="audition-header"><div><p class="muted">兴安电竞 · 陪玩端</p><h1>试音报名</h1></div><Button size="xs" variant="outline" @click="router.push(logged ? '/mine' : '/login')">返回</Button></header>
    <Skeleton v-if="loading" class="loading-card" aria-label="正在加载试音活动" />
    <Alert v-else-if="error" variant="destructive" role="alert"><AlertTitle>无法打开试音链接</AlertTitle><AlertDescription>{{ error }} <Button size="xs" variant="outline" @click="load">重试</Button></AlertDescription></Alert>
    <template v-else-if="info">
      <Card class="panel activity">
        <div class="section-heading"><div><p class="caption">陪玩招募</p><h2>{{ info.title }}</h2></div></div>
        <p v-if="info.remark" class="activity-description">{{ info.remark }}</p>
        <div class="deadline"><span>报名截止</span><strong>{{ info.expire_at ? new Date(info.expire_at).toLocaleString('zh-CN',{hour12:false}) : '长期有效' }}</strong></div>
      </Card>
      <Alert v-if="notice" role="status"><AlertDescription>{{ notice }}</AlertDescription></Alert>
      <div class="audition-columns">
        <Card v-if="!logged" class="panel"><h2>登录后报名</h2><p class="muted">请先使用陪玩账号登录。</p><div><Button size="xs" @click="login">去登录</Button></div></Card>
        <Card v-else-if="currentSignup" class="panel current-signup">
          <div class="section-heading"><h2>本次报名</h2><Badge :variant="currentSignup.status === 'APPROVED' ? 'secondary' : 'outline'">{{ currentSignup.status_display }}</Badge></div>
          <p v-if="currentSignup.status === 'PENDING'" class="muted">报名已提交，等待运营审核。</p>
          <dl v-if="currentSignup.audit_remark" class="audit-note"><dt>审核备注</dt><dd>{{ currentSignup.audit_remark }}</dd></dl>
        </Card>
        <Card v-else class="panel form-panel">
          <h2>提交报名信息</h2>
          <form class="signup-form" @submit.prevent="submit">
            <label class="field">联系方式<Input v-model="contact" maxlength="100" placeholder="微信号或手机号" :disabled="busy" /></label>
            <label class="field">擅长游戏<Input v-model="game" maxlength="100" placeholder="填写擅长的游戏" :disabled="busy" /></label>
            <label class="field">补充说明（选填）<Textarea v-model="remark" maxlength="255" :disabled="busy" /></label>
            <div><Button size="xs" type="submit" :disabled="busy || !contact.trim() || !game.trim()">{{ busy ? '提交中…' : '提交报名' }}</Button></div>
          </form>
        </Card>
        <Card v-if="logged" class="panel history">
          <h2>我的报名记录</h2><p v-if="!rows.length" class="muted">暂无报名记录</p>
          <article v-for="item in rows" :key="item.id" class="record">
            <div class="section-heading"><h3>{{ item.link_title }}</h3><Badge :variant="item.status === 'APPROVED' ? 'secondary' : 'outline'">{{ item.status_display }}</Badge></div>
            <dl class="record-fields"><div><dt>擅长游戏</dt><dd>{{ item.game || '—' }}</dd></div><div><dt>联系方式</dt><dd>{{ item.contact || '—' }}</dd></div><div v-if="item.remark"><dt>补充说明</dt><dd>{{ item.remark }}</dd></div><div v-if="item.audit_remark"><dt>审核备注</dt><dd>{{ item.audit_remark }}</dd></div></dl>
          </article>
        </Card>
      </div>
    </template>
  </main>
</template>
<style scoped>
.audition-page { --compact-control-height:24px; gap:16px; }
.audition-header,.section-heading,.deadline { display:flex; align-items:center; justify-content:space-between; gap:12px; } .audition-header { margin-bottom:8px; }
h1,h2,h3,p,dl,dd { margin:0; overflow-wrap:anywhere; } h1 { margin-top:4px; font-size:24px; line-height:1.35; letter-spacing:-.5px; } h2 { font-size:16px; line-height:1.5; } h3 { font-size:14px; line-height:1.5; }
.audition-page .panel { gap:12px; padding:16px; min-width:0; border-color:var(--border); border-radius:16px; box-shadow:0 1px 4px rgb(15 23 42 / 3%); } .activity h2 { margin-top:4px; font-size:20px; }
.caption,dt,.deadline span { color:var(--muted-foreground); font-size:12px; } .activity-description,.audit-note dd,.record-fields dd { white-space:pre-wrap; }
.deadline { flex-wrap:wrap; justify-content:start; gap:8px; padding-top:12px; border-top:1px solid var(--border); } .deadline strong { font-size:14px; }
.audition-columns { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); align-items:start; gap:16px; } .signup-form,.record-fields { display:grid; gap:12px; } .field { display:grid; gap:4px; font-size:12px; }
.record,.audit-note { padding-top:12px; border-top:1px solid var(--border); } .record-fields { gap:8px; margin-top:8px; } .record-fields>div { display:grid; grid-template-columns:64px minmax(0,1fr); gap:8px; } .record-fields dd,.audit-note dd { font-size:14px; } .audit-note dd { margin-top:4px; }
.section-heading { align-items:start; } .section-heading :deep([data-slot='badge']) { border-color:var(--border); flex-shrink:0; }
.audition-page :deep([data-slot='button']) { min-height:var(--compact-control-height); border:1px solid transparent; background:var(--primary); color:var(--primary-foreground); font:inherit; font-size:12px; box-shadow:none; } .audition-page :deep([data-slot='button'][data-variant='outline']) { background:var(--card); color:var(--foreground); border-color:var(--border); } .audition-page :deep([data-slot='button']:focus-visible) { outline:2px solid var(--primary); outline-offset:4px; }
.audition-page :deep(input),.audition-page :deep(textarea) { height:28px; min-height:28px; padding:4px 8px; font-size:12px; } .audition-page :deep(textarea) { min-height:64px; resize:vertical; }
.audition-page :deep([data-slot='alert-description']) { display:flex; align-items:center; flex-wrap:wrap; gap:8px; } .loading-card { height:120px; border-radius:16px; }
@media(max-width:700px) { .audition-page { gap:12px; } .audition-columns { grid-template-columns:minmax(0,1fr); gap:12px; } .audition-header { margin-bottom:4px; } }
@media(max-width:600px),(pointer:coarse) { .audition-page { --compact-control-height:44px; } .audition-page :deep([data-slot='button']) { min-width:44px; } .audition-page :deep(input) { min-height:44px; height:44px; font-size:14px; } .audition-page :deep(textarea) { font-size:14px; } }
@media(prefers-reduced-motion:reduce) { .audition-page :deep(*) { transition:none; animation:none; } }
</style>
