<template>
  <Teleport to="body">
    <div v-if="open" class="drawer-layer orchestration-sheet-layer">
      <button class="drawer-overlay" type="button" aria-label="关闭编排详情" @click="close" />
      <aside class="orchestration-sheet" role="dialog" aria-modal="true" aria-labelledby="orchestration-sheet-title">
        <header class="orchestration-sheet__header">
          <div>
            <span>结构化编排详情</span>
            <h2 id="orchestration-sheet-title">{{ sheetTitle }}</h2>
          </div>
          <button class="icon-button" type="button" aria-label="关闭编排详情" @click="close">
            <X :size="19" aria-hidden="true" />
          </button>
        </header>

        <div class="orchestration-sheet__status">
          <StatusBadge :status="runStatus || 'RUNNING'" />
          <button v-if="runId" class="text-button" type="button" @click="$emit('trace')">查看完整运行过程</button>
        </div>

        <div class="orchestration-tabs" role="tablist" aria-label="编排详情类别">
          <button
            v-for="tab in tabs"
            :key="tab.id"
            type="button"
            role="tab"
            :aria-selected="activeTab === tab.id"
            :class="{ active: activeTab === tab.id }"
            @click="activeTab = tab.id"
          >
            <component :is="tab.icon" :size="16" aria-hidden="true" />
            {{ tab.label }}
            <span v-if="tab.id === 'candidates' && candidates.length">{{ candidates.length }}</span>
          </button>
        </div>

        <div class="orchestration-sheet__body">
          <div v-if="activeTab === 'requirements'" class="orchestration-section">
            <div class="orchestration-section__heading">
              <div><span>需求解析</span><h3>需求与确认草案</h3></div>
              <StatusBadge v-if="actionType" status="PENDING" :label="operationLabel" />
            </div>
            <RequirementSummary :action-type="actionType" :draft="draft" />
            <section v-if="draft && actionType && confirmationToken" class="sheet-hitl-panel" aria-labelledby="sheet-hitl-title">
              <div class="sheet-hitl-panel__heading">
                <span aria-hidden="true"><ShieldCheck :size="18" /></span>
                <div>
                  <h3 id="sheet-hitl-title">等待你的明确确认</h3>
                  <p v-if="expired" class="sheet-hitl-panel__expired">确认已过期，请重新发起编排生成新草案。</p>
                  <p v-else>确认前不会创建、改期或取消正式会议。<template v-if="countdown"> · {{ countdown }} 后过期</template></p>
                </div>
              </div>
              <HitlReviewBar
                :action-type="actionType"
                :draft="draft"
                :expires-at="expiresAt"
                :busy="busy || expired"
                :feedback="feedback"
                @update:feedback="$emit('update:feedback', $event)"
                @accept="$emit('accept')"
                @reject="$emit('reject')"
                @edit="$emit('edit', $event)"
              />
            </section>
          </div>

          <div v-else-if="activeTab === 'candidates'" class="orchestration-section">
            <div class="orchestration-section__heading">
              <div><span>候选方案</span><h3>经过硬约束验证的候选</h3></div>
              <span class="orchestration-section__meta">最多 3 项 · 成本升序</span>
            </div>
            <CandidateComparison :candidates="candidates" :draft="editableDraft" @select="$emit('select-candidate', $event)" />
          </div>

          <div v-else-if="activeTab === 'policy'" class="orchestration-section">
            <div class="orchestration-section__heading">
              <div><span>制度检索</span><h3>政策依据</h3></div>
              <span class="orchestration-section__meta">可核对出处</span>
            </div>
            <PolicyCitations :citations="citations" />
          </div>

          <div v-else class="orchestration-section">
            <div class="orchestration-section__heading">
              <div><span>执行</span><h3>受控执行过程</h3></div>
              <button v-if="runId" class="text-button" type="button" @click="$emit('refresh')">刷新状态</button>
            </div>
            <div v-if="runStatus === 'WAITING_BUSINESS_RESULT'" class="pending-callout">
              <StatusBadge status="WAITING_BUSINESS_RESULT" />
              <div><strong>热门时段正在确认</strong><p>结果确认后会自动更新；如遇冲突，系统会重新规划。</p></div>
            </div>
            <div v-if="steps.length || tools.length" class="execution-summary">
              <div><span>处理步骤</span><strong>{{ steps.length }}</strong></div>
              <div><span>资源查询</span><strong>{{ tools.length }}</strong></div>
              <div><span>校验轮次</span><strong>{{ loops.length }}</strong></div>
            </div>
            <AgentLoopTimeline :events="loops" :run="run" />
          </div>
        </div>
      </aside>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { CalendarRange, ListChecks, ShieldCheck, Workflow, X } from '@lucide/vue'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import { proposedDraft } from '@/api/agent-view'
