<template>
  <div class="plan-diff">
    <section class="plan-diff__column"><header><span>原计划</span><StatusBadge status="CONFLICT" label="受影响" /></header><dl><div><dt>会议室</dt><dd>{{ before.room }}</dd></div><div><dt>时间</dt><dd>{{ before.time }}</dd></div><div><dt>约束</dt><dd>{{ before.constraint }}</dd></div></dl></section>
    <div class="plan-diff__arrow" aria-hidden="true"><ArrowRight :size="20" /></div>
    <section class="plan-diff__column plan-diff__column--after"><header><span>新计划</span><StatusBadge status="SUCCESS" label="建议" /></header><dl><div><dt>会议室</dt><dd>{{ after.room }}</dd></div><div><dt>时间</dt><dd>{{ after.time }}</dd></div><div><dt>调整</dt><dd>{{ after.constraint }}</dd></div></dl></section>
  </div>
</template>
<script setup lang="ts">
import { ArrowRight } from '@lucide/vue'
import StatusBadge from './StatusBadge.vue'

defineProps<{
  before: { room: string; time: string; constraint: string }
  after: { room: string; time: string; constraint: string }
}>()
</script>
