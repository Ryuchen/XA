<template>
  <el-config-provider size="small">
    <router-view />
  </el-config-provider>
</template>

<script setup lang="ts">
import { watch, onUnmounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { adminWs } from '@/utils/ws'

const auth = useAuthStore()
watch(() => auth.token, () => adminWs.connect(), { immediate: true })
onUnmounted(() => adminWs.disconnect())
</script>
