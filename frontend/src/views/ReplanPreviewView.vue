<template>
  <AppShell
    title="异常重排"
    description="处理会议室失效后的换房与约束变化；所有会议写入仍需人工确认。"
    eyebrow="协作 / 异常重排"
  >
    <template #actions>
      <span class="replan-open-count"><TriangleAlert :size="15" aria-hidden="true" />开放 {{ openCount }}</span>
      <button class="ui-button ui-button--outline" type="button" :disabled="listLoading || detailLoading" @click="refreshAll">
        <RefreshCw :size="16" aria-hidden="true" />刷新事实
      </button>
    </template>

    <section class="replan-toolbar content-panel" aria-label="异常单筛选">
      <label>
        <span>处置状态</span>
        <select v-model="statusFilter" @change="applyStatusFilter">
          <option value="">全部状态</option>
          <option value="OPEN">待处理</option>
          <option value="RESOLVED">已解决</option>
          <option value="RESTORED">资源已恢复</option>
          <option value="CANCELLED">会议已取消</option>
        </select>
      </label>
      <p>快速处理只替换会议室；需要改变时间、参会人、地点或设备时，请进入智能编排。</p>
    </section>

    <p v-if="actionNotice" class="replan-notice" role="status">{{ actionNotice }}</p>
    <p v-if="actionError" class="error-message replan-action-error" role="alert">{{ actionError }}</p>

    <div class="replan-layout">
      <section class="content-panel replan-case-list" aria-labelledby="replan-case-list-title">
        <header class="section-heading">
          <div><h2 id="replan-case-list-title">异常单</h2><p>共 {{ total }} 条符合当前筛选</p></div>
        </header>

        <ErrorState v-if="listError" :message="listError" retryable @retry="loadCases" />
        <div v-else-if="listLoading" class="replan-list-loading" aria-live="polite"><span class="spinner" />正在加载异常单…</div>
        <EmptyState
          v-else-if="cases.length === 0"
          title="当前没有异常单"
          :description="statusFilter === 'OPEN' ? '没有待处理的资源失效事件。' : '可以切换状态查看其他记录。'"
          icon="check"
        />
        <div v-else class="replan-case-buttons">
          <button
            v-for="item in cases"
            :key="item.id"
            type="button"
            :class="['replan-case-button', { 'replan-case-button--active': selectedCaseId === item.id }]"
            @click="selectCase(item.id)"
          >
            <span class="replan-case-button__heading"><strong>{{ item.caseNo }}</strong><StatusBadge :status="item.status" /></span>
            <span>{{ item.currentMeeting.title }}</span>
            <small><DoorClosed :size="13" aria-hidden="true" />{{ item.failedRoom.name }} · {{ formatDateTime(item.originalStartAt) }}</small>
            <small class="replan-case-button__reason">{{ item.failureReason }}</small>
          </button>
        </div>

        <footer v-if="totalPages > 1" class="pagination-bar replan-pagination">
          <span>第 {{ page }} / {{ totalPages }} 页</span>
          <div>
            <button class="ui-button ui-button--outline ui-button--sm" type="button" :disabled="page <= 1" @click="changePage(page - 1)"><ChevronLeft :size="15" aria-hidden="true" />上一页</button>
            <button class="ui-button ui-button--outline ui-button--sm" type="button" :disabled="page >= totalPages" @click="changePage(page + 1)">下一页<ChevronRight :size="15" aria-hidden="true" /></button>
          </div>
        </footer>
      </section>

      <section class="replan-detail-column" aria-live="polite">
        <ErrorState v-if="detailError" :message="detailError" retryable @retry="selectedCaseId && loadDetail(selectedCaseId)" />
        <div v-else-if="detailLoading" class="feedback-state"><span class="spinner" />正在同步异常单与会议事实…</div>
        <EmptyState v-else-if="selectedCase === null" title="选择一张异常单" description="查看资源失效原因、当前会议事实和可验证替代方案。" icon="calendar" />
        <template v-else>
          <article class="content-panel replan-case-detail">
            <header class="replan-detail-heading">
              <div>
                <p class="eyebrow">{{ selectedCase.caseNo }} · Meeting ID {{ selectedCase.meetingId }}</p>
                <h2>{{ selectedCase.currentMeeting.title }}</h2>
              </div>
              <StatusBadge :status="selectedCase.status" />
            </header>

            <div class="replan-fact-grid">
              <section class="replan-fact replan-fact--danger">
                <span><TriangleAlert :size="16" aria-hidden="true" />资源失效</span>
                <strong>{{ selectedCase.failedRoom.name }}</strong>
                <p>{{ selectedCase.failureReason }}</p>
                <small>发现于 {{ formatDateTime(selectedCase.createdAt) }} · 房间事件版本 {{ selectedCase.roomStatusVersion }}</small>
              </section>
              <section class="replan-fact">
                <span><CalendarClock :size="16" aria-hidden="true" />原计划</span>
                <strong>{{ formatRange(selectedCase.originalStartAt, selectedCase.originalEndAt) }}</strong>
                <p>{{ durationLabel(selectedCase.originalStartAt, selectedCase.originalEndAt) }} · {{ selectedCase.currentMeeting.participants.length }} 名参会人</p>
                <small>原会议室 {{ selectedCase.failedRoom.name }}</small>
              </section>
              <section class="replan-fact">
                <span><Database :size="16" aria-hidden="true" />当前事实</span>
                <strong>{{ selectedCase.currentMeeting.roomName }}</strong>
                <p>{{ formatRange(selectedCase.currentMeeting.startAt, selectedCase.currentMeeting.endAt) }}</p>
                <small>会议 {{ selectedCase.currentMeeting.status }} · 版本 {{ selectedCase.currentMeeting.version }}</small>
              </section>
              <section v-if="selectedCase.status !== 'OPEN'" class="replan-fact replan-fact--readonly">
                <span><ShieldCheck :size="16" aria-hidden="true" />处置结果</span>
                <strong>{{ resolutionLabel(selectedCase.resolutionType) }}</strong>
                <p>{{ selectedCase.resolvedStartAt ? formatRange(selectedCase.resolvedStartAt, selectedCase.resolvedEndAt) : '未改变会议时间' }}</p>
                <small>{{ selectedCase.resolvedAt ? `完成于 ${formatDateTime(selectedCase.resolvedAt)}` : '当前记录只读' }}</small>
              </section>
            </div>

            <div class="replan-constraint-grid">
              <section>
                <h3><RefreshCw :size="15" aria-hidden="true" />变化约束</h3>
                <ul><li v-for="item in selectedCase.changedConstraints" :key="item">{{ item }}</li><li v-if="selectedCase.changedConstraints.length === 0">仅资源状态发生变化</li></ul>
              </section>
              <section>
                <h3><LockKeyhole :size="15" aria-hidden="true" />保留约束</h3>
                <ul><li v-for="item in selectedCase.preservedConstraints" :key="item">{{ item }}</li><li v-if="selectedCase.preservedConstraints.length === 0">以当前会议事实为准</li></ul>
              </section>
            </div>
          </article>

          <article v-if="selectedCase.status === 'OPEN'" class="content-panel replan-alternatives">
            <header class="section-heading">
              <div><h2>原时段快速换房</h2><p>只展示通过状态、容量、设备能力和槽位占用硬约束的 Top {{ alternatives?.items.length ?? 0 }}。</p></div>
              <span v-if="alternatives?.sameTime" class="replan-same-time"><Clock3 :size="13" aria-hidden="true" />原时段</span>
            </header>

            <p v-if="alternativesError" class="error-message" role="alert">{{ alternativesError }}</p>
            <div v-if="alternativesLoading" class="replan-list-loading"><span class="spinner" />正在重新验证候选…</div>
            <template v-else-if="alternatives && alternatives.items.length > 0">
              <div class="replan-alternative-list">
                <button
                  v-for="candidate in alternatives.items"
                  :key="candidate.roomId"
                  type="button"
                  :class="['replan-alternative', { 'replan-alternative--selected': selectedAlternative?.roomId === candidate.roomId }]"
                  :aria-pressed="selectedAlternative?.roomId === candidate.roomId"
                  @click="selectedAlternative = candidate"
                >
                  <span class="replan-alternative__heading">
                    <span><strong>{{ candidate.roomName }}</strong><small>{{ candidate.roomCode }} · {{ candidate.building }} {{ candidate.floor }}</small></span>
                    <span class="replan-only-room">仅会议室改变</span>
                  </span>
                  <span class="replan-alternative__meta"><Users :size="14" aria-hidden="true" />容纳 {{ candidate.capacity }} 人 <span v-for="feature in candidate.features" :key="feature.code">{{ feature.name }}</span></span>
                  <span class="replan-hard-evidence">
                    <span><Clock3 :size="13" />时间保持</span>
                    <span><Timer :size="13" />时长保持</span>
                    <span><Users :size="13" />人员保持</span>
                    <span><Wrench :size="13" />设备不降级</span>
                  </span>
                  <small class="replan-alternative__reason">{{ candidate.reason }}</small>
                </button>
              </div>
              <div class="replan-actions">
                <button class="ui-button ui-button--default" type="button" :disabled="selectedAlternative === null" @click="openResolutionConfirm">
                  <CheckCircle2 :size="16" aria-hidden="true" />确认快速换房
                </button>
                <button class="ui-button ui-button--outline" type="button" @click="openSmartReplan"><Sparkles :size="16" aria-hidden="true" />在智能编排中详细处理</button>
              </div>
            </template>
            <div v-else class="replan-no-alternative">
              <DoorClosed :size="24" aria-hidden="true" />
              <div><strong>原时段没有满足全部硬约束的房间</strong><p>系统没有自行放宽时间、人员或设备要求。可进入智能编排查看阻塞证据并明确允许变化的约束。</p></div>
              <button class="ui-button ui-button--default" type="button" @click="openSmartReplan"><Sparkles :size="16" aria-hidden="true" />详细重排</button>
            </div>
          </article>

          <article v-else class="content-panel replan-readonly-callout">
            <ShieldCheck :size="22" aria-hidden="true" />
            <div><h2>此异常单已进入终态</h2><p>页面展示 Java 返回的最新事实，旧候选已失效，不能再次提交。</p></div>
          </article>
        </template>
      </section>
    </div>

    <Teleport to="body">
      <div v-if="confirmingAlternative" class="dialog-layer">
        <button class="drawer-overlay" aria-label="关闭快速换房确认" @click="closeResolutionConfirm" />
        <section class="ui-dialog" role="alertdialog" aria-modal="true" aria-labelledby="replan-confirm-title">
          <header><div><p>最终人工确认</p><h2 id="replan-confirm-title">换到“{{ confirmingAlternative.roomName }}”？</h2></div><button class="icon-button" type="button" aria-label="关闭确认" @click="closeResolutionConfirm"><X :size="18" /></button></header>
          <p>本次只改变会议室。原时间、时长、必需/可选参会人和设备能力保持不变；提交时 Java 会使用会议版本 {{ alternatives?.meetingVersion }} 与异常单版本 {{ alternatives?.caseVersion }} 重新裁决。</p>
          <dl class="replan-confirm-summary">
            <div><dt>失效房间</dt><dd>{{ selectedCase?.failedRoom.name }}</dd></div>
            <div><dt>替代房间</dt><dd>{{ confirmingAlternative.roomName }}</dd></div>
            <div><dt>会议时间</dt><dd>{{ selectedCase ? formatRange(selectedCase.currentMeeting.startAt, selectedCase.currentMeeting.endAt) : '—' }}</dd></div>
          </dl>
          <p v-if="resolveError" class="error-message" role="alert">{{ resolveError }}</p>
          <footer><button class="ui-button ui-button--outline" type="button" :disabled="resolveSubmitting" @click="closeResolutionConfirm">返回检查</button><button class="ui-button ui-button--default" type="button" :disabled="resolveSubmitting" @click="confirmResolution">{{ resolveSubmitting ? '正在重新校验…' : '确认并提交' }}</button></footer>
        </section>
      </div>
    </Teleport>
  </AppShell>
