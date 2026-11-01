<template>
  <div v-bind="$attrs" class="private-image" :role="previewSrcList?.length ? 'button' : undefined" :tabindex="previewSrcList?.length ? 0 : undefined"
    :aria-label="previewSrcList?.length ? label : undefined" :aria-busy="opening || undefined" @click="preview" @keydown.enter.prevent="preview" @keydown.space.prevent="preview">
    <el-image :src="currentSrc" :fit="fit" :alt="label" style="width: 100%; height: 100%" @error="renewThumbnail">
      <template #placeholder><span class="private-image-status">图片加载中…</span></template>
      <template #error><span class="private-image-status">图片加载失败</span></template>
    </el-image>
    <el-image-viewer v-if="visible" :url-list="previewUrls" :initial-index="previewIndex" teleported @close="visible = false" />
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
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
let sequence = 0
let triedRenewal = false
const opening = ref(false)
const label = props.label || '查看图片'
watch(() => props.src, src => { ++sequence; currentSrc.value = src; triedRenewal = false; visible.value = false })
onBeforeUnmount(() => { ++sequence })

async function renewed(url: string) {
  const ticket = url.match(/\/api\/media\/private\/([^/?]+)\/?$/)?.[1]
  if (!ticket) return url
  // The configured admin API may live on a separate origin or path prefix.
  const baseURL = (import.meta.env.VITE_API_BASE || '/api/admin').replace(/\/admin\/?$/, '')
  const response = await request<{ url: string }>({ baseURL, url: '/media/renew/', method: 'POST', data: { ticket } })
  return response.data.url
}

async function renewThumbnail() {
  if (triedRenewal) return
  triedRenewal = true
  const version = sequence
  try {
    const url = await renewed(props.src)
    if (version === sequence) currentSrc.value = url
  } catch { /* The API interceptor explains permission/network failures. */ }
}

async function preview(event: Event) {
  if (!props.previewSrcList?.length) return
  event.stopPropagation()
  if (opening.value) return
  opening.value = true
  const version = sequence
  try {
    const urls = await Promise.all(props.previewSrcList.map(renewed))
    if (version !== sequence) return
    previewUrls.value = urls
    previewIndex.value = Math.max(0, props.previewSrcList.indexOf(props.src))
    visible.value = true
  } catch { /* Keep the form or chat draft intact; retry remains available. */ }
  finally { opening.value = false }
}
</script>

<style scoped>
.private-image { display: inline-block; overflow: hidden; vertical-align: middle; }
.private-image[role='button'] { cursor: zoom-in; }
.private-image[role='button']:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
.private-image-status { display: flex; width: 100%; height: 100%; min-height: 44px; align-items: center; justify-content: center; background: var(--el-fill-color-light); color: var(--el-text-color-secondary); font-size: 13px; }
</style>
