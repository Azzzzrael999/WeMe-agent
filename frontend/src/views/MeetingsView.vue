<template>
  <AppShell
    title="我的会议"
    description="查看并安排团队会议。"
    eyebrow="协作 / 我的会议"
  >
    <template #actions>
      <button class="ui-button ui-button--default" type="button" @click="openCreateSheet()">
        <Plus :size="16" aria-hidden="true" />创建会议
      </button>
    </template>

    <section class="calendar-workspace" aria-labelledby="meeting-workspace-title">
      <h2 id="meeting-workspace-title" class="sr-only">会议日历与列表</h2>
      <div class="calendar-toolbar">
        <div class="calendar-toolbar__navigation">
          <button class="ui-button ui-button--outline" type="button" @click="goToday">今天</button>
          <div class="calendar-toolbar__arrows">
            <button class="icon-button" type="button" aria-label="上一日期窗口" @click="moveWindow(-1)">
              <ChevronLeft :size="18" aria-hidden="true" />
            </button>
            <button class="icon-button" type="button" aria-label="下一日期窗口" @click="moveWindow(1)">
              <ChevronRight :size="18" aria-hidden="true" />
            </button>
          </div>
          <div class="calendar-toolbar__title">
            <strong>{{ dateTitle }}</strong>
          </div>
        </div>

        <div class="calendar-toolbar__controls">
          <div class="segmented-control" aria-label="日期范围">
            <button type="button" :class="{ active: windowMode === 'day' }" @click="setWindowMode('day')">日</button>
            <button type="button" :class="{ active: windowMode === 'week' }" @click="setWindowMode('week')">周</button>
          </div>
          <label class="compact-filter">
            <span>状态</span>
            <select v-model="statusFilter" :disabled="listLoading" @change="loadMeetings">
              <option value="ACTIVE">有效会议</option>
              <option value="CONFIRMED">已确认</option>
              <option value="COMPLETED">已完成</option>
              <option value="CANCELLED">已取消</option>
            </select>
          </label>
          <div class="segmented-control" aria-label="会议视图">
            <button type="button" :class="{ active: viewMode === 'calendar' }" @click="viewMode = 'calendar'">
              <CalendarDays :size="15" aria-hidden="true" />日历
            </button>
            <button type="button" :class="{ active: viewMode === 'list' }" @click="viewMode = 'list'">
              <List :size="15" aria-hidden="true" />列表
            </button>
          </div>
          <button class="icon-button" type="button" :disabled="listLoading" aria-label="刷新会议" @click="loadMeetings">
            <RefreshCw :size="17" aria-hidden="true" />
          </button>
        </div>
      </div>

      <p v-if="listError" class="calendar-feedback calendar-feedback--error" role="alert">
        {{ listError }}
        <button type="button" @click="loadMeetings">重试</button>
      </p>
      <div v-else-if="listLoading" class="calendar-loading" aria-live="polite">
        <LoaderCircle :size="20" aria-hidden="true" />正在加载当前窗口的会议…
      </div>
      <div v-else-if="meetings.length === 0" class="calendar-empty">
        <CalendarX2 :size="28" aria-hidden="true" />
        <strong>这个窗口没有会议</strong>
        <span>可以切换日期或创建会议。</span>
        <button class="ui-button ui-button--outline" type="button" @click="openCreateSheet()">创建会议</button>
      </div>

      <MeetingCalendar
        v-else-if="viewMode === 'calendar'"
        :meetings="meetings"
        :days="visibleDays"
        @select="openMeetingDetails"
      />

      <div v-else class="meeting-list-view">
        <button
          v-for="meeting in meetings"
          :key="meeting.id"
          class="meeting-list-row"
          type="button"
          @click="openMeetingDetails(meeting)"
        >
          <span class="meeting-list-row__date">
            <strong>{{ dayNumber(meeting.startAt) }}</strong>
            <small>{{ weekdayFromValue(meeting.startAt) }}</small>
          </span>
          <span class="meeting-list-row__main">
            <strong>{{ meeting.title }}</strong>
            <small>{{ timeRange(meeting) }} · {{ meeting.roomName }}</small>
          </span>
          <span class="meeting-list-row__people">
            <strong>{{ meeting.organizerName }}</strong>
            <small>{{ participantSummary(meeting) }}</small>
          </span>
          <span class="meeting-list-row__source">{{ sourceLabel(meeting.source) }}</span>
          <StatusBadge :status="meeting.status" />
          <ChevronRight :size="17" aria-hidden="true" />
        </button>
      </div>

    </section>

    <Teleport to="body">
      <div v-if="createSheetOpen" class="drawer-layer">
        <button class="drawer-overlay" aria-label="关闭创建会议" @click="closeCreateSheet" />
        <aside class="trace-drawer product-sheet" role="dialog" aria-modal="true" aria-labelledby="create-meeting-title">
          <header class="product-sheet__header">
            <div><p>手动预约</p><h2 id="create-meeting-title">创建会议</h2></div>
            <button class="icon-button" type="button" aria-label="关闭创建会议" @click="closeCreateSheet"><X :size="18" aria-hidden="true" /></button>
          </header>
          <MeetingFormFields :model-value="createForm" :rooms="activeMeetingRooms" :employees="employeeDirectory" :disabled="createSubmitting" />
          <p v-if="createError" class="error-message" role="alert">{{ createError }}</p>
          <footer class="product-sheet__actions">
            <button class="ui-button ui-button--outline" type="button" :disabled="createSubmitting" @click="closeCreateSheet">返回</button>
            <button class="ui-button ui-button--default" type="button" :disabled="createSubmitting || activeMeetingRooms.length === 0" @click="createMeeting">
              {{ createSubmitting ? '正在创建…' : '创建并确认' }}
            </button>
          </footer>
        </aside>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="selectedMeeting" class="drawer-layer">
        <button class="drawer-overlay" aria-label="关闭会议详情" @click="selectedMeeting = null" />
        <aside class="trace-drawer product-sheet" role="dialog" aria-modal="true" aria-labelledby="meeting-detail-title">
          <header class="product-sheet__header">
            <div><h2 id="meeting-detail-title">{{ selectedMeeting.title }}</h2></div>
            <button class="icon-button" type="button" aria-label="关闭会议详情" @click="selectedMeeting = null"><X :size="18" aria-hidden="true" /></button>
          </header>
          <div class="product-sheet__badges">
            <StatusBadge :status="selectedMeeting.status" />
            <span>{{ sourceLabel(selectedMeeting.source) }}</span>
          </div>
          <p v-if="detailLoading" class="calendar-feedback" aria-live="polite">正在同步详情…</p>
          <p v-if="detailError" class="calendar-feedback calendar-feedback--error" role="alert">{{ detailError }}</p>
          <dl class="product-detail-list">
            <div><dt><Clock3 :size="15" aria-hidden="true" />时间</dt><dd>{{ formatDateTime(selectedMeeting.startAt) }} — {{ formatDateTime(selectedMeeting.endAt) }}</dd></div>
            <div><dt><MapPin :size="15" aria-hidden="true" />会议室</dt><dd>{{ selectedMeeting.roomName }}</dd></div>
            <div><dt><UserRound :size="15" aria-hidden="true" />组织者</dt><dd>{{ selectedMeeting.organizerName }}</dd></div>
            <div><dt><Tag :size="15" aria-hidden="true" />类型</dt><dd>{{ meetingTypeLabel(selectedMeeting.meetingType) }}</dd></div>
          </dl>
          <section class="participant-section">
            <h3>参会者</h3>
            <ul v-if="selectedMeeting.participants.length">
              <li v-for="participant in selectedMeeting.participants" :key="`${participant.employeeId}-${participant.participantType}`">
                <span>{{ participant.displayName }}</span>
                <small>{{ participant.participantType === 'REQUIRED' ? '必须参加' : '可选参加' }}</small>
              </li>
            </ul>
            <p v-else>没有额外参会者。</p>
          </section>
          <footer v-if="canManage(selectedMeeting) && selectedMeeting.status === 'CONFIRMED'" class="product-sheet__actions">
            <button class="ui-button ui-button--outline" type="button" @click="beginEdit(selectedMeeting)"><Pencil :size="15" aria-hidden="true" />编辑</button>
            <button class="ui-button ui-button--destructive" type="button" @click="requestCancel(selectedMeeting)"><Trash2 :size="15" aria-hidden="true" />取消会议</button>
          </footer>
        </aside>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="editingMeeting" class="drawer-layer">
        <button class="drawer-overlay" aria-label="关闭编辑会议" @click="editingMeeting = null" />
        <aside class="trace-drawer product-sheet" role="dialog" aria-modal="true" aria-labelledby="edit-meeting-title">
          <header class="product-sheet__header">
            <div><p>修改并重新校验</p><h2 id="edit-meeting-title">{{ editingMeeting.title }}</h2></div>
            <button class="icon-button" type="button" aria-label="关闭编辑会议" @click="editingMeeting = null"><X :size="18" aria-hidden="true" /></button>
          </header>
          <MeetingFormFields :model-value="editForm" :rooms="editableMeetingRooms" :employees="employeeDirectory" :disabled="updateSubmitting" />
          <p v-if="updateError" class="error-message" role="alert">{{ updateError }}</p>
          <footer class="product-sheet__actions">
            <button class="ui-button ui-button--outline" type="button" :disabled="updateSubmitting" @click="editingMeeting = null">返回</button>
            <button class="ui-button ui-button--default" type="button" :disabled="updateSubmitting" @click="updateMeeting">
              {{ updateSubmitting ? '正在保存…' : '保存并重新校验' }}
            </button>
          </footer>
        </aside>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="pendingCancel" class="dialog-layer">
        <button class="drawer-overlay" aria-label="关闭取消确认" @click="pendingCancel = null" />
        <section class="ui-dialog ui-dialog--sm" role="alertdialog" aria-modal="true" aria-labelledby="cancel-meeting-title">
          <h2 id="cancel-meeting-title">取消“{{ pendingCancel.title }}”？</h2>
          <p>正式槽位将被释放，此操作只适用于当前已确认会议。</p>
          <footer>
            <button class="ui-button ui-button--outline" type="button" @click="pendingCancel = null">返回</button>
            <button class="ui-button ui-button--destructive" type="button" @click="confirmCancel">确认取消</button>
          </footer>
        </section>
      </div>
    </Teleport>
  </AppShell>