import type {
  AgentCandidate,
  AgentCitation,
  AgentHitlDraft,
  AgentLoopEvent,
  AgentOperationType,
  AgentRunSummary,
  AgentStepEvent,
  AgentToolEvent,
} from '@/api/types'
import { useModalFocus } from '@/composables/useModalFocus'
import AgentLoopTimeline from './AgentLoopTimeline.vue'
import CandidateComparison from './CandidateComparison.vue'
import HitlReviewBar from './HitlReviewBar.vue'
import PolicyCitations from './PolicyCitations.vue'
import RequirementSummary from './RequirementSummary.vue'
import StatusBadge from './StatusBadge.vue'

type OrchestrationTab = 'requirements' | 'candidates' | 'policy' | 'execution'

const props = defineProps<{
  open: boolean
  initialTab?: OrchestrationTab
  runId: string | null
  runStatus: string
  candidates: readonly AgentCandidate[]
  citations: readonly AgentCitation[]
  actionType: AgentOperationType | null
  draft: AgentHitlDraft | null
  confirmationToken: string | null
  expiresAt?: string
  feedback: string
  busy: boolean
  steps: readonly AgentStepEvent[]
  tools: readonly AgentToolEvent[]
  loops: readonly AgentLoopEvent[]
  run?: Partial<AgentRunSummary> | null
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  'update:feedback': [value: string]
  accept: []
  reject: []
  edit: [changes: { roomId?: number; startAt?: string }]
  'select-candidate': [candidate: AgentCandidate]
  trace: []
  refresh: []
}>()

const activeTab = ref<OrchestrationTab>(props.initialTab ?? 'requirements')
const currentTime = ref(Date.now())
let clockTimer: ReturnType<typeof setInterval> | null = null
const editableDraft = computed(() => proposedDraft(props.draft))
const tabs = [
  { id: 'requirements' as const, label: '需求', icon: ListChecks },
  { id: 'candidates' as const, label: '候选', icon: CalendarRange },
  { id: 'policy' as const, label: '政策', icon: ShieldCheck },
  { id: 'execution' as const, label: '执行', icon: Workflow },
]
const operationLabel = computed(() => props.actionType === null ? '' : ({ CREATE: '创建会议', RESCHEDULE: '改期会议', CANCEL: '取消会议' })[props.actionType])
const sheetTitle = computed(() => {
  if (props.runStatus === 'WAITING_CONFIRMATION') return '确认编排方案'
  if (props.candidates.length > 0) return '候选方案与依据'
  return '本次编排详情'
})
const expiresAtTime = computed(() => props.expiresAt === undefined ? null : Date.parse(props.expiresAt))
const expired = computed(() => expiresAtTime.value !== null && Number.isFinite(expiresAtTime.value) && currentTime.value >= expiresAtTime.value)
const countdown = computed(() => {
  if (expiresAtTime.value === null || !Number.isFinite(expiresAtTime.value) || expired.value) return ''
  const seconds = Math.max(0, Math.floor((expiresAtTime.value - currentTime.value) / 1000))
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return `${minutes}:${String(remainder).padStart(2, '0')}`
})

function close(): void {
  emit('update:open', false)
}

watch(() => props.initialTab, (tab) => {
  if (tab !== undefined) activeTab.value = tab
})

useModalFocus(computed(() => props.open), close)

onMounted(() => {
  clockTimer = setInterval(() => { currentTime.value = Date.now() }, 1000)
})

onUnmounted(() => {
  if (clockTimer !== null) clearInterval(clockTimer)
})
</script>
