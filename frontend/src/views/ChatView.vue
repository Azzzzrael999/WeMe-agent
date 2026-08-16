<template>
  <WorkspaceShell>
    <div class="chat-workspace">
      <header class="chat-workspace__header">
        <div>
          <span class="chat-workspace__eyebrow">工作台 / 智能编排</span>
          <div class="chat-workspace__title-row">
            <h1>智能编排</h1>
            <StatusBadge v-if="runId" :status="runStatus || 'RUNNING'" />
          </div>
        </div>
        <div class="chat-workspace__actions">
          <button
            v-if="runId"
            class="ui-button ui-button--outline"
            type="button"
            @click="openOrchestration('execution')"
          >
            <PanelRightOpen :size="17" aria-hidden="true" />编排详情
          </button>
          <button class="ui-button ui-button--default" type="button" :disabled="streaming" @click="resetConversation">
            <Plus :size="17" aria-hidden="true" />新建编排
          </button>
        </div>
      </header>

      <ConversationCanvas
        :history="conversationHistory"
        :submitted-message="submittedMessage"
        :answer-summary="answerSummary"
        :unsat-analysis="unsatAnalysis"
        :citations="citations"
        :run-id="runId"
        :run-status="runStatus"
        :booking-request="bookingRequest"
        :streaming="streaming"
        :recovery-loading="recoveryLoading"
        :error-message="errorMessage"
        @select-example="selectExample"
      />

      <div v-if="!recoveryLoading || runId" class="composer-dock">
        <section
          v-if="(runStatus === 'WAITING_USER_INPUT' || (runStatus === 'FAILED' && requirementBaselineAvailable)) && requirementItems.length > 0"
          class="requirement-progress"
        >
          <header>
            <div>
              <strong>已整理的会议需求</strong>
              <small>{{ runStatus === 'FAILED' ? '上次运行失败，将从这版有效需求创建恢复任务' : '直接补充待确认项，Agent 会在当前任务中继续' }}</small>
            </div>
            <span>第 {{ requirementRevision }} 版</span>
          </header>
          <ul>
            <li v-for="item in requirementItems" :key="item.field">
              <span class="requirement-progress__status" :data-status="item.status">
                {{ requirementStatusLabel(item.status) }}
              </span>
              <span><strong>{{ requirementFieldLabel(item.field) }}</strong><small>{{ item.summary }}</small></span>
            </li>
          </ul>
        </section>
        <button
          v-if="hitlDraft && actionType && confirmationToken"
          class="composer-hitl-notice"
          type="button"
          @click="openOrchestration('requirements')"
        >
          <ShieldCheck :size="18" aria-hidden="true" />
          <span><strong>方案正在等待确认</strong><small>在执行任何业务写入前查看并确认完整草案</small></span>
          <ChevronRight :size="17" aria-hidden="true" />
        </button>
        <RunStatusBar
          :run-id="runId"
          :status="runStatus"
          :loading="recoveryLoading"
          @refresh="runId && loadRecovery(runId)"
          @trace="traceOpen = true"
        />
        <p v-if="replanPrefillLoaded" class="replan-prefill-notice" role="status">
          <Sparkles :size="16" aria-hidden="true" />
          已从异常重排单预填处理要求，尚未发送。你可以先补充允许变化的时间、地点、设备或参会人约束。
        </p>
        <AgentComposer
          v-model="message"
          :disabled="streaming || decisionBusy"
          :streaming="streaming"
          @submit="startRun"
        />
      </div>
    </div>

    <OrchestrationSheet
      :open="orchestrationOpen"
      :initial-tab="orchestrationTab"
      :run-id="runId"
      :run-status="runStatus"
      :candidates="candidates"
      :citations="citations"
      :requirement-items="requirementItems"
      :action-type="actionType"
      :draft="hitlDraft"
      :confirmation-token="confirmationToken"
      :expires-at="expiresAt"
      :feedback="hitlFeedback"
      :busy="decisionBusy || streaming"
      :steps="steps"
      :tools="tools"
      :loops="loopEvents"
      :run="runMetrics"
      @update:open="setOrchestrationOpen"
      @update:feedback="hitlFeedback = $event"
      @accept="resumeRun('ACCEPT')"
      @reject="resumeRun('REJECT')"
      @edit="resumeRun('EDIT', $event)"
      @select-candidate="selectCandidate"
      @trace="traceOpen = true"
      @refresh="runId && loadRecovery(runId)"
    />
    <TraceDrawer v-model:open="traceOpen" :run-id="runId" :steps="steps" :tools="tools" :loops="loopEvents" :run="runMetrics" />
  </WorkspaceShell>
</template>


<script setup lang="ts">
import { ChevronRight, PanelRightOpen, Plus, ShieldCheck, Sparkles } from '@lucide/vue'

import AgentComposer from '../components/AgentComposer.vue'
import ConversationCanvas from '../components/ConversationCanvas.vue'
import OrchestrationSheet from '../components/OrchestrationSheet.vue'
import RunStatusBar from '../components/RunStatusBar.vue'
import StatusBadge from '../components/StatusBadge.vue'
import TraceDrawer from '../components/TraceDrawer.vue'
import WorkspaceShell from '../components/WorkspaceShell.vue'
import { useChatWorkflow } from '../composables/useChatWorkflow'

const {
  actionType,
  answerSummary,
  bookingRequest,
  candidates,
  citations,
  confirmationToken,
  conversationHistory,
  decisionBusy,
  errorMessage,
  expiresAt,
  hitlDraft,
  hitlFeedback,
  loadRecovery,
  loopEvents,
  message,
  openOrchestration,
  orchestrationOpen,
  orchestrationTab,
  recoveryLoading,
  replanPrefillLoaded,
  requirementBaselineAvailable,
  requirementFieldLabel,
  requirementItems,
  requirementRevision,
  requirementStatusLabel,
  resetConversation,
  resumeRun,
  runId,
  runMetrics,
  runStatus,
  selectCandidate,
  selectExample,
  setOrchestrationOpen,
  startRun,
  steps,
  streaming,
  submittedMessage,
  tools,
  traceOpen,
  unsatAnalysis,
} = useChatWorkflow()
</script>