</template>

<script setup lang="ts">
import {
  CalendarClock,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Database,
  DoorClosed,
  LockKeyhole,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Timer,
  TriangleAlert,
  Users,
  Wrench,
  X,
} from '@lucide/vue'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { getReplanAlternatives, getReplanCase, listReplanCases, resolveReplanCase } from '@/api/replan'
import type {
  ReplanAlternative,
  ReplanAlternatives,
  ReplanCase,
  ReplanCaseStatus,
  ReplanResolutionType,
} from '@/api/types'
import AppShell from '@/components/AppShell.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useModalFocus } from '@/composables/useModalFocus'
import { formatDateTime } from '@/utils/format'

const PAGE_SIZE = 12
const STALE_CODES = new Set(['REPLAN_CASE_STATE_CONFLICT', 'REPLAN_CANDIDATE_STALE', 'MEETING_STATE_CONFLICT', 'BOOKING_CONFLICT'])
const route = useRoute()
const router = useRouter()
const cases = ref<ReplanCase[]>([])
const total = ref(0)
const openCount = ref(0)
const page = ref(1)
const statusFilter = ref<'' | ReplanCaseStatus>('OPEN')
const listLoading = ref(true)
const listError = ref('')
const selectedCaseId = ref<number | null>(null)
const selectedCase = ref<ReplanCase | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
const alternatives = ref<ReplanAlternatives | null>(null)
const alternativesLoading = ref(false)
const alternativesError = ref('')
const selectedAlternative = ref<ReplanAlternative | null>(null)
const confirmingAlternative = ref<ReplanAlternative | null>(null)
const resolveSubmitting = ref(false)
const resolveError = ref('')
const actionNotice = ref('')
const actionError = ref('')
let detailEpoch = 0

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const confirmOpen = computed(() => confirmingAlternative.value !== null)
useModalFocus(confirmOpen, closeResolutionConfirm)

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

