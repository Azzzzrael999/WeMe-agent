<template>
  <AppShell title="Agent Run 详情" description="查看脱敏的 Agent 步骤、工具摘要与业务结果。" eyebrow="系统 / Agent Run">
    <template #actions><RouterLink class="ui-button ui-button--outline" :to="{ name: 'chat', query: { runId } }">← 返回智能编排</RouterLink><button class="ui-button ui-button--outline" type="button" :disabled="loading" @click="loadData">{{ loading ? '刷新中…' : '刷新' }}</button></template>
    <section class="content-panel trace-header" aria-labelledby="trace-title">
      <div class="section-heading compact-heading">
        <div>
          <p class="eyebrow">安全恢复视图</p>
          <h2 id="trace-title">{{ runId }}</h2>
          <p class="muted">此页面只使用 Java 公共 API 返回的脱敏 Run、Step 与 Tool 摘要。</p>
        </div>
        <StatusBadge v-if="run" :status="run.status" />
      </div>

      <ErrorState v-if="errorMessage" :message="errorMessage" retryable @retry="loadData" />
      <LoadingState v-else-if="loading && run === null" title="正在加载 Run 和 Trace" />

      <dl v-else-if="run" class="trace-run-facts">
        <div>
          <dt>状态</dt>
          <dd><StatusBadge :status="run.status" /></dd>
        </div>
        <div>
          <dt>意图</dt>
          <dd>{{ run.intent ?? '—' }}</dd>
        </div>
        <div>
          <dt>开始时间</dt>
          <dd>{{ formatDateTime(run.createdAt) }}</dd>
        </div>
        <div>
          <dt>总耗时</dt>
          <dd>{{ formatDuration(run.durationMs) }}</dd>
        </div>
        <div>
          <dt>模型调用</dt>
          <dd>{{ run.modelCallCount }}</dd>
        </div>
        <div>
          <dt>工具调用</dt>
          <dd>{{ run.toolCallCount }}</dd>
        </div>
        <div>
          <dt>模型</dt>
          <dd>{{ run.model ?? run.configuredModel ?? '—' }}</dd>
        </div>
        <div>
          <dt>Token（输入 / 输出）</dt>
          <dd>{{ run.inputTokens ?? 0 }} / {{ run.outputTokens ?? 0 }}</dd>
        </div>
      </dl>

      <div v-if="run" class="trace-summaries">
        <div>
          <h3>任务摘要</h3>
          <p>{{ run.questionSummary }}</p>
        </div>
        <div v-if="run.answerSummary">
          <h3>结果摘要</h3>
          <p>{{ run.answerSummary }}</p>
        </div>
      </div>
    </section>

    <section v-if="run?.status === 'WAITING_CONFIRMATION' && run.draft && recoveryActionType" class="content-panel recovery-panel">
      <div class="section-heading compact-heading">
        <div>
          <h2>存在待确认草案</h2>
          <p class="muted">确认令牌仅保留在当前已鉴权会话内，此 Trace 页面不会显示它。</p>
        </div>
        <RouterLink class="primary-link" :to="{ name: 'chat', query: { runId } }">继续确认</RouterLink>
      </div>
      <HitlDraftSummary :action-type="recoveryActionType" :draft="run.draft" />
      <p v-if="run.candidates?.length" class="muted">当前恢复视图包含 {{ run.candidates.length }} 个已验证候选；请在聊天页选择或编辑后重新校验。</p>
    </section>

    <section class="content-panel run-timeline-panel"><div class="section-heading"><div><h2>运行时间线</h2><p>受控 Loop、Agent 节点与确定性 Tool Call 按序展示。</p></div></div><TraceTimeline :steps="trace?.steps ?? []" :tools="trace?.toolCalls ?? []" :loops="trace?.loopEvents ?? []" :run="trace?.run ?? run" /></section>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { ApiError, apiRequest } from '../api/client'
import { readHitlDraft } from '../api/agent-view'
import type { AgentRunRecovery, AgentTrace } from '../api/types'
import AppShell from '../components/AppShell.vue'
import ErrorState from '../components/ErrorState.vue'
import LoadingState from '../components/LoadingState.vue'
import StatusBadge from '../components/StatusBadge.vue'
import TraceTimeline from '../components/TraceTimeline.vue'
import HitlDraftSummary from '../components/HitlDraftSummary.vue'
import { formatDateTime, formatDuration } from '../utils/format'

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
