<template>
  <div v-bind="$attrs" class="private-image" :role="previewSrcList?.length ? 'button' : undefined" :tabindex="previewSrcList?.length ? 0 : undefined"
    :aria-label="previewSrcList?.length ? label : undefined" :aria-busy="opening || undefined" @click="preview" @keydown.enter.prevent="preview" @keydown.space.prevent="preview">
    <img v-if="!imageFailed" :src="currentSrc" :alt="label" :style="{width:'100%',height:'100%',objectFit:fit || 'cover'}" @load="imageLoading = false" @error="renewThumbnail" />
    <span v-if="imageLoading && !imageFailed" class="private-image-status">图片加载中…</span><span v-if="imageFailed" class="private-image-status">图片加载失败</span>
  </div>
  <span v-if="previewError" class="preview-error" role="alert">图片预览暂时无法打开，请点击图片重试。</span>
  <Dialog v-model:open="visible"><DialogContent class="image-viewer"><DialogHeader><DialogTitle>{{ label }}</DialogTitle><DialogDescription>{{ previewIndex + 1 }} / {{ previewUrls.length }}</DialogDescription></DialogHeader><div class="image-stage"><img :src="previewUrls[previewIndex]" :alt="label" :style="{transform:`scale(${zoom})`}" /></div><DialogFooter><Button variant="outline" :disabled="previewIndex <= 0" @click="previewIndex--; zoom = 1">上一张</Button><Button variant="outline" :disabled="previewIndex >= previewUrls.length - 1" @click="previewIndex++; zoom = 1">下一张</Button><Button variant="outline" :disabled="zoom <= 0.5" @click="zoom = Math.max(0.5, zoom - 0.25)">缩小</Button><Button variant="outline" :disabled="zoom >= 3" @click="zoom = Math.min(3, zoom + 0.25)">放大</Button><Button variant="outline" @click="visible = false">关闭</Button></DialogFooter></DialogContent></Dialog>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { request } from '@/api/request'

defineOptions({ inheritAttrs: false })
const props = defineProps<{
  src: string; previewSrcList?: string[]; previewTeleported?: boolean
  fit?: 'fill' | 'contain' | 'cover' | 'none' | 'scale-down'; label?: string
}>()
const currentSrc = ref(props.src)
const previewUrls = ref<string[]>([])
const previewIndex = ref(0)
const visible = ref(false)
const imageLoading = ref(true), imageFailed = ref(false), zoom = ref(1)
const previewError = ref(false)
let sequence = 0
let triedRenewal = false
const opening = ref(false)
const label = props.label || '查看图片'
watch(() => props.src, src => { ++sequence; currentSrc.value = src; triedRenewal = false; visible.value = false; imageLoading.value = true; imageFailed.value = false; previewError.value = false })
onBeforeUnmount(() => { ++sequence })

async function renewed(url: string) {
  const ticket = url.match(/\/api\/media\/private\/([^/?]+)\/?$/)?.[1]
  if (!ticket) return url
  // The configured admin API may live on a separate origin or path prefix.
  const baseURL = (import.meta.env.VITE_API_BASE || '/api/admin').replace(/\/admin\/?$/, '')
  const response = await request<{ url: string }>({ baseURL, url: '/media/renew/', method: 'POST', data: { ticket }, silent: true })
  return response.data.url
}

async function renewThumbnail() {
  if (triedRenewal) { imageLoading.value = false; imageFailed.value = true; return }
  triedRenewal = true
  const version = sequence
  try {
    const url = await renewed(props.src)
    if (version === sequence) { if (url === currentSrc.value) { imageLoading.value = false; imageFailed.value = true } else currentSrc.value = url }
  } catch { if (version === sequence) { imageLoading.value = false; imageFailed.value = true } }
}

async function preview(event: Event) {
  if (!props.previewSrcList?.length) return
  event.stopPropagation()
  if (opening.value) return
  opening.value = true
  previewError.value = false
  const version = sequence
  try {
    const urls = await Promise.all(props.previewSrcList.map(renewed))
    if (version !== sequence) return
    previewUrls.value = urls
    previewIndex.value = Math.max(0, props.previewSrcList.indexOf(props.src))
    zoom.value = 1
    visible.value = true
  } catch { if (version === sequence) previewError.value = true }
  finally { opening.value = false }
}
</script>

<style scoped>
.private-image { display: inline-block; position:relative; overflow: hidden; vertical-align: middle; }
.private-image[role='button'] { cursor: zoom-in; }
.private-image[role='button']:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.private-image-status { position:absolute; inset:0; display: flex; width: 100%; height: 100%; min-height: 44px; align-items: center; justify-content: center; background: var(--muted); color: var(--muted-foreground); font-size: 13px; }
.preview-error { display:block; color:var(--destructive); font-size:12px; margin-top:4px; }
.image-viewer { max-width:min(960px,calc(100vw - 24px)); }
.image-stage { height: min(65vh, 720px); overflow:auto; display:grid; place-items:center; background:var(--muted); }
.image-stage img { max-width:100%; max-height:100%; object-fit:contain; transform-origin:center; }
</style>