function routeCaseId(): number | null {
  const raw = route.query.caseId
  if (typeof raw !== 'string' || !/^\d{1,18}$/.test(raw)) return null
  const parsed = Number(raw)
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null
}

function formatRange(startAt: string, endAt: string | null): string {
  return `${formatDateTime(startAt)} — ${formatDateTime(endAt)}`
}

function durationLabel(startAt: string, endAt: string): string {
  const minutes = Math.round((new Date(endAt).getTime() - new Date(startAt).getTime()) / 60000)
  if (!Number.isFinite(minutes) || minutes <= 0) return '固定时长'
  return minutes >= 60 && minutes % 60 === 0 ? `${minutes / 60} 小时` : `${minutes} 分钟`
}

function resolutionLabel(type: ReplanResolutionType | null): string {
  if (type === 'QUICK_ROOM_CHANGE') return '快速换房完成'
  if (type === 'AGENT_RESCHEDULE') return '会议改期完成'
  if (type === 'MEETING_CANCELLED') return '会议已取消'
  if (type === 'RESOURCE_RESTORED') return '原资源已恢复'
  return '已结束'
}

async function loadCases(): Promise<void> {
  listLoading.value = true
  listError.value = ''
  try {
    const result = await listReplanCases({ status: statusFilter.value || undefined, page: page.value, size: PAGE_SIZE })
    cases.value = result.items
    total.value = result.total
    if (statusFilter.value === 'OPEN') openCount.value = result.total
    else await loadOpenCount()
    if (selectedCaseId.value === null && result.items.length > 0 && routeCaseId() === null) {
      await selectCase(result.items[0]!.id)
    }
  } catch (error) {
    listError.value = errorMessage(error, '异常单加载失败，请稍后重试。')
  } finally {
    listLoading.value = false
  }
}