</template>

<script setup lang="ts">
import {
  CalendarDays,
  CalendarX2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  List,
  LoaderCircle,
  MapPin,
  Pencil,
  Plus,
  RefreshCw,
  Tag,
  Trash2,
  UserRound,
  X,
} from '@lucide/vue'
import { computed, defineComponent, h, onMounted, reactive, ref, watch, type PropType } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError, apiRequest } from '../api/client'
import type { EmployeeDirectoryItem, EmployeeDirectoryResult, Meeting, MeetingListResult, MeetingMutation, MeetingRoom, MeetingUpdateMutation, RoomListResult } from '../api/types'
import { authStore } from '../auth/store'
import AppShell from '../components/AppShell.vue'
import EmployeeMultiSelect from '../components/EmployeeMultiSelect.vue'
import MeetingCalendar from '../components/MeetingCalendar.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useModalFocus } from '../composables/useModalFocus'
import { createClientRequestId, formatDateTime, toShanghaiDateTimeLocal, toShanghaiOffset } from '../utils/format'
import { isTechnicalDemoMeeting, meetingTypeLabel, meetingTypeOptions } from '../utils/labels'

interface MeetingForm {
  title: string
  meetingType: string
  roomId: number
  startAt: string
  endAt: string
  requiredParticipantIds: number[]
  optionalParticipantIds: number[]
}

