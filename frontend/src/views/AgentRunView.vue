<template>
  <AppShell title="Agent Trace">
    <section class="content-panel trace-header" aria-labelledby="trace-title">
      <div class="section-heading compact-heading">
        <div>
          <p class="eyebrow">安全恢复视图</p>
          <h2 id="trace-title">{{ runId }}</h2>
          <p class="muted">此页面只使用 Java 公共 API 返回的脱敏 Run、Step 与 Tool 摘要。</p>
        </div>
        <button class="secondary-button" type="button" :disabled="loading" @click="loadData">
          {{ loading ? '刷新中…' : '刷新' }}
        </button>
      </div>

      <p v-if="errorMessage" class="error-message" role="alert">{{ errorMessage }}</p>
      <div v-else-if="loading && run === null" class="status-message">正在加载 Run 和 Trace…</div>

      <dl v-else-if="run" class="trace-run-facts">
        <div>
          <dt>状态</dt>
          <dd><span class="badge" :class="statusClass(run.status)">{{ run.status }}</span></dd>
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

    <section v-if="run?.status === 'WAITING_CONFIRMATION' && run.draft" class="content-panel recovery-panel">
      <div class="section-heading compact-heading">
        <div>
          <h2>存在待确认草案</h2>
          <p class="muted">确认令牌仅保留在当前已鉴权会话内，此 Trace 页面不会显示它。</p>
        </div>
        <RouterLink class="primary-link" :to="{ name: 'chat', query: { runId } }">继续确认</RouterLink>
      </div>
      <dl class="draft-facts">
        <div><dt>会议</dt><dd>{{ run.draft.title }}</dd></div>
        <div><dt>会议室</dt><dd>{{ run.draft.roomName }}</dd></div>
        <div><dt>开始</dt><dd>{{ formatDateTime(run.draft.startAt) }}</dd></div>
        <div><dt>结束</dt><dd>{{ formatDateTime(run.draft.endAt) }}</dd></div>
      </dl>
      <p v-if="run.candidates?.length" class="muted">当前恢复视图包含 {{ run.candidates.length }} 个已验证候选；请在聊天页选择或编辑后重新校验。</p>
    </section>

    <AgentTimeline :steps="trace?.steps ?? []" :tools="trace?.toolCalls ?? []" />
  </AppShell>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { ApiError, apiRequest } from '../api/client'
import type { AgentRunRecovery, AgentTrace } from '../api/types'
import AgentTimeline from '../components/AgentTimeline.vue'
import AppShell from '../components/AppShell.vue'
import { formatDateTime, formatDuration } from '../utils/format'

const route = useRoute()
const runId = typeof route.params.runId === 'string' ? route.params.runId : ''

const run = ref<AgentRunRecovery | null>(null)
const trace = ref<AgentTrace | null>(null)
const loading = ref(false)
const errorMessage = ref('')

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
    run.value = nextRun
    trace.value = nextTrace
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '无法加载 Agent Trace。'
  } finally {
    loading.value = false
  }
}

function statusClass(status: string): string {
  if (status === 'FAILED' || status === 'CONFLICT' || status === 'CANCELLED') {
    return 'badge-danger'
  }
  if (status.startsWith('WAITING') || status === 'PENDING') {
    return 'badge-warning'
  }
  return 'badge-success'
}

onMounted(() => {
  void loadData()
})
</script>
