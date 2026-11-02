<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Button } from '@xa/shadcn-vue/button'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { Badge } from '@xa/shadcn-vue/badge'
import { Switch } from '@xa/shadcn-vue/switch'
import { Card } from '@xa/shadcn-vue/card'
import { Input } from '@xa/shadcn-vue/input'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { api } from '../lib/session'

type Asset = 'avatar' | 'intro_video' | 'voice_card' | 'cheat_proof'
type Profile = { display_name:string; gender:string; bio:string; city:string; service_area:string; avatar_url:string; intro_video_url:string; voice_card_url:string; cheat_proof_url:string; qualifications:{id:number;label:string;is_active:boolean}[]; dispatch_notification_options:{key:string;label:string;eligible:boolean;enabled:boolean}[] }
type Slot = { weekday:number; start_minute:number; end_minute:number }
const days = ['周一','周二','周三','周四','周五','周六','周日'], segments = [{label:'00:00–06:00',start_minute:0,end_minute:360},{label:'06:00–12:00',start_minute:360,end_minute:720},{label:'12:00–18:00',start_minute:720,end_minute:1080},{label:'18:00–24:00',start_minute:1080,end_minute:1440}]
const assets:{key:Asset;label:string;accept:string;limit:number}[] = [{key:'avatar',label:'头像',accept:'image/*',limit:5},{key:'intro_video',label:'介绍视频',accept:'video/*',limit:50},{key:'voice_card',label:'语音卡',accept:'.mp3,.m4a,.wav,.aac,.ogg',limit:10},{key:'cheat_proof',label:'查外挂凭证',accept:'image/*',limit:10}]
const router = useRouter(), profile = ref<Profile | null>(null), form = ref({display_name:'',gender:'UNKNOWN',bio:'',city:'',service_area:''}), slots = ref<Slot[]>([])
const loading = ref(true), error = ref(''), saving = ref(false), scheduleBusy = ref(false), notificationBusy = ref(false), uploading = ref('')
type Section = 'details' | 'notification' | 'media' | 'schedule'
const notices = ref<Partial<Record<Section,string>>>({})
const activeQualifications = computed(() => profile.value?.qualifications.filter(item => item.is_active) ?? [])
let sequence = 0, alive = true
async function load() {
  const current = ++sequence; loading.value = true; error.value = ''
  try {
    const [p,s] = await Promise.all([api<Profile>('/users/escorts/me/'),api<Slot[]>('/users/escorts/schedule/')])
    if (!alive || current !== sequence) return
    profile.value = p; slots.value = s; form.value = {display_name:p.display_name,gender:p.gender,bio:p.bio,city:p.city,service_area:p.service_area}
  } catch (cause) { if (alive && current === sequence) error.value = cause instanceof Error ? cause.message : '资料加载失败' }
  finally { if (alive && current === sequence) loading.value = false }
}
async function save() {
  if (saving.value || !form.value.display_name.trim()) return
  saving.value = true; notices.value.details = ''
  try { const data = {...form.value,display_name:form.value.display_name.trim(),bio:form.value.bio.trim(),city:form.value.city.trim(),service_area:form.value.service_area.trim()}; profile.value = await api<Profile>('/users/escorts/me/',{method:'PATCH',data}); notices.value.details = '资料已保存' }
  catch (cause) { notices.value.details = cause instanceof Error ? cause.message : '保存失败' } finally { saving.value = false }
}
function selected(slot:Slot) { return slots.value.some(row => row.weekday === slot.weekday && row.start_minute === slot.start_minute && row.end_minute === slot.end_minute) }
function toggle(slot:Slot) { slots.value = selected(slot) ? slots.value.filter(row => !(row.weekday === slot.weekday && row.start_minute === slot.start_minute && row.end_minute === slot.end_minute)) : [...slots.value,slot] }
async function saveSchedule() {
  if (scheduleBusy.value) return
  scheduleBusy.value = true; notices.value.schedule = ''
  try { slots.value = await api<Slot[]>('/users/escorts/schedule/',{method:'PUT',data:{slots:slots.value}}); notices.value.schedule = '档期已保存' }
  catch (cause) { notices.value.schedule = cause instanceof Error ? cause.message : '保存失败' } finally { scheduleBusy.value = false }
}
async function changeNotification(key:string, enabled:boolean) {
  if (notificationBusy.value) return
  notificationBusy.value = true; notices.value.notification = ''
  try { profile.value = await api<Profile>('/users/escorts/me/',{method:'PATCH',data:{dispatch_notification_preferences:{[key]:enabled}}}); notices.value.notification = '通知设置已保存' }
  catch (cause) { notices.value.notification = cause instanceof Error ? cause.message : '设置失败' }
  finally { notificationBusy.value = false }
}
async function upload(field:Asset, limit:number, event:Event) {
  const input = event.target as HTMLInputElement, file = input.files?.[0]; input.value = ''
  if (!file || uploading.value) return
  if (file.size > limit * 1024 * 1024) { notices.value.media = `文件不能超过 ${limit}MB`; return }
  uploading.value = field; notices.value.media = ''
  try { const data = new FormData(); data.append(field,file); profile.value = await api<Profile>('/users/escorts/me/',{method:'POST',data}); notices.value.media = '上传成功' }
  catch (cause) { notices.value.media = cause instanceof Error ? cause.message : '上传失败' } finally { uploading.value = '' }
}
async function removeVoice() {
  if (uploading.value) return
  uploading.value = 'remove_voice_card'
  try { profile.value = await api<Profile>('/users/escorts/me/',{method:'PATCH',data:{remove_voice_card:true}}); notices.value.media = '语音卡已移除' }
  catch (cause) { notices.value.media = cause instanceof Error ? cause.message : '移除失败' } finally { uploading.value = '' }
}
onMounted(() => { void load() })
onUnmounted(() => { alive = false; sequence++ })
</script>
<template>
  <main class="page stack profile-page">
    <header class="page-heading">
      <div><p class="muted">兴安电竞 · 陪玩端</p><h1>资料与档期</h1></div>
      <Button size="xs" variant="outline" @click="router.push('/mine')">返回个人中心</Button>
    </header>
    <p v-if="loading" role="status" class="muted">正在加载资料与档期…</p>
    <Alert v-else-if="error" role="alert" variant="destructive"><AlertDescription>{{ error }} <Button size="xs" variant="outline" @click="load">重新加载</Button></AlertDescription></Alert>
    <div v-else-if="profile" class="profile-columns">
      <div class="profile-column">
        <Card class="panel profile-details">
          <h2>对外展示资料</h2>
          <form class="details-form" @submit.prevent="save">
            <label>展示昵称<Input v-model="form.display_name" maxlength="50" required /></label>
            <label>性别<NativeSelect v-model="form.gender"><NativeSelectOption value="UNKNOWN">保密</NativeSelectOption><NativeSelectOption value="MALE">男</NativeSelectOption><NativeSelectOption value="FEMALE">女</NativeSelectOption></NativeSelect></label>
            <label>所在城市<Input v-model="form.city" maxlength="50" placeholder="例如杭州" /></label>
            <label>擅长项目<Input v-model="form.service_area" maxlength="255" /></label>
            <label class="wide">个人简介<Textarea v-model="form.bio" maxlength="500" rows="3" /></label>
            <div class="form-actions wide"><Button type="submit" size="xs" :disabled="saving || !form.display_name.trim()">{{ saving ? '保存中…' : '保存资料' }}</Button><span role="status">{{ notices.details }}</span></div>
          </form>
        </Card>
        <Card class="panel">
          <div><h2>资料附件</h2><p class="muted">选择文件后立即上传。</p></div>
          <div class="rows">
            <div v-for="asset in assets" :key="asset.key" class="asset-row">
              <div><strong>{{ asset.label }}</strong><p class="muted">{{ asset.key === 'voice_card' ? 'MP3、M4A、WAV、AAC、OGG' : asset.key === 'intro_video' ? '视频' : '图片' }} · 最大 {{ asset.limit }} MB</p></div>
              <div class="asset-actions">
                <Button v-if="asset.key === 'voice_card' && profile.voice_card_url" size="xs" variant="outline" :disabled="!!uploading" @click="removeVoice">移除语音卡</Button>
                <Button as="label" size="xs" variant="outline" class="file-button" :data-disabled="!!uploading">
                  {{ uploading === asset.key ? '上传中…' : profile[`${asset.key}_url`] ? '更换' : '上传' }}
                  <input type="file" :aria-label="'选择' + asset.label" :accept="asset.accept" :disabled="!!uploading" @change="upload(asset.key,asset.limit,$event)" />
                </Button>
              </div>
              <audio v-if="asset.key === 'voice_card' && profile.voice_card_url" :src="profile.voice_card_url" controls preload="none" aria-label="已上传的语音卡" />
            </div>
          </div>
          <p v-if="notices.media" role="status" class="feedback">{{ notices.media }}</p>
        </Card>
      </div>
      <div class="profile-column">
        <Card class="panel">
          <div><h2>每周接单档期</h2><p class="muted">每格为 6 小时，勾选可接单时段。未设置时不限接单时间。暂停接单请将状态切换为离线。</p></div>
          <table class="schedule">
            <caption class="sr-only">每周接单时段</caption>
            <thead><tr><th scope="col">星期</th><th v-for="segment in segments" :key="segment.start_minute" scope="col"><span>{{ segment.label.split('–')[0] }}</span><span>–{{ segment.label.split('–')[1] }}</span></th></tr></thead>
            <tbody><tr v-for="(day,weekday) in days" :key="day">
              <th scope="row">{{ day }}</th>
              <td v-for="segment in segments" :key="segment.start_minute">
                <Button size="xs" variant="outline" :aria-label="day + ' ' + segment.label" :aria-pressed="selected({weekday,...segment})" :disabled="scheduleBusy" @click="toggle({weekday,start_minute:segment.start_minute,end_minute:segment.end_minute})">
                  <span aria-hidden="true">{{ selected({weekday,...segment}) ? '✓' : '–' }}</span>
                </Button>
              </td>
            </tr></tbody>
          </table>
          <div class="form-actions"><Button size="xs" :disabled="scheduleBusy" @click="saveSchedule">{{ scheduleBusy ? '保存中…' : '保存档期' }}</Button><span role="status">{{ notices.schedule }}</span></div>
        </Card>
        <Card class="panel">
          <div class="section-heading"><h2>我的接单资格</h2><Badge variant="secondary">运营认证</Badge></div>
          <p class="muted">认证、参考时价与接单资格由运营后台维护。同一范围可接此档及以下，不同游戏、方向、平台和难度独立认证。</p>
          <ul v-if="activeQualifications.length" class="qualifications"><li v-for="item in activeQualifications" :key="item.id">{{ item.label }}</li></ul>
          <p v-else class="muted">资格待配置，请联系运营。</p>
        </Card>
        <Card class="panel">
          <div><h2>护航派单通知</h2><p class="muted">开关即时生效。关闭仅停止此类新单提醒，仍可在接单池查看并主动接单。</p></div>
          <div class="rows"><label v-for="item in profile.dispatch_notification_options" :key="item.key" class="notification-row" :for="'notice-' + item.key">
            <span>{{ item.label }} <small v-if="!item.eligible" class="muted">尚未认证</small></span>
            <Switch :id="'notice-' + item.key" :aria-label="item.label" :model-value="item.eligible && item.enabled" :disabled="!item.eligible || notificationBusy" @update:model-value="changeNotification(item.key,$event)" />
          </label></div>
          <p v-if="notices.notification" role="status" class="feedback">{{ notices.notification }}</p>
        </Card>
      </div>
    </div>
  </main>