const MeetingFormFields = defineComponent({
  props: {
    modelValue: { type: Object as PropType<MeetingForm>, required: true },
    rooms: { type: Array as PropType<MeetingRoom[]>, required: true },
    employees: { type: Array as PropType<EmployeeDirectoryItem[]>, required: true },
    disabled: { type: Boolean, default: false },
  },
  setup(props) {
    const update = (field: keyof MeetingForm, value: string | number | number[]): void =>
      { props.modelValue[field] = value as never }
    const field = (label: string, key: keyof MeetingForm, attributes: Record<string, unknown> = {}) =>
      h('label', { class: key === 'title' ? 'form-span-2' : undefined }, [
        h('span', label),
        h('input', {
          value: props.modelValue[key],
          disabled: props.disabled,
          ...attributes,
          onInput: (event: Event) => update(key, (event.target as HTMLInputElement).value),
        }),
      ])
    return () => h('div', { class: 'form-grid product-sheet__form' }, [
      field('会议主题', 'title', { maxlength: 128, required: true }),
      h('label', [h('span', '会议类型'), h('select', {
        value: props.modelValue.meetingType,
        disabled: props.disabled,
        required: true,
        onChange: (event: Event) => update('meetingType', (event.target as HTMLSelectElement).value),
      }, meetingTypeOptions.map((option) => h('option', { value: option.value }, option.label)))]),
      h('label', [h('span', '会议室'), h('select', {
        value: props.modelValue.roomId,
        disabled: props.disabled,
        required: true,
        onChange: (event: Event) => update('roomId', Number((event.target as HTMLSelectElement).value)),
      }, [h('option', { value: 0, disabled: true }, '请选择会议室'), ...props.rooms.map((room) => h('option', { value: room.id }, `${room.name}（${room.capacity} 人）`))])]),
      field('开始时间', 'startAt', { type: 'datetime-local', step: 1800, required: true }),
      field('结束时间', 'endAt', { type: 'datetime-local', step: 1800, required: true }),
      h(EmployeeMultiSelect, {
        label: '必须参会者',
        modelValue: props.modelValue.requiredParticipantIds,
        employees: props.employees,
        excludedIds: props.modelValue.optionalParticipantIds,
        disabled: props.disabled,
        'onUpdate:modelValue': (value: number[]) => update('requiredParticipantIds', value),
      }),
      h(EmployeeMultiSelect, {
        label: '可选参会者',
        modelValue: props.modelValue.optionalParticipantIds,
        employees: props.employees,
        excludedIds: props.modelValue.requiredParticipantIds,
        disabled: props.disabled,
        'onUpdate:modelValue': (value: number[]) => update('optionalParticipantIds', value),
      }),
      h('p', { class: 'form-span-2 product-sheet__help' }, '会议时间按 30 分钟粒度安排。'),
    ])
  },
})

