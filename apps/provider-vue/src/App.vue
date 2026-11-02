<script setup lang="ts">
import { watch } from 'vue'
import { currentUser } from './lib/session'
import { startRealtime, stopRealtime } from './lib/realtime'
import { startOrderAlerts, stopOrderAlerts } from './lib/orderAlerts'
watch(currentUser, user => { stopOrderAlerts(); stopRealtime(); if (user) { startOrderAlerts(); startRealtime() } }, { immediate: true })
</script>
<template><router-view /></template>