</template>
<style scoped>
.profile-page { --target:24px; --field:28px; gap:12px; }
.page-heading,.section-heading,.form-actions,.asset-actions,.notification-row { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:8px; }
.page-heading { margin-bottom:4px; }
.profile-columns,.profile-column { display:grid; align-content:start; gap:12px; min-width:0; }
@media(min-width:840px) { .profile-columns { grid-template-columns:minmax(0,1fr) minmax(0,1fr); align-items:start; } }
h1,h2,p { margin:0; overflow-wrap:anywhere; }
h1 { margin-top:4px; font-size:24px; letter-spacing:-.5px; line-height:1.3; }
h2 { font-size:16px; font-weight:600; line-height:1.5; }
h2 + p { margin-top:4px; }
.profile-page .panel { min-width:0; gap:12px; padding:16px; box-shadow:0 1px 4px rgb(15 23 42 / 3%); }
.profile-details { background:linear-gradient(120deg,var(--secondary),var(--card) 44%); }
.details-form { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
.details-form label { display:grid; align-content:start; gap:4px; min-width:0; font-size:12px; color:var(--muted-foreground); }
.wide { grid-column:1/-1; }
.details-form :deep([data-slot='native-select-wrapper']) { width:100%; }
.details-form :deep(input),.details-form :deep(select),.details-form :deep(textarea) { min-width:0; width:100%; min-height:var(--field); border:1px solid var(--input); color:var(--foreground); background:var(--card); padding:4px 8px; font:inherit; font-size:13px; }
.details-form :deep(input),.details-form :deep(select) { height:var(--field); }
.details-form :deep(select) { padding-right:32px; }
.details-form :deep(textarea) { min-height:64px; resize:vertical; }
.form-actions,.asset-actions { justify-content:flex-start; }
.form-actions span,.feedback { color:var(--foreground); font-size:13px; overflow-wrap:anywhere; }
.asset-row { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:8px; padding:8px 0; }
.asset-row + .asset-row,.notification-row + .notification-row { border-top:1px solid var(--border); }
.asset-row strong,.notification-row { font-size:14px; font-weight:500; }
.asset-row audio { grid-column:1/-1; max-width:100%; height:32px; }
.asset-actions { justify-content:flex-end; }
.file-button { position:relative; overflow:hidden; cursor:pointer; }
.file-button input { position:absolute; inset:0; width:100%; height:100%; opacity:0; cursor:pointer; }
.file-button[data-disabled=true] { opacity:.5; }
.file-button:focus-within,.notification-row:focus-within { outline:2px solid var(--primary); outline-offset:2px; }
.schedule { table-layout:fixed; width:100%; border-spacing:4px; font-variant-numeric:tabular-nums; margin:-4px 0; }
.schedule th { font-size:12px; line-height:16px; color:var(--muted-foreground); font-weight:500; }
.schedule th:first-child { width:32px; text-align:left; }
.schedule th span { display:inline-block; }
.schedule td { padding:0; }
.schedule td :deep(button) { width:100%; }
.schedule td span { font-size:16px; line-height:1; }
.profile-page :deep([data-slot='button']) { min-height:var(--target); padding:0 8px; border:1px solid transparent; background:var(--primary); color:var(--primary-foreground); font:inherit; font-size:12px; }
.profile-page :deep([data-slot='button'][data-variant='outline']) { background:var(--card); border-color:var(--border); color:var(--foreground); }
.profile-page .schedule :deep([aria-pressed='true']) { background:var(--secondary); color:var(--primary); border-color:var(--primary); }
.profile-page .schedule :deep([aria-pressed='false']) { color:var(--muted-foreground); }
.qualifications { list-style:none; display:grid; gap:4px; padding:0; margin:0; }
.qualifications li { padding:8px; border-left:2px solid var(--primary); background:var(--secondary); font-size:13px; overflow-wrap:anywhere; }
.notification-row { min-height:var(--target); flex-wrap:nowrap; padding:4px; cursor:pointer; }
.notification-row small { margin-left:4px; font-size:12px; }
.notification-row :deep([data-slot='switch']) { width:36px; min-width:36px; height:20px; min-height:20px; padding:0; border:1px solid transparent; border-radius:20px; }
@media (pointer:coarse),(max-width:600px) {
  .profile-page { --target:44px; --field:44px; }
  .profile-page :deep([data-slot='button']) { min-width:44px; }
  .notification-row { min-height:44px; }
  .details-form :deep(input),.details-form :deep(select),.details-form :deep(textarea) { font-size:16px; }
}
@media(max-width:440px) {
  .profile-page .panel { padding:12px; }
  .asset-actions { max-width:112px; }
}
</style>
