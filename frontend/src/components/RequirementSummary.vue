<template>
  <section class="requirement-summary">
    <div class="summary-intro"><span class="summary-icon" aria-hidden="true"><ListChecks :size="18" /></span><div><h3>已识别的编排上下文</h3><p>仅展示 Agent 流与草案中已有的结构化业务数据。</p></div></div>
    <dl v-if="items.length" class="requirement-summary__facts">
      <div v-for="item in items" :key="item.field">
        <dt>{{ fieldLabel(item.field) }} <small>{{ statusLabel(item.status) }}</small></dt>
        <dd>{{ item.summary }}</dd>
      </div>
    </dl>
    <HitlDraftSummary v-if="draft && actionType" :action-type="actionType" :draft="draft" />
    <EmptyState v-else title="等待结构化结果" description="提交需求后，已验证的意图、时间和资源信息会出现在这里。" icon="document" />
  </section>
</template>
<script setup lang="ts">
import type { AgentHitlDraft, AgentOperationType, AgentRequirementItem } from '@/api/types'
import { ListChecks } from '@lucide/vue'
import EmptyState from './EmptyState.vue'
import HitlDraftSummary from './HitlDraftSummary.vue'
defineProps<{
  actionType: AgentOperationType | null
  draft: AgentHitlDraft | null
  items: readonly AgentRequirementItem[]
}>()

function fieldLabel(field: string): string {
  return ({
    timeWindow: '时间范围',
    durationMinutes: '会议时长',
    requiredParticipants: '参会人员',
    optionalRequirements: '设备与其他要求',
  } as Record<string, string>)[field] ?? field
}

function statusLabel(status: string): string {
  return ({
    EXPLICIT: '已明确',
    DEFAULTED: '系统补全',
    DIRECTORY_RESOLVED: '通讯录解析',
    INHERITED: '原会议继承',
    MISSING: '待补充',
    AMBIGUOUS: '待确认',
    CONFLICT: '有冲突',
    UNSPECIFIED: '未说明',
    CLOSED: '已结束',
  } as Record<string, string>)[status] ?? status
}
</script>
