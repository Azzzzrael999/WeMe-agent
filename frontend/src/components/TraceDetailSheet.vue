<template>
  <Teleport to="body">
    <div v-if="open && activity" class="drawer-layer">
      <button class="drawer-overlay" type="button" aria-label="关闭活动详情" @click="$emit('update:open', false)" />
      <aside class="trace-detail-sheet" role="dialog" aria-modal="true" aria-labelledby="trace-detail-title">
        <header>
          <div>
            <p>{{ activity.kind }} 活动</p>
            <h2 id="trace-detail-title">{{ activity.title }}</h2>
          </div>
          <button class="icon-button" type="button" aria-label="关闭活动详情" @click="$emit('update:open', false)">
            <X :size="19" aria-hidden="true" />
          </button>
        </header>
        <div class="trace-detail-status">
          <StatusBadge :status="activity.status" />
          <span v-if="activity.durationMs !== null">{{ formatDuration(activity.durationMs) }}</span>
        </div>
        <dl class="trace-detail-facts">
          <div><dt>节点类型</dt><dd>{{ activity.category }}</dd></div>
          <div v-if="activity.createdAt"><dt>发生时间</dt><dd>{{ formatDateTime(activity.createdAt) }}</dd></div>
          <div v-if="activity.riskLevel"><dt>风险级别</dt><dd>{{ activity.riskLevel }}</dd></div>
          <div v-if="activity.errorCode"><dt>错误 / 反馈码</dt><dd>{{ activity.errorCode }}</dd></div>
          <div v-if="activity.idempotencySummary"><dt>幂等键摘要</dt><dd>{{ activity.idempotencySummary }}</dd></div>
        </dl>
        <section v-if="activity.inputSummary">
          <h3>安全输入摘要</h3>
          <p>{{ activity.inputSummary }}</p>
        </section>
        <section>
          <h3>输出摘要</h3>
          <p>{{ activity.outputSummary || activity.summary }}</p>
        </section>
        <section v-if="activity.sanitizedArgs">
          <h3>脱敏参数</h3>
          <pre>{{ formatSanitizedArgs(activity.sanitizedArgs) }}</pre>
        </section>
        <p class="trace-security-note">
          <ShieldCheck :size="16" aria-hidden="true" />
          不展示隐藏推理、完整 Prompt、确认令牌或访问凭据。
        </p>
      </aside>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ShieldCheck, X } from '@lucide/vue'
import { computed } from 'vue'

import { useModalFocus } from '@/composables/useModalFocus'
import { formatDateTime, formatDuration, formatSanitizedArgs } from '@/utils/format'
import StatusBadge from './StatusBadge.vue'

export interface TraceActivity {
  id: string
  kind: 'AGENT' | 'TOOL' | 'LOOP'
  title: string
  category: string
  status: string
  summary: string
  createdAt: string | null
  durationMs: number | null
  errorCode: string | null
  riskLevel: string | null
  inputSummary: string | null
  outputSummary: string | null
  idempotencySummary: string | null
  sanitizedArgs: Record<string, unknown> | null
}

const props = defineProps<{ open: boolean; activity: TraceActivity | null }>()
const emit = defineEmits<{ 'update:open': [value: boolean] }>()
useModalFocus(computed(() => props.open), () => emit('update:open', false))
</script>