type WindowMode = 'day' | 'week'
type ViewMode = 'calendar' | 'list'

const rooms = ref<MeetingRoom[]>([])
const employeeDirectory = ref<EmployeeDirectoryItem[]>([])
const route = useRoute()
const activeMeetingRooms = computed(() => rooms.value.filter((room) => room.status === 'ACTIVE'))
const editableMeetingRooms = computed(() => {
  const currentRoomId = editingMeeting.value?.roomId
  return rooms.value.filter((room) => room.status === 'ACTIVE' || room.id === currentRoomId)
})
const meetings = ref<Meeting[]>([])
const total = ref(0)
const statusFilter = ref('ACTIVE')
const listLoading = ref(true)
const listError = ref('')
const windowMode = ref<WindowMode>('week')
const viewMode = ref<ViewMode>(window.matchMedia('(max-width: 520px)').matches ? 'list' : 'calendar')
const anchorDate = ref(shanghaiToday())
const createSubmitting = ref(false)
const createError = ref('')
const updateSubmitting = ref(false)
const updateError = ref('')
const createSheetOpen = ref(false)
const selectedMeeting = ref<Meeting | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
const editingMeeting = ref<Meeting | null>(null)
const pendingCancel = ref<Meeting | null>(null)
let createIdempotencyKey: string | null = null

const createForm = reactive<MeetingForm>(blankMeetingForm())
const editForm = reactive<MeetingForm>(blankMeetingForm())
const modalOpen = computed(() => createSheetOpen.value || selectedMeeting.value !== null || editingMeeting.value !== null || pendingCancel.value !== null)
useModalFocus(modalOpen, closeAllOverlays)

const visibleDays = computed(() => {
  const count = windowMode.value === 'day' ? 1 : 7
  const firstDay = windowMode.value === 'week' ? mondayOfWeek(anchorDate.value) : anchorDate.value
  return Array.from({ length: count }, (_, index) => addDays(firstDay, index))
})
const windowStart = computed(() => visibleDays.value[0] ?? anchorDate.value)
const windowEnd = computed(() => addDays(windowStart.value, visibleDays.value.length))
const dateTitle = computed(() => {
  if (windowMode.value === 'day') return longDate(anchorDate.value)
  return `${shortDate(windowStart.value)} — ${shortDate(addDays(windowEnd.value, -1))}`
})

function blankMeetingForm(): MeetingForm {
  return { title: '', meetingType: 'GENERAL', roomId: 0, startAt: '', endAt: '', requiredParticipantIds: [], optionalParticipantIds: [] }
}

function replaceForm(target: MeetingForm, source: MeetingForm): void { Object.assign(target, source) }

function shanghaiToday(): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date())
  const read = (type: Intl.DateTimeFormatPartTypes): string => parts.find((part) => part.type === type)?.value ?? ''
  return `${read('year')}-${read('month')}-${read('day')}`
}

function addDays(value: string, amount: number): string {
  const date = new Date(`${value}T12:00:00+08:00`)
  date.setUTCDate(date.getUTCDate() + amount)
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(date)
  const read = (type: Intl.DateTimeFormatPartTypes): string => parts.find((part) => part.type === type)?.value ?? ''
  return `${read('year')}-${read('month')}-${read('day')}`
}

