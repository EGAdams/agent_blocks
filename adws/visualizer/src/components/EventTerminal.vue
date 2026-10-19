<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import type { EventRow } from '../lib/types'
import { LogViewer } from './log-viewer/log-viewer.component'
import { eventLogs } from './log-viewer/event-logs'

const props = defineProps<{ events: EventRow[] }>()
const host = ref<HTMLElement | null>(null)
let viewer: LogViewer | undefined

function update() {
  if (viewer) {
    viewer.logs = eventLogs(props.events)
    const sessionId = props.events[0]?.adw_id
    viewer.pendingQuestionUrl = sessionId ? `/api/sessions/${encodeURIComponent(sessionId)}/pending-question` : ''
  }
}

onMounted(() => {
  viewer = document.createElement('adw-log-viewer') as LogViewer
  host.value!.append(viewer)
  update()
})

watch(() => props.events, update)
</script>

<template>
  <div ref="host" class="event-terminal" />
</template>

<style scoped>
.event-terminal { min-width: 0; }
</style>
