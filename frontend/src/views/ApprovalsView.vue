<template>
  <AppShell title="待我确认" eyebrow="工作台 / 待我确认">
    <template #actions>
      <button
        class="ui-button ui-button--outline"
        type="button"
        :disabled="loading || decisionRunId !== null"
        @click="loadApprovals"
      >
        <RefreshCw :size="16" aria-hidden="true" />
        {{ loading ? '刷新中…' : '刷新' }}
      </button>
    </template>

    <section class="approval-workbench" aria-labelledby="approval-heading">
      <header class="approval-workbench__header">
        <div>
          <p class="eyebrow">HITL 工作台</p>
          <div class="approval-workbench__title">
            <h2 id="approval-heading">当前待确认</h2>
            <span class="approval-count" :aria-label="`共 ${approvalCount} 项`">{{ approvalCount }}</span>
          </div>
        </div>
        <label v-if="approvals.length > 0" class="approval-filter">
          <span>类型</span>
          <select disabled aria-label="审批类型筛选">
            <option>全部会议操作</option>
          </select>
        </label>
      </header>

      <ErrorState v-if="errorMessage" :message="errorMessage" retryable @retry="loadApprovals" />
      <LoadingState
        v-else-if="loading"
        title="正在加载待确认方案"
        description="正在同步当前账号的全部待确认草案。"
      />

      <div v-else-if="approvals.length > 0" class="approval-list">
        <ApprovalCard
          v-for="item in approvals"
          :key="item.runId"
          :run-id="item.runId"
          :action-type="item.actionType"
          :draft="item.draft"
          :expires-at="item.expiresAt"
          :expired="expired(item)"
          :busy="decisionRunId === item.runId"
          :feedback="feedbackByRun[item.runId] ?? ''"
          @update:feedback="setFeedback(item.runId, $event)"
          @accept="resumeRun(item, 'ACCEPT')"
          @reject="resumeRun(item, 'REJECT')"
          @edit="resumeRun(item, 'EDIT', $event)"
        />
      </div>

      <div v-else class="approval-empty">
        <EmptyState
          title="没有需要确认的草案"
          description="当前账号暂无待确认的会议方案。其他账号的草案不会显示在这里。"
          icon="check"
        >
          <RouterLink class="ui-button ui-button--default" :to="{ name: 'chat' }">
            <Sparkles :size="16" aria-hidden="true" />
            返回智能编排
          </RouterLink>
        </EmptyState>
      </div>
    </section>
  </AppShell>
</template>

<script setup lang="ts">
import { RefreshCw, Sparkles } from '@lucide/vue'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { readHitlDraft } from '@/api/agent-view'
import { ApiError, apiRequest, apiSseRequest, type SseMessage } from '@/api/client'
import type {
  AgentHitlDraft,
  AgentOperationType,
  AgentResumeAction,
  AgentRunRecovery,
  AgentThreadList,
} from '@/api/types'
import ApprovalCard from '@/components/ApprovalCard.vue'
import AppShell from '@/components/AppShell.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'

interface ApprovalViewModel {
  runId: string
  actionType: AgentOperationType
  draft: AgentHitlDraft
  confirmationToken: string
  expiresAt?: string
}

const approvals = ref<ApprovalViewModel[]>([])
const loading = ref(false)
const decisionRunId = ref<string | null>(null)
const errorMessage = ref('')
const feedbackByRun = ref<Record<string, string>>({})
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | null = null

const approvalCount = computed(() => approvals.value.length)

function expired(approval: ApprovalViewModel): boolean {
  if (approval.expiresAt === undefined) return false
  const expiry = Date.parse(approval.expiresAt)
  return Number.isFinite(expiry) && expiry <= now.value
}

function setFeedback(runId: string, feedback: string): void {
  feedbackByRun.value = { ...feedbackByRun.value, [runId]: feedback }
}

function recoveryToApproval(recovery: AgentRunRecovery): ApprovalViewModel | null {
  if (recovery.status !== 'WAITING_CONFIRMATION' || recovery.draft === undefined) return null
  const parsed = readHitlDraft(recovery.draft, recovery.actionType ?? recovery.operationType)
  if (
    parsed === null
    || typeof recovery.confirmationToken !== 'string'
    || recovery.confirmationToken.length === 0
  ) return null
  return {
    runId: recovery.runId,
    actionType: parsed.actionType,
    draft: parsed.draft,
    confirmationToken: recovery.confirmationToken,
    ...(recovery.expiresAt === undefined ? {} : { expiresAt: recovery.expiresAt }),
  }
}

async function loadApprovals(): Promise<void> {
  if (loading.value) return
  loading.value = true
  errorMessage.value = ''
  try {
    const threads = await apiRequest<AgentThreadList>(
      '/agent/threads?page=1&size=100&status=WAITING_CONFIRMATION',
    )
    const recoveries = await Promise.allSettled(
      threads.items.map((thread) => apiRequest<AgentRunRecovery>(
        `/agent/runs/${thread.latestRunId}`,
      )),
    )
    approvals.value = recoveries.flatMap((result) => {
      if (result.status !== 'fulfilled') return []
      const approval = recoveryToApproval(result.value)
      return approval === null ? [] : [approval]
    })
    if (recoveries.some((result) => result.status === 'rejected') && approvals.value.length === 0) {
      errorMessage.value = '待确认任务已找到，但暂时无法恢复草案详情，请稍后重试。'
    }
  } catch (error) {
    approvals.value = []
    errorMessage.value = error instanceof ApiError ? error.message : '无法加载当前账号的待确认任务。'
  } finally {
    loading.value = false
  }
}

async function resumeRun(
  approval: ApprovalViewModel,
  action: AgentResumeAction,
  editedDraft?: { roomId?: number; startAt?: string },
): Promise<void> {
  if (decisionRunId.value !== null || expired(approval)) return
  decisionRunId.value = approval.runId
  errorMessage.value = ''
  try {
    await apiSseRequest(
      `/agent/runs/${approval.runId}/resume`,
      {
        action,
        confirmationToken: approval.confirmationToken,
        ...(editedDraft === undefined ? {} : { editedDraft }),
        ...((feedbackByRun.value[approval.runId] ?? '').trim().length === 0
          ? {}
          : { feedback: feedbackByRun.value[approval.runId]?.trim() }),
      },
      (message) => applyResumeEvent(approval.runId, message),
    )
    feedbackByRun.value = { ...feedbackByRun.value, [approval.runId]: '' }
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '无法提交本次确认决定。'
  } finally {
    decisionRunId.value = null
  }
  await loadApprovals()
}

function applyResumeEvent(runId: string, message: SseMessage): void {
  if (message.event !== 'hitl.required' || typeof message.data !== 'object' || message.data === null) {
    return
  }
  const payload = message.data as Record<string, unknown>
  const parsed = readHitlDraft(payload.draft, payload.actionType ?? payload.operationType)
  const confirmationToken = typeof payload.confirmationToken === 'string'
    ? payload.confirmationToken
    : null
  if (parsed === null || confirmationToken === null) return
  approvals.value = approvals.value.map((approval) => approval.runId === runId
    ? {
        runId,
        actionType: parsed.actionType,
        draft: parsed.draft,
        confirmationToken,
        ...(typeof payload.expiresAt === 'string' ? { expiresAt: payload.expiresAt } : {}),
      }
    : approval)
  now.value = Date.now()
}

onMounted(() => {
  void loadApprovals()
  timer = setInterval(() => { now.value = Date.now() }, 1000)
})

onUnmounted(() => {
  if (timer !== null) clearInterval(timer)
})
</script>