function mondayOfWeek(value: string): string {
  const date = new Date(`${value}T12:00:00+08:00`)
  const day = date.getUTCDay()
  return addDays(value, -(day === 0 ? 6 : day - 1))
}

function longDate(value: string): string { return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' }).format(new Date(`${value}T00:00:00+08:00`)) }
function shortDate(value: string): string { return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric' }).format(new Date(`${value}T00:00:00+08:00`)) }
function shanghaiDatePart(value: string, option: 'day' | 'weekday' | 'time'): string {
  const date = new Date(value)
  if (option === 'day') return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', day: '2-digit' }).format(date)
  if (option === 'weekday') return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', weekday: 'short' }).format(date)
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false }).format(date)
}

function dayNumber(value: string): string { return shanghaiDatePart(value, 'day') }
function weekdayFromValue(value: string): string { return shanghaiDatePart(value, 'weekday') }
function timeRange(meeting: Meeting): string { return `${shanghaiDatePart(meeting.startAt, 'time')}–${shanghaiDatePart(meeting.endAt, 'time')}` }
function sourceLabel(source: string): string { return source === 'AGENT' ? '智能编排' : '手动创建' }
function participantSummary(meeting: Meeting): string { return meeting.participants.length > 0 ? `${meeting.participants.length} 位参会者` : '仅组织者' }
function canManage(meeting: Meeting): boolean { return authStore.state.user?.roles.includes('ADMIN') === true || authStore.state.user?.id === meeting.organizerId }

async function loadRooms(): Promise<void> {
  try {
    const result = await apiRequest<RoomListResult>('/rooms')
    rooms.value = result.items
  } catch (error) {
    createError.value = error instanceof ApiError ? error.message : '会议室列表加载失败。'
  }
}

async function loadEmployeeDirectory(): Promise<void> {
  try {
    const result = await apiRequest<EmployeeDirectoryResult>('/directory/employees')
    employeeDirectory.value = result.items
  } catch (error) {
    createError.value = error instanceof ApiError ? error.message : '参会人员列表加载失败。'
  }
}

async function loadMeetings(): Promise<void> {
  listLoading.value = true
  listError.value = ''
  try {
    const query = new URLSearchParams({
      from: `${windowStart.value}T00:00:00+08:00`,
      to: `${windowEnd.value}T00:00:00+08:00`,
      page: '1',
      size: '100',
    })
    if (statusFilter.value !== 'ACTIVE') query.set('status', statusFilter.value)
    const result = await apiRequest<MeetingListResult>(`/meetings?${query.toString()}`)
    const visibleItems = result.items.filter((meeting) =>
      !isTechnicalDemoMeeting(meeting.title, meeting.meetingType)
      && (statusFilter.value !== 'ACTIVE' || meeting.status !== 'CANCELLED'))
    meetings.value = [...visibleItems].sort((left, right) => left.startAt.localeCompare(right.startAt))
    total.value = visibleItems.length
  } catch (error) {
    listError.value = error instanceof ApiError ? error.message : '会议列表加载失败，请稍后重试。'
  } finally {
    listLoading.value = false
  }
}

function goToday(): void { anchorDate.value = shanghaiToday(); void loadMeetings() }
function moveWindow(direction: -1 | 1): void { anchorDate.value = addDays(anchorDate.value, direction * (windowMode.value === 'day' ? 1 : 7)); void loadMeetings() }
function setWindowMode(mode: WindowMode): void { windowMode.value = mode; void loadMeetings() }

function openCreateSheet(prefill?: Partial<MeetingForm>): void {
  replaceForm(createForm, { ...blankMeetingForm(), roomId: activeMeetingRooms.value[0]?.id ?? 0, ...prefill })
  createError.value = ''
  createIdempotencyKey = null
  createSheetOpen.value = true
}
function closeCreateSheet(): void { createSheetOpen.value = false }

function validateForm(form: MeetingForm): MeetingMutation | null {
  const requiredParticipantIds = [...new Set(form.requiredParticipantIds)]
  const optionalParticipantIds = [...new Set(form.optionalParticipantIds)]
  if (!form.title || !form.meetingType || form.roomId <= 0 || !form.startAt || !form.endAt) return null
  if (!/:(00|30)$/.test(form.startAt) || !/:(00|30)$/.test(form.endAt) || form.endAt <= form.startAt) return null
  if (optionalParticipantIds.some((id) => requiredParticipantIds.includes(id))) return null
  return { title: form.title, meetingType: form.meetingType, roomId: form.roomId, startAt: toShanghaiOffset(form.startAt), endAt: toShanghaiOffset(form.endAt), requiredParticipantIds, optionalParticipantIds }
}

