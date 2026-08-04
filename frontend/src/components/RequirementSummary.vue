<template>
  <section class="requirement-summary">
    <div class="summary-intro"><span class="summary-icon" aria-hidden="true">◎</span><div><h3>已识别的编排上下文</h3><p>仅展示 Agent 流与草案中已有的结构化业务数据。</p></div></div>
    <HitlDraftSummary v-if="draft && actionType" :action-type="actionType" :draft="draft" />
    <EmptyState v-else title="等待结构化结果" description="提交需求后，已验证的意图、时间和资源信息会出现在这里。" icon="◎" />
  </section>
</template>
<script setup lang="ts">
import type { AgentHitlDraft, AgentOperationType } from '@/api/types'
import EmptyState from './EmptyState.vue'
import HitlDraftSummary from './HitlDraftSummary.vue'
defineProps<{ actionType: AgentOperationType | null; draft: AgentHitlDraft | null }>()
</script>
