<template>
  <AppShell title="运行记录" description="查看任务进度与安全运行详情。" eyebrow="系统 / 运行记录">
    <template #actions>
      <RouterLink class="ui-button ui-button--outline" :to="{ name: 'chat', query: { runId } }"><ArrowLeft :size="16" aria-hidden="true" />返回编排</RouterLink>
      <button class="icon-button" type="button" :disabled="loading" aria-label="刷新运行记录" @click="loadData"><RefreshCw :size="17" aria-hidden="true" /></button>
    </template>
    <section class="run-page" aria-labelledby="trace-title">
      <header class="run-page__identity">
        <div><p class="eyebrow">任务活动</p><h2 id="trace-title">{{ agentIntentLabel(run?.intent) }}</h2><p>{{ run?.questionSummary ?? '正在加载任务摘要…' }}</p></div>
        <StatusBadge v-if="run" :status="run.status" />
      </header>

      <ErrorState v-if="errorMessage" :message="errorMessage" retryable @retry="loadData" />
      <LoadingState v-else-if="loading && run === null" title="正在加载运行记录" />

      <RunOverview v-else-if="run" :run="run" />
      <AgentTimeline v-if="run" :steps="trace?.steps ?? []" :tools="trace?.toolCalls ?? []" :run-status="run.status" />
    </section>

    <section v-if="run?.status === 'WAITING_CONFIRMATION' && run.draft && recoveryActionType" class="content-panel recovery-panel">
      <div class="section-heading compact-heading">
        <div>
          <h2>存在待确认草案</h2>
          <p class="muted">请返回智能编排完成确认。</p>
        </div>
        <RouterLink class="primary-link" :to="{ name: 'chat', query: { runId } }">继续确认</RouterLink>
      </div>
      <HitlDraftSummary :action-type="recoveryActionType" :draft="run.draft" />
      <p v-if="run.candidates?.length" class="muted">当前恢复视图包含 {{ run.candidates.length }} 个已验证候选；请在聊天页选择或编辑后重新校验。</p>
    </section>

    <section class="content-panel run-timeline-panel"><div class="section-heading"><div><p class="eyebrow">技术详情</p><h2>活动记录</h2><p>可按智能体、工具、循环和错误筛选；点击活动查看安全详情。</p></div></div><TraceTimeline :steps="trace?.steps ?? []" :tools="trace?.toolCalls ?? []" :loops="trace?.loopEvents ?? []" :run="trace?.run ?? run" /></section>
  </AppShell>
</template>

<script setup lang="ts">
import { ArrowLeft, RefreshCw } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { ApiError, apiRequest } from '../api/client'
import { readHitlDraft } from '../api/agent-view'
import type { AgentRunRecovery, AgentTrace } from '../api/types'
import { agentIntentLabel } from '../utils/labels'
import AppShell from '../components/AppShell.vue'
import AgentTimeline from '../components/AgentTimeline.vue'
import ErrorState from '../components/ErrorState.vue'
import LoadingState from '../components/LoadingState.vue'
import StatusBadge from '../components/StatusBadge.vue'
import TraceTimeline from '../components/TraceTimeline.vue'
import RunOverview from '../components/RunOverview.vue'
import HitlDraftSummary from '../components/HitlDraftSummary.vue'

const route = useRoute()
const runId = typeof route.params.runId === 'string' ? route.params.runId : ''

const run = ref<AgentRunRecovery | null>(null)
const trace = ref<AgentTrace | null>(null)
const loading = ref(false)
const errorMessage = ref('')
const recoveryActionType = computed(() => {
  const current = run.value
  if (current?.draft === undefined) {
    return null
  }
  return readHitlDraft(current.draft, current.actionType ?? current.operationType)?.actionType ?? null
})

async function loadData(): Promise<void> {
  if (runId.length === 0 || loading.value) {
    return
  }
  loading.value = true
  errorMessage.value = ''
  try {
    const [nextRun, nextTrace] = await Promise.all([
      apiRequest<AgentRunRecovery>(`/agent/runs/${runId}`),
      apiRequest<AgentTrace>(`/agent/runs/${runId}/trace`),
    ])
    const parsedDraft = nextRun.draft === undefined
      ? null
      : readHitlDraft(nextRun.draft, nextRun.actionType ?? nextRun.operationType)
    run.value = {
      ...nextRun,
      ...(parsedDraft === null ? {} : { actionType: parsedDraft.actionType, draft: parsedDraft.draft }),
    }
    trace.value = nextTrace
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '无法加载 Agent Trace。'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void loadData()
})
</script>
