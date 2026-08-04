<template>
  <AppShell title="待我确认" eyebrow="工作台 / 待我确认">
    <template #actions>
      <button
        v-if="activeRunId"
        class="ui-button ui-button--outline"
        type="button"
        :disabled="loading || decisionBusy"
        @click="loadApproval"
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
          <p>只显示当前浏览器标签页保存且仍可恢复的 Run，不创建跨 Run 任务队列。</p>
        </div>
        <label v-if="approval" class="approval-filter">
          <span>类型</span>
          <select disabled aria-label="审批类型筛选">
            <option>{{ operationLabel }}</option>
          </select>
        </label>
      </header>

      <ErrorState v-if="errorMessage" :message="errorMessage" retryable @retry="loadApproval" />
      <LoadingState v-else-if="loading" title="正在恢复当前 Run" description="仅通过 Java 公共恢复接口读取真实待确认状态。" />

      <ApprovalCard
        v-else-if="approval"
        :run-id="approval.runId"
        :action-type="approval.actionType"
        :draft="approval.draft"
        :expires-at="approval.expiresAt"
        :expired="expired"
        :busy="decisionBusy"
        :feedback="feedback"
        @update:feedback="feedback = $event"
        @accept="resumeRun('ACCEPT')"
        @reject="resumeRun('REJECT')"
        @edit="resumeRun('EDIT', $event)"
      />

      <div v-else class="approval-empty">
        <EmptyState
          title="没有需要确认的草案"
          :description="emptyDescription"
          icon="check"
        >
          <RouterLink class="ui-button ui-button--default" :to="chatTarget">
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
import { RouterLink, useRoute } from 'vue-router'

import { readHitlDraft } from '@/api/agent-view'
import { ApiError, apiRequest, apiSseRequest, type SseMessage } from '@/api/client'
import type {
  AgentHitlDraft,
  AgentOperationType,
  AgentResumeAction,
  AgentRunRecovery,
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

const CHAT_ACTIVE_RUN_STORAGE_KEY = 'meetops.chat-active-run.v1'
const SAFE_RUN_ID = /^[A-Za-z0-9_-]{1,64}$/

const route = useRoute()
const activeRunId = ref<string | null>(null)
const approval = ref<ApprovalViewModel | null>(null)
const loading = ref(false)
const decisionBusy = ref(false)
const errorMessage = ref('')
const feedback = ref('')
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | null = null

const approvalCount = computed(() => approval.value === null ? 0 : 1)
const operationLabel = computed(() => approval.value === null ? '' : ({
  CREATE: '创建会议', RESCHEDULE: '会议改期', CANCEL: '取消会议',
})[approval.value.actionType])
const expired = computed(() => {
  const expiresAt = approval.value?.expiresAt
  if (expiresAt === undefined) return false
  const expiry = Date.parse(expiresAt)
  return Number.isFinite(expiry) && expiry <= now.value
})
const chatTarget = computed(() => activeRunId.value === null
  ? { name: 'chat' as const }
  : { name: 'chat' as const, query: { runId: activeRunId.value } })
const emptyDescription = computed(() => activeRunId.value === null
  ? '当前标签页没有保存可恢复的 Run。请先在智能编排中生成会议草案。'
  : '当前 Run 不处于 WAITING_CONFIRMATION，或服务端没有返回可恢复的真实草案。')

function resolveActiveRunId(): string | null {
  const queryRunId = route.query.runId
  if (typeof queryRunId === 'string' && SAFE_RUN_ID.test(queryRunId)) return queryRunId
  const stored = window.sessionStorage.getItem(CHAT_ACTIVE_RUN_STORAGE_KEY)
  return stored !== null && SAFE_RUN_ID.test(stored) ? stored : null
}

async function loadApproval(): Promise<void> {
  if (loading.value || decisionBusy.value) return
  const runId = resolveActiveRunId()
  activeRunId.value = runId
  approval.value = null
  feedback.value = ''
  errorMessage.value = ''
  if (runId === null) return

  loading.value = true
  try {
    const recovery = await apiRequest<AgentRunRecovery>(`/agent/runs/${runId}`)
    if (recovery.status !== 'WAITING_CONFIRMATION') return
    const parsed = recovery.draft === undefined
      ? null
      : readHitlDraft(recovery.draft, recovery.actionType ?? recovery.operationType)
    if (parsed === null || typeof recovery.confirmationToken !== 'string' || recovery.confirmationToken.length === 0) {
      errorMessage.value = '当前 Run 标记为待确认，但恢复视图缺少可用草案或确认凭据。'
      return
    }
    approval.value = {
      runId: recovery.runId,
      actionType: parsed.actionType,
      draft: parsed.draft,
      confirmationToken: recovery.confirmationToken,
      ...(recovery.expiresAt === undefined ? {} : { expiresAt: recovery.expiresAt }),
    }
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '无法恢复当前待确认 Run。'
  } finally {
    loading.value = false
  }
}

async function resumeRun(
  action: AgentResumeAction,
  editedDraft?: { roomId?: number; startAt?: string },
): Promise<void> {
  const current = approval.value
  if (current === null || decisionBusy.value || expired.value) return

  decisionBusy.value = true
  errorMessage.value = ''
  let pausedAgain = false
  try {
    await apiSseRequest(
      `/agent/runs/${current.runId}/resume`,
      {
        action,
        confirmationToken: current.confirmationToken,
        ...(editedDraft === undefined ? {} : { editedDraft }),
        ...(feedback.value.trim().length === 0 ? {} : { feedback: feedback.value.trim() }),
      },
      (message) => {
        pausedAgain = applyResumeEvent(message) || pausedAgain
      },
    )
    if (!pausedAgain) {
      approval.value = null
      await loadApproval()
    }
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '无法提交本次确认决定。'
  } finally {
    decisionBusy.value = false
  }
}

function applyResumeEvent(message: SseMessage): boolean {
  if (message.event !== 'hitl.required' || typeof message.data !== 'object' || message.data === null) return false
  const payload = message.data as Record<string, unknown>
  const parsed = readHitlDraft(payload.draft, payload.actionType ?? payload.operationType)
  const confirmationToken = typeof payload.confirmationToken === 'string' ? payload.confirmationToken : null
  if (parsed === null || confirmationToken === null || approval.value === null) return false
  approval.value = {
    runId: approval.value.runId,
    actionType: parsed.actionType,
    draft: parsed.draft,
    confirmationToken,
    ...(typeof payload.expiresAt === 'string' ? { expiresAt: payload.expiresAt } : {}),
  }
  feedback.value = ''
  now.value = Date.now()
  return true
}

onMounted(() => {
  void loadApproval()
  timer = setInterval(() => { now.value = Date.now() }, 1000)
})

onUnmounted(() => {
  if (timer !== null) clearInterval(timer)
})
</script>