async function loadOpenCount(): Promise<void> {
  try {
    const result = await listReplanCases({ status: 'OPEN', page: 1, size: 1 })
    openCount.value = result.total
  } catch {
    // The visible list/detail error remains the actionable error surface.
  }
}

async function loadDetail(caseId: number): Promise<void> {
  const epoch = ++detailEpoch
  selectedCaseId.value = caseId
  detailLoading.value = true
  detailError.value = ''
  alternativesError.value = ''
  actionError.value = ''
  selectedAlternative.value = null
  alternatives.value = null
  alternativesLoading.value = false
  try {
    const detail = await getReplanCase(caseId)
    if (epoch !== detailEpoch) return
    selectedCase.value = detail
    if (detail.status !== 'OPEN') return
    alternativesLoading.value = true
    try {
      const result = await getReplanAlternatives(caseId, 3)
      if (epoch === detailEpoch) alternatives.value = result
    } catch (error) {
      if (epoch === detailEpoch) alternativesError.value = errorMessage(error, '替代会议室加载失败，请刷新后重试。')
    } finally {
      if (epoch === detailEpoch) alternativesLoading.value = false
    }
  } catch (error) {
    if (epoch === detailEpoch) {
      selectedCase.value = null
      detailError.value = errorMessage(error, '异常单详情加载失败。')
    }
  } finally {
    if (epoch === detailEpoch) detailLoading.value = false
  }
}

