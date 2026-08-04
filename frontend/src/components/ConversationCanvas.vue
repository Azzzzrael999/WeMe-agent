<template>
  <section class="conversation-canvas" aria-labelledby="conversation-canvas-title">
    <h2 id="conversation-canvas-title" class="sr-only">MeetOps 智能编排会话</h2>
    <ConversationSidebar :run-id="runId" :status="runStatus" />

    <div ref="scrollRef" class="conversation-canvas__scroll" aria-live="polite">
      <div v-if="!hasConversation" class="conversation-empty">
        <span class="conversation-empty__mark" aria-hidden="true"><Sparkles :size="25" /></span>
        <h2>把会议协调交给 MeetOps</h2>
        <p>用自然语言说明目标。我会查询真实资源、验证硬约束，并在任何写入发生前请你确认。</p>
        <div class="quick-task-grid" aria-label="快捷编排任务">
          <button v-for="task in quickTasks" :key="task.label" type="button" @click="selectExample(task.prompt)">
            <component :is="task.icon" :size="17" aria-hidden="true" />
            <span><strong>{{ task.label }}</strong><small>{{ task.description }}</small></span>
          </button>
        </div>
      </div>

      <div v-else class="conversation-thread">
        <template v-for="turn in history" :key="turn.id">
          <article class="conversation-message conversation-message--user">
            <div><span>你</span><p>{{ turn.question }}</p></div>
          </article>
          <article class="conversation-message conversation-message--assistant">
            <span class="conversation-assistant-mark" aria-hidden="true"><Sparkles :size="15" /></span>
            <div>
              <span>MeetOps</span>
              <p>{{ turn.answer }}</p>
              <footer v-if="turn.runId">
                <StatusBadge :status="turn.status || 'SUCCEEDED'" />
                <RouterLink :to="{ name: 'agent-run', params: { runId: turn.runId } }">查看这次运行 <ArrowUpRight :size="13" aria-hidden="true" /></RouterLink>
              </footer>
            </div>
          </article>
        </template>

        <article v-if="submittedMessage" class="conversation-message conversation-message--user">
          <div><span>你</span><p>{{ submittedMessage }}</p></div>
        </article>
        <article v-if="submittedMessage && (runId || answerSummary || streaming)" class="conversation-message conversation-message--assistant">
          <span class="conversation-assistant-mark" aria-hidden="true"><Sparkles :size="15" /></span>
          <div>
            <span>MeetOps</span>
            <p v-if="answerSummary">{{ answerSummary }}</p>
            <p v-else-if="streaming" class="streaming-copy"><LoaderCircle :size="15" aria-hidden="true" />正在理解需求并查询可验证的业务事实…</p>
            <p v-else>已保存当前 Run，可继续查看结构化编排结果。</p>
            <footer v-if="bookingRequest">
              <StatusBadge :status="bookingRequest.status" />
              <span>请求号 {{ bookingRequest.requestNo }}</span>
            </footer>
          </div>
        </article>

        <LoadingState v-if="recoveryLoading && !streaming" title="正在恢复当前任务" description="正在加载 Run、候选与安全运行轨迹。" />
        <ErrorState v-if="errorMessage" :message="errorMessage" />
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ArrowUpRight, Ban, Building2, CalendarPlus, LoaderCircle, RefreshCw, ScrollText, Sparkles, Users } from '@lucide/vue'
import { computed, nextTick, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import type { BookingRequest } from '@/api/types'
import ErrorState from './ErrorState.vue'
import LoadingState from './LoadingState.vue'
import StatusBadge from './StatusBadge.vue'
import ConversationSidebar from './ConversationSidebar.vue'

export interface ConversationCanvasTurn {
  id: string
  runId: string | null
  question: string
  answer: string
  status: string
}

const props = defineProps<{
  history: readonly ConversationCanvasTurn[]
  submittedMessage: string
  answerSummary: string
  runId: string | null
  runStatus: string
  bookingRequest: BookingRequest | null
  streaming: boolean
  recoveryLoading: boolean
  errorMessage: string
}>()

const emit = defineEmits<{ 'select-example': [prompt: string] }>()

const scrollRef = ref<HTMLElement | null>(null)
const hasConversation = computed(() => props.history.length > 0 || props.submittedMessage.length > 0 || props.runId !== null)
const quickTasks = [
  { label: '创建会议', description: '按时间与资源约束生成候选', icon: CalendarPlus, prompt: '下周三下午安排 90 分钟架构评审，要大屏' },
  { label: '多人协调', description: '寻找必需参会者共同空闲', icon: Users, prompt: '帮我找下周三下午张三、李四和王五都有空的 1 小时时间' },
  { label: '推荐会议室', description: '按容量、设备和位置筛选', icon: Building2, prompt: '找一个能坐 10 个人、有视频会议设备的会议室' },
  { label: '查询制度', description: '只回答带可验证依据的政策', icon: ScrollText, prompt: '客户会议能不能使用 VIP 会议室？' },
  { label: '调整安排', description: '先展示 Before / After 草案', icon: RefreshCw, prompt: '把刚才那个会议延长半小时，换一个有白板的会议室' },
  { label: '取消会议', description: '先核对目标并生成取消预览', icon: Ban, prompt: '取消刚才创建的那个会议' },
]

function selectExample(prompt: string): void {
  emit('select-example', prompt)
  void nextTick(() => document.querySelector<HTMLTextAreaElement>('#agent-request')?.focus())
}

watch(
  () => [props.history.length, props.submittedMessage, props.answerSummary, props.streaming],
  async () => {
    await nextTick()
    scrollRef.value?.scrollTo({ top: scrollRef.value.scrollHeight, behavior: 'smooth' })
  },
  { deep: true },
)
</script>
