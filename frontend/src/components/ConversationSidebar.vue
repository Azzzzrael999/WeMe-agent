<template>
  <section class="conversation-context" aria-label="当前编排上下文">
    <div class="conversation-context__identity">
      <span class="conversation-context__mark" aria-hidden="true"><Sparkles :size="15" /></span>
      <div>
        <strong>WeMe 编排助手</strong>
        <small>你的智能企业会议助手</small>
      </div>
    </div>
    <div v-if="runId" class="conversation-context__run">
      <StatusBadge :status="status || 'RUNNING'" />
    </div>
    <span v-else class="conversation-context__ready"><span aria-hidden="true" />已就绪</span>
  </section>
</template>

<script setup lang="ts">
import { Sparkles } from '@lucide/vue'

import StatusBadge from './StatusBadge.vue'

defineProps<{ runId: string | null; status: string }>()
</script>