async function createMeeting(): Promise<void> {
  if (createSubmitting.value) return
  const payload = validateForm(createForm)
  if (!payload) { createError.value = '请完整填写会议，并确保时间位于 30 分钟边界、参会者不重复。'; return }
  createSubmitting.value = true
  createError.value = ''
  createIdempotencyKey ??= createClientRequestId()
  try {
    const meeting = await apiRequest<Meeting>('/meetings', { method: 'POST', headers: { 'Idempotency-Key': createIdempotencyKey }, body: JSON.stringify(payload) })
    createIdempotencyKey = null
    createSheetOpen.value = false
    anchorDate.value = toShanghaiDateTimeLocal(meeting.startAt).slice(0, 10)
    await loadMeetings()
    await openMeetingDetails(meeting)
  } catch (error) {
    createError.value = error instanceof ApiError ? error.message : '手动创建会议失败。'
  } finally { createSubmitting.value = false }
}

async function openMeetingDetails(meeting: Meeting): Promise<void> {
  selectedMeeting.value = meeting
  detailLoading.value = true
  detailError.value = ''
  try { selectedMeeting.value = await apiRequest<Meeting>(`/meetings/${meeting.id}`) }
  catch (error) { detailError.value = error instanceof ApiError ? error.message : '会议详情同步失败。' }
  finally { detailLoading.value = false }
}

async function openMeetingFromQuery(value: unknown): Promise<void> {
  if (typeof value !== 'string' || !/^\d{1,18}$/.test(value)) return
  detailLoading.value = true
  detailError.value = ''
  try { selectedMeeting.value = await apiRequest<Meeting>(`/meetings/${value}`) }
  catch (error) {
    selectedMeeting.value = null
    listError.value = error instanceof ApiError ? error.message : '关联会议加载失败。'
  } finally { detailLoading.value = false }
}

function beginEdit(meeting: Meeting): void {
  selectedMeeting.value = null
  editingMeeting.value = meeting
  updateError.value = ''
  replaceForm(editForm, {
    title: meeting.title,
    meetingType: meeting.meetingType,
    roomId: meeting.roomId,
    startAt: toShanghaiDateTimeLocal(meeting.startAt),
    endAt: toShanghaiDateTimeLocal(meeting.endAt),
    requiredParticipantIds: meeting.participants.filter((participant) => participant.participantType === 'REQUIRED').map((participant) => participant.employeeId),
    optionalParticipantIds: meeting.participants.filter((participant) => participant.participantType === 'OPTIONAL').map((participant) => participant.employeeId),
  })
}

async function updateMeeting(): Promise<void> {
  const meeting = editingMeeting.value
  const payload = validateForm(editForm)
  if (!meeting || updateSubmitting.value) return
  if (!payload) { updateError.value = '请完整填写会议，并确保时间位于 30 分钟边界、参会者不重复。'; return }
  updateSubmitting.value = true
  updateError.value = ''
  try {
    const updated = await apiRequest<Meeting>(`/meetings/${meeting.id}`, { method: 'PUT', body: JSON.stringify({ ...payload, expectedVersion: meeting.version } satisfies MeetingUpdateMutation) })
    editingMeeting.value = null
    await loadMeetings()
    await openMeetingDetails(updated)
  } catch (error) { updateError.value = error instanceof ApiError ? error.message : '会议修改失败。' }
  finally { updateSubmitting.value = false }
}

function requestCancel(meeting: Meeting): void { selectedMeeting.value = null; pendingCancel.value = meeting }
async function confirmCancel(): Promise<void> {
  const meeting = pendingCancel.value
  if (!meeting) return
  pendingCancel.value = null
  try { await apiRequest<Meeting>(`/meetings/${meeting.id}`, { method: 'DELETE' }); await loadMeetings() }
  catch (error) { listError.value = error instanceof ApiError ? error.message : '会议取消失败。' }
}

function closeAllOverlays(): void { createSheetOpen.value = false; selectedMeeting.value = null; editingMeeting.value = null; pendingCancel.value = null }

onMounted(() => {
  if (window.matchMedia('(max-width: 520px)').matches) windowMode.value = 'day'
  void Promise.all([loadRooms(), loadEmployeeDirectory(), loadMeetings()]).then(() => openMeetingFromQuery(route.query.meetingId))
})
watch(() => route.query.meetingId, (value, previous) => {
  if (value !== previous) void openMeetingFromQuery(value)
})
</script>
