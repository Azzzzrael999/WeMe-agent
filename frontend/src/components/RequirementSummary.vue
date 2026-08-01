<template>
  <section class="requirement-summary">
    <div class="summary-intro"><span class="summary-icon" aria-hidden="true">◎</span><div><h3>已识别的编排上下文</h3><p>仅展示 Agent 流与草案中已有的结构化业务数据。</p></div></div>
    <dl v-if="draft" class="summary-grid">
      <div><dt>意图</dt><dd>创建会议</dd></div><div><dt>会议主题</dt><dd>{{ draft.title }}</dd></div>
      <div><dt>时间窗口</dt><dd>{{ formatDateTime(draft.startAt) }} — {{ formatDateTime(draft.endAt) }}</dd></div>
      <div><dt>会议室</dt><dd>{{ draft.roomName }}</dd></div>
      <div><dt>必需参会者</dt><dd>{{ names(draft.requiredParticipants) }}</dd></div>
      <div><dt>可选参会者</dt><dd>{{ names(draft.optionalParticipants) }}</dd></div>
    </dl>
    <EmptyState v-else title="等待结构化结果" description="提交需求后，已验证的意图、时间和资源信息会出现在这里。" icon="◎" />
  </section>
</template>
<script setup lang="ts">
import type { AgentDraft, AgentDraftParticipant } from '@/api/types'
import { formatDateTime } from '@/utils/format'
import EmptyState from './EmptyState.vue'
defineProps<{ draft: AgentDraft | null }>()
function names(items: AgentDraftParticipant[]): string { return items.length ? items.map((item) => item.displayName).join('、') : '未提供' }
</script>
