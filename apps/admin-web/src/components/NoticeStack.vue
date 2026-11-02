<script setup lang="ts">
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { Button } from '@xa/shadcn-vue/button'
import { dismissNotice, interactiveNotices, notices } from '@/utils/notice'
</script>
<template>
  <div class="notice-stack" aria-live="polite"><Alert v-for="item in notices" :key="item.id" :variant="item.type === 'error' ? 'destructive' : 'default'" class="notice"><AlertDescription>{{ item.message }}</AlertDescription><Button variant="ghost" aria-label="关闭提示" @click="dismissNotice(item.id)">×</Button></Alert><Alert v-for="item in interactiveNotices" :key="item.id" class="notice"><button type="button" class="notice-action" @click="item.onClick"><strong>{{ item.title }}</strong><span>{{ item.message }}</span></button><Button variant="ghost" :aria-label="`关闭${item.title}`" @click="item.close">×</Button></Alert></div>
</template>
<style scoped>
.notice-stack { position:fixed; z-index:10000; top:16px; right:16px; width:min(360px,calc(100vw - 32px)); display:grid; gap:8px; pointer-events:none; } .notice { display:flex; align-items:center; justify-content:space-between; gap:8px; background:var(--card); pointer-events:auto; }
.notice-action { display:grid; gap:4px; width:100%; padding:0; border:0; background:transparent; color:inherit; text-align:left; cursor:pointer; } .notice-action span { color:var(--muted-foreground); }
</style>