async function selectCase(caseId: number): Promise<void> {
  if (routeCaseId() === caseId) {
    await loadDetail(caseId)
    return
  }
  await router.replace({ name: 'replan', query: { caseId: String(caseId) } })
}

function applyStatusFilter(): void {
  page.value = 1
  void loadCases()
}

function changePage(nextPage: number): void {
  page.value = nextPage
  void loadCases()
}

async function refreshAll(): Promise<void> {
  actionError.value = ''
  actionNotice.value = ''
  await Promise.all([loadCases(), selectedCaseId.value === null ? Promise.resolve() : loadDetail(selectedCaseId.value)])
}

function openResolutionConfirm(): void {
  if (selectedAlternative.value === null || alternatives.value === null) return
  resolveError.value = ''
  confirmingAlternative.value = selectedAlternative.value
}

function closeResolutionConfirm(): void {
  if (resolveSubmitting.value) return
  confirmingAlternative.value = null
  resolveError.value = ''
}

async function confirmResolution(): Promise<void> {
  const detail = selectedCase.value
  const candidate = confirmingAlternative.value
  const evidence = alternatives.value
  if (detail === null || candidate === null || evidence === null || resolveSubmitting.value) return
  resolveSubmitting.value = true
  resolveError.value = ''
  actionError.value = ''
  actionNotice.value = ''
  try {
    const resolved = await resolveReplanCase(detail.id, {
      roomId: candidate.roomId,
      expectedMeetingVersion: evidence.meetingVersion,
      expectedCaseVersion: evidence.caseVersion,
    })
    confirmingAlternative.value = null
    selectedAlternative.value = null
    alternatives.value = null
    selectedCase.value = resolved
    actionNotice.value = `异常单 ${resolved.caseNo} 已完成快速换房，页面已同步最新会议事实。`
    await Promise.all([loadCases(), loadOpenCount()])
  } catch (error) {
    const message = errorMessage(error, '快速换房提交失败。')
    if (error instanceof ApiError && STALE_CODES.has(error.code)) {
      confirmingAlternative.value = null
      selectedAlternative.value = null
      actionError.value = `${message} 已自动刷新会议与异常单事实，请重新选择候选。`
      await Promise.all([loadCases(), loadDetail(detail.id)])
    } else {
      resolveError.value = message
    }
  } finally {
    resolveSubmitting.value = false
  }
}

async function openSmartReplan(): Promise<void> {
  const detail = selectedCase.value
  if (detail === null || detail.status !== 'OPEN') return
  const prompt = `请处理异常重排单 ${detail.caseNo}。会议 ID ${detail.meetingId} 的原会议室“${detail.failedRoom.name}”已失效，原因是“${detail.failureReason}”。请先读取我可管理的会议事实；默认保留原会议时长、必需/可选参会人和设备要求，优先保持原时段，排除失效会议室，给出 Top 3，并在任何写入前让我确认。若我改变时间、地点、设备或参会人，请明确列出改变项、保留项和放宽原因，重新求解与验证后再生成 RESCHEDULE HITL 草案。`
  await router.push({ name: 'chat', query: { prefill: prompt, sourceCaseId: String(detail.id) } })
}

watch(
  () => route.query.caseId,
  () => {
    const caseId = routeCaseId()
    if (caseId !== null && caseId !== selectedCaseId.value) void loadDetail(caseId)
  },
)

onMounted(async () => {
  const caseId = routeCaseId()
  if (caseId !== null) void loadDetail(caseId)
  await loadCases()
})
</script>
