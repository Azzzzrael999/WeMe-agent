<template>
  <AppShell
    title="会议室"
    description="按 30 分钟槽位查看真实可用性，或浏览会议室资源目录。"
    eyebrow="协作 / 会议室"
  >
    <template #actions>
      <button v-if="isAdmin" class="ui-button ui-button--default" type="button" @click="openAdminCreate">
        <Plus :size="16" aria-hidden="true" />新增会议室
      </button>
    </template>

    <section class="resource-workspace" aria-labelledby="room-workspace-title">
      <h2 id="room-workspace-title" class="sr-only">会议室资源工作台</h2>
      <div class="resource-toolbar">
        <div class="segmented-control" aria-label="会议室视图">
          <button type="button" :class="{ active: viewMode === 'timeline' }" @click="viewMode = 'timeline'">
            <GanttChart :size="15" aria-hidden="true" />时间轴
          </button>
          <button type="button" :class="{ active: viewMode === 'directory' }" @click="viewMode = 'directory'">
            <LayoutGrid :size="15" aria-hidden="true" />房间目录
          </button>
        </div>
        <div class="resource-toolbar__summary">
          <strong>{{ filteredRooms.length }}</strong>
          <span>/ {{ rooms.length }} 间资源</span>
        </div>
        <button class="icon-button resource-toolbar__refresh" type="button" :disabled="loading" aria-label="刷新会议室" @click="refreshRooms">
          <RefreshCw :size="17" aria-hidden="true" />
        </button>
      </div>

      <form class="resource-filters" @submit.prevent="loadAvailabilityMatrix">
        <label><span>楼栋</span><select v-model="filters.building"><option value="">全部楼栋</option><option v-for="value in buildingOptions" :key="value">{{ value }}</option></select></label>
        <label><span>楼层</span><select v-model="filters.floor"><option value="">全部楼层</option><option v-for="value in floorOptions" :key="value">{{ value }}</option></select></label>
        <label><span>至少容纳</span><input v-model.number="filters.capacity" type="number" min="0" step="1" placeholder="不限" /></label>
        <label><span>设备</span><select v-model="filters.feature"><option value="">全部设备</option><option v-for="value in featureOptions" :key="value.code" :value="value.code">{{ value.name }}</option></select></label>
        <label><span>房型</span><select v-model="filters.roomType"><option value="">全部房型</option><option v-for="value in roomTypeOptions" :key="value">{{ value }}</option></select></label>
        <label><span>日期</span><input v-model="selectedDate" type="date" required /></label>
        <label><span>开始</span><input v-model="timeFrom" type="time" step="1800" required /></label>
        <label><span>结束</span><input v-model="timeTo" type="time" step="1800" required /></label>
        <label class="resource-filters__check"><input v-model="filters.onlyAvailable" type="checkbox" /><span>仅看有空闲槽位</span></label>
        <button class="ui-button ui-button--outline" type="submit" :disabled="availabilityLoading || loading">
          <Search :size="15" aria-hidden="true" />{{ availabilityLoading ? '查询中…' : '查询可用性' }}
        </button>
      </form>

      <p v-if="errorMessage" class="calendar-feedback calendar-feedback--error" role="alert">{{ errorMessage }}</p>
      <p v-if="availabilityError" class="calendar-feedback calendar-feedback--error" role="alert">{{ availabilityError }}</p>
      <div v-if="loading" class="calendar-loading" aria-live="polite"><LoaderCircle :size="20" aria-hidden="true" />正在加载会议室…</div>
      <div v-else-if="filteredRooms.length === 0" class="calendar-empty">
        <DoorClosed :size="28" aria-hidden="true" />
        <strong>没有符合条件的会议室</strong>
        <span>这些筛选基于真实会议室字段，没有生成替代资源。</span>
        <button class="ui-button ui-button--outline" type="button" @click="resetFilters">清除筛选</button>
      </div>

      <ResourceTimeline
        v-else-if="viewMode === 'timeline'"
        :rows="visibleTimelineRows"
        :axis="axis"
        @select-room="openRoomDetails"
        @select-slot="openMeetingFromSlot"
      />
      <RoomDirectory
        v-else
        :rooms="filteredRooms"
        :admin="isAdmin"
        @detail="openRoomDetails"
        @edit="beginRoomEdit"
        @toggle="requestRoomStatus"
      />

      <footer v-if="!loading" class="calendar-workspace__footer">
        时间轴只显示公共 availability 接口返回的可用/占用状态；其他会议标题和参会者不会暴露。
      </footer>
    </section>

    <Teleport to="body">
      <div v-if="selectedRoom" class="drawer-layer">
        <button class="drawer-overlay" aria-label="关闭会议室详情" @click="selectedRoom = null" />
        <aside class="trace-drawer product-sheet" role="dialog" aria-modal="true" aria-labelledby="room-detail-title">
          <header class="product-sheet__header">
            <div><p>{{ selectedRoom.code }}</p><h2 id="room-detail-title">{{ selectedRoom.name }}</h2></div>
            <button class="icon-button" type="button" aria-label="关闭会议室详情" @click="selectedRoom = null"><X :size="18" aria-hidden="true" /></button>
          </header>
          <div class="product-sheet__badges"><StatusBadge :status="selectedRoom.status" /><span v-if="selectedRoom.isHot"><Flame :size="13" aria-hidden="true" />热门资源</span></div>
          <p v-if="detailLoading" class="calendar-feedback">正在同步详情…</p>
          <p v-if="detailError" class="calendar-feedback calendar-feedback--error" role="alert">{{ detailError }}</p>
          <dl class="product-detail-list">
            <div><dt><MapPin :size="15" aria-hidden="true" />位置</dt><dd>{{ selectedRoom.building }} · {{ selectedRoom.floor }}</dd></div>
            <div><dt><Users :size="15" aria-hidden="true" />容量</dt><dd>{{ selectedRoom.capacity }} 人</dd></div>
            <div><dt><DoorOpen :size="15" aria-hidden="true" />类型</dt><dd>{{ selectedRoom.roomType }}</dd></div>
            <div><dt><GitCommitHorizontal :size="15" aria-hidden="true" />版本</dt><dd>{{ selectedRoom.version }}</dd></div>
          </dl>
          <section class="participant-section"><h3>设备</h3><div class="room-detail-features"><span v-for="feature in selectedRoom.features" :key="feature.code">{{ feature.name }}</span><p v-if="selectedRoom.features.length === 0">暂无设备标签。</p></div></section>
          <footer class="product-sheet__actions">
            <button v-if="selectedRoom.status === 'ACTIVE'" class="ui-button ui-button--outline" type="button" @click="focusRoomTimeline(selectedRoom)"><CalendarClock :size="15" aria-hidden="true" />查看可用性</button>
            <template v-if="isAdmin">
              <button class="ui-button ui-button--outline" type="button" @click="beginRoomEdit(selectedRoom)"><Pencil :size="15" aria-hidden="true" />编辑</button>
              <button class="ui-button ui-button--destructive" type="button" @click="requestRoomStatus(selectedRoom)"><Power :size="15" aria-hidden="true" />{{ selectedRoom.status === 'ACTIVE' ? '停用' : '启用' }}</button>
            </template>
          </footer>
        </aside>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="meetingSheetOpen" class="drawer-layer">
        <button class="drawer-overlay" aria-label="关闭创建会议" @click="closeMeetingSheet" />
        <aside class="trace-drawer product-sheet" role="dialog" aria-modal="true" aria-labelledby="room-booking-title">
          <header class="product-sheet__header">
            <div><p>来自空闲槽位</p><h2 id="room-booking-title">创建会议</h2></div>
            <button class="icon-button" type="button" aria-label="关闭创建会议" @click="closeMeetingSheet"><X :size="18" aria-hidden="true" /></button>
          </header>
          <p class="product-sheet__notice">已预填 {{ selectedBookingRoom?.name }} 和一个 30 分钟空闲槽位；提交时仍由 Java 最终校验。</p>
          <div class="form-grid product-sheet__form">
            <label class="form-span-2"><span>会议主题</span><input v-model.trim="meetingForm.title" maxlength="128" required :disabled="meetingSubmitting" /></label>
            <label><span>会议类型</span><input v-model.trim="meetingForm.meetingType" maxlength="32" required :disabled="meetingSubmitting" /></label>
            <label><span>会议室</span><select v-model.number="meetingForm.roomId" disabled><option v-for="room in activeRooms" :key="room.id" :value="room.id">{{ room.name }}</option></select></label>
            <label><span>开始（Asia/Shanghai）</span><input v-model="meetingForm.startAt" type="datetime-local" step="1800" required :disabled="meetingSubmitting" /></label>
            <label><span>结束（Asia/Shanghai）</span><input v-model="meetingForm.endAt" type="datetime-local" step="1800" required :disabled="meetingSubmitting" /></label>
            <label><span>必须参会者 ID</span><input v-model.trim="meetingForm.requiredParticipantIds" placeholder="1002, 1003" :disabled="meetingSubmitting" /></label>
            <label><span>可选参会者 ID</span><input v-model.trim="meetingForm.optionalParticipantIds" placeholder="1004" :disabled="meetingSubmitting" /></label>
          </div>
          <p v-if="meetingError" class="error-message" role="alert">{{ meetingError }}</p>
          <footer class="product-sheet__actions">
            <button class="ui-button ui-button--outline" type="button" :disabled="meetingSubmitting" @click="closeMeetingSheet">返回</button>
            <button class="ui-button ui-button--default" type="button" :disabled="meetingSubmitting" @click="createMeeting">{{ meetingSubmitting ? '正在创建…' : '创建并确认' }}</button>
          </footer>
        </aside>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="isAdmin && adminPanelOpen" class="drawer-layer">
        <button class="drawer-overlay" aria-label="关闭会议室编辑" @click="closeAdminPanel" />
        <aside class="trace-drawer product-sheet" role="dialog" aria-modal="true" aria-labelledby="room-admin-title">
          <header class="product-sheet__header">
            <div><p>管理员</p><h2 id="room-admin-title">{{ editingRoom ? '编辑会议室' : '新增会议室' }}</h2></div>
            <button class="icon-button" type="button" aria-label="关闭会议室编辑" @click="closeAdminPanel"><X :size="18" aria-hidden="true" /></button>
          </header>
          <p class="product-sheet__notice">更新和启停都携带当前资源版本，由 Java 管理接口裁决。</p>
          <form class="form-grid product-sheet__form" @submit.prevent="saveRoom">
            <label><span>编码</span><input v-model.trim="roomForm.code" maxlength="32" required :disabled="adminSubmitting" /></label>
            <label><span>名称</span><input v-model.trim="roomForm.name" maxlength="64" required :disabled="adminSubmitting" /></label>
            <label><span>楼栋</span><input v-model.trim="roomForm.building" maxlength="64" required :disabled="adminSubmitting" /></label>
            <label><span>楼层</span><input v-model.trim="roomForm.floor" maxlength="32" required :disabled="adminSubmitting" /></label>
            <label><span>容量</span><input v-model.number="roomForm.capacity" type="number" min="1" required :disabled="adminSubmitting" /></label>
            <label><span>类型</span><input v-model.trim="roomForm.roomType" maxlength="32" required :disabled="adminSubmitting" /></label>
            <label class="form-span-2"><span>设备代码</span><input v-model.trim="roomForm.featureCodes" placeholder="WHITEBOARD, LARGE_SCREEN" :disabled="adminSubmitting" /></label>
            <label class="resource-filters__check"><input v-model="roomForm.isHot" type="checkbox" :disabled="adminSubmitting" /><span>热门会议室</span></label>
            <p v-if="adminError" class="error-message form-span-2" role="alert">{{ adminError }}</p>
            <footer class="product-sheet__actions form-span-2"><button class="ui-button ui-button--outline" type="button" :disabled="adminSubmitting" @click="closeAdminPanel">返回</button><button class="ui-button ui-button--default" type="submit" :disabled="adminSubmitting">{{ adminSubmitting ? '正在保存…' : editingRoom ? '保存修改' : '新增会议室' }}</button></footer>
          </form>
        </aside>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="pendingStatusRoom" class="dialog-layer">
        <button class="drawer-overlay" aria-label="关闭状态确认" @click="closeRoomStatusDialog" />
        <section class="ui-dialog ui-dialog--sm" role="alertdialog" aria-modal="true" aria-labelledby="room-status-title">
          <h2 id="room-status-title">{{ pendingStatusRoom.status === 'ACTIVE' ? '停用' : '启用' }}“{{ pendingStatusRoom.name }}”？</h2>
          <p>{{ pendingStatusRoom.status === 'ACTIVE' ? '停用后系统会为尚未开始的已确认会议创建异常单，并通知会议发起人；不会自动移动会议。' : '启用后会议室将重新对员工可见，仍引用原房间的开放异常单会按服务端事实进入恢复状态。' }}</p>
          <label v-if="pendingStatusRoom.status === 'ACTIVE'">
            <span>失效原因</span>
            <textarea v-model.trim="roomStatusReason" rows="3" maxlength="200" required placeholder="例如：空调漏水，预计今日不可用" :disabled="statusSubmitting" />
            <small>{{ roomStatusReason.length }} / 200 · 将展示给受影响会议的发起人</small>
          </label>
          <p v-if="adminError" class="error-message" role="alert">{{ adminError }}</p>
          <footer><button class="ui-button ui-button--outline" type="button" :disabled="statusSubmitting" @click="closeRoomStatusDialog">返回</button><button class="ui-button ui-button--destructive" type="button" :disabled="statusSubmitting || (pendingStatusRoom.status === 'ACTIVE' && roomStatusReason.length === 0)" @click="confirmRoomStatus">{{ statusSubmitting ? '正在提交…' : `确认${pendingStatusRoom.status === 'ACTIVE' ? '停用' : '启用'}` }}</button></footer>
        </section>
      </div>
    </Teleport>
  </AppShell>
</template>

<script setup lang="ts">
import {
  CalendarClock,
  DoorClosed,
  DoorOpen,
  Flame,
  GanttChart,
  GitCommitHorizontal,
  LayoutGrid,
  LoaderCircle,
  MapPin,
  Pencil,
  Plus,
  Power,
  RefreshCw,
  Search,
  Users,
  X,
} from '@lucide/vue'
import { computed, onMounted, reactive, ref } from 'vue'

import { ApiError, apiRequest } from '../api/client'
import type {
  Meeting,
  MeetingMutation,
  MeetingRoom,
  RoomAvailability,
  RoomAvailabilitySlot,
  RoomListResult,
  RoomMutation,
  RoomStatusMutation,
  RoomUpdateMutation,
} from '../api/types'
import { authStore } from '../auth/store'
import AppShell from '../components/AppShell.vue'
import ResourceTimeline, { type ResourceTimelineRow } from '../components/ResourceTimeline.vue'
import RoomDirectory from '../components/RoomDirectory.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useModalFocus } from '../composables/useModalFocus'
import { createClientRequestId, parseEmployeeIds, toShanghaiDateTimeLocal, toShanghaiOffset } from '../utils/format'

interface RoomForm { code: string; name: string; building: string; floor: string; capacity: number; roomType: string; isHot: boolean; featureCodes: string }
interface MeetingForm { title: string; meetingType: string; roomId: number; startAt: string; endAt: string; requiredParticipantIds: string; optionalParticipantIds: string }

const rooms = ref<MeetingRoom[]>([])
const loading = ref(true)
const errorMessage = ref('')
const viewMode = ref<'timeline' | 'directory'>('timeline')
const selectedDate = ref(shanghaiToday())
const timeFrom = ref('08:00')
const timeTo = ref('19:00')
const availabilityRows = ref<ResourceTimelineRow[]>([])
const availabilityLoading = ref(false)
const availabilityError = ref('')
const filters = reactive({ building: '', floor: '', capacity: 0, feature: '', roomType: '', onlyAvailable: false })
const selectedRoom = ref<MeetingRoom | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
const adminPanelOpen = ref(false)
const editingRoom = ref<MeetingRoom | null>(null)
const pendingStatusRoom = ref<MeetingRoom | null>(null)
const roomStatusReason = ref('')
const statusSubmitting = ref(false)
const adminSubmitting = ref(false)
const adminError = ref('')
const meetingSheetOpen = ref(false)
const selectedBookingRoom = ref<MeetingRoom | null>(null)
const meetingSubmitting = ref(false)
const meetingError = ref('')
let meetingIdempotencyKey: string | null = null

const roomForm = reactive<RoomForm>({ code: '', name: '', building: '', floor: '', capacity: 8, roomType: 'STANDARD', isHot: false, featureCodes: '' })
const meetingForm = reactive<MeetingForm>({ title: '', meetingType: 'GENERAL', roomId: 0, startAt: '', endAt: '', requiredParticipantIds: '', optionalParticipantIds: '' })
const isAdmin = computed(() => authStore.state.user?.roles.includes('ADMIN') ?? false)
const activeRooms = computed(() => rooms.value.filter((room) => room.status === 'ACTIVE'))
const buildingOptions = computed(() => unique(rooms.value.map((room) => room.building)))
const floorOptions = computed(() => unique(rooms.value.filter((room) => !filters.building || room.building === filters.building).map((room) => room.floor)))
const roomTypeOptions = computed(() => unique(rooms.value.map((room) => room.roomType)))
const featureOptions = computed(() => {
  const map = new Map<string, string>()
  for (const room of rooms.value) for (const feature of room.features) map.set(feature.code, feature.name)
  return [...map.entries()].map(([code, name]) => ({ code, name })).sort((left, right) => left.name.localeCompare(right.name, 'zh-CN'))
})
const filteredRoomsBase = computed(() => rooms.value.filter((room) =>
  (!filters.building || room.building === filters.building)
  && (!filters.floor || room.floor === filters.floor)
  && (!filters.capacity || room.capacity >= filters.capacity)
  && (!filters.feature || room.features.some((feature) => feature.code === filters.feature))
  && (!filters.roomType || room.roomType === filters.roomType),
))
const filteredRooms = computed(() => filters.onlyAvailable
  ? filteredRoomsBase.value.filter((room) => availabilityRows.value.find((row) => row.room.id === room.id)?.slots.some((slot) => slot.available) === true)
  : filteredRoomsBase.value)
const visibleTimelineRows = computed(() => filteredRooms.value.map((room) => availabilityRows.value.find((row) => row.room.id === room.id) ?? { room, slots: [], loading: availabilityLoading.value, error: availabilityLoading.value ? undefined : '尚未查询当前筛选' }))
const axis = computed(() => buildAxis(selectedDate.value, timeFrom.value, timeTo.value))
const modalOpen = computed(() => selectedRoom.value !== null || meetingSheetOpen.value || adminPanelOpen.value || pendingStatusRoom.value !== null)
useModalFocus(modalOpen, closeAllOverlays)

function unique(values: string[]): string[] { return [...new Set(values)].sort((left, right) => left.localeCompare(right, 'zh-CN')) }
function shanghaiToday(): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date())
  const read = (type: Intl.DateTimeFormatPartTypes): string => parts.find((part) => part.type === type)?.value ?? ''
  return `${read('year')}-${read('month')}-${read('day')}`
}
function validWindow(): boolean { return /^(?:[01]\d|2[0-3]):(?:00|30)$/.test(timeFrom.value) && /^(?:[01]\d|2[0-3]):(?:00|30)$/.test(timeTo.value) && timeTo.value > timeFrom.value }
function buildAxis(date: string, from: string, to: string): string[] {
  if (!date || !validWindow()) return []
  const starts: string[] = []
  const [fromHour = 0, fromMinute = 0] = from.split(':').map(Number)
  const [toHour = 0, toMinute = 0] = to.split(':').map(Number)
  let minuteOfDay = fromHour * 60 + fromMinute
  const endMinute = toHour * 60 + toMinute
  while (minuteOfDay < endMinute) {
    starts.push(`${date}T${String(Math.floor(minuteOfDay / 60)).padStart(2, '0')}:${String(minuteOfDay % 60).padStart(2, '0')}:00+08:00`)
    minuteOfDay += 30
  }
  return starts
}

async function loadRooms(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try { const result = await apiRequest<RoomListResult>('/rooms'); rooms.value = result.items }
  catch (error) { errorMessage.value = error instanceof ApiError ? error.message : '会议室加载失败，请稍后重试。' }
  finally { loading.value = false }
}

async function refreshRooms(): Promise<void> { await loadRooms(); await loadAvailabilityMatrix() }
async function loadAvailabilityMatrix(): Promise<void> {
  if (!selectedDate.value || !validWindow()) { availabilityError.value = '请选择有效日期；开始和结束时间必须落在 30 分钟边界。'; return }
  const targets = filteredRoomsBase.value
  availabilityLoading.value = true
  availabilityError.value = ''
  availabilityRows.value = targets.map((room) => ({ room, slots: [], loading: room.status === 'ACTIVE' }))
  const from = `${selectedDate.value}T${timeFrom.value}:00+08:00`
  const to = `${selectedDate.value}T${timeTo.value}:00+08:00`
  await Promise.all(targets.map(async (room) => {
    if (room.status !== 'ACTIVE') return
    const query = new URLSearchParams({ from, to })
    try {
      const result = await apiRequest<RoomAvailability>(`/rooms/${room.id}/availability?${query.toString()}`)
      replaceAvailabilityRow(room, { room, slots: result.availableSlots })
    } catch (error) {
      replaceAvailabilityRow(room, { room, slots: [], error: error instanceof ApiError ? error.message : '查询失败' })
    }
  }))
  availabilityLoading.value = false
  if (availabilityRows.value.some((row) => row.error)) availabilityError.value = '部分会议室可用性查询失败，对应槽位不会被展示为可预约。'
}
function replaceAvailabilityRow(room: MeetingRoom, row: ResourceTimelineRow): void { availabilityRows.value = availabilityRows.value.map((current) => current.room.id === room.id ? row : current) }
function resetFilters(): void { Object.assign(filters, { building: '', floor: '', capacity: 0, feature: '', roomType: '', onlyAvailable: false }); void loadAvailabilityMatrix() }

async function openRoomDetails(room: MeetingRoom): Promise<void> {
  selectedRoom.value = room
  detailLoading.value = true
  detailError.value = ''
  try { selectedRoom.value = await apiRequest<MeetingRoom>(`/rooms/${room.id}`) }
  catch (error) { detailError.value = error instanceof ApiError ? error.message : '会议室详情同步失败。' }
  finally { detailLoading.value = false }
}
function focusRoomTimeline(room: MeetingRoom): void {
  selectedRoom.value = null
  Object.assign(filters, { building: room.building, floor: room.floor, capacity: 0, feature: '', roomType: '', onlyAvailable: false })
  viewMode.value = 'timeline'
  void loadAvailabilityMatrix()
}

function openMeetingFromSlot(room: MeetingRoom, slot: RoomAvailabilitySlot): void {
  selectedBookingRoom.value = room
  Object.assign(meetingForm, { title: '', meetingType: 'GENERAL', roomId: room.id, startAt: toShanghaiDateTimeLocal(slot.startAt), endAt: toShanghaiDateTimeLocal(slot.endAt), requiredParticipantIds: '', optionalParticipantIds: '' })
  meetingError.value = ''
  meetingIdempotencyKey = null
  meetingSheetOpen.value = true
}
function closeMeetingSheet(): void { meetingSheetOpen.value = false; selectedBookingRoom.value = null }
function meetingPayload(): MeetingMutation | null {
  const requiredParticipantIds = parseEmployeeIds(meetingForm.requiredParticipantIds)
  const optionalParticipantIds = parseEmployeeIds(meetingForm.optionalParticipantIds)
  if (!meetingForm.title || !meetingForm.meetingType || meetingForm.roomId <= 0 || !/:(00|30)$/.test(meetingForm.startAt) || !/:(00|30)$/.test(meetingForm.endAt) || meetingForm.endAt <= meetingForm.startAt || optionalParticipantIds.some((id) => requiredParticipantIds.includes(id))) return null
  return { title: meetingForm.title, meetingType: meetingForm.meetingType, roomId: meetingForm.roomId, startAt: toShanghaiOffset(meetingForm.startAt), endAt: toShanghaiOffset(meetingForm.endAt), requiredParticipantIds, optionalParticipantIds }
}
async function createMeeting(): Promise<void> {
  if (meetingSubmitting.value) return
  const payload = meetingPayload()
  if (!payload) { meetingError.value = '请完整填写会议，并确保时间位于 30 分钟边界、参会者不重复。'; return }
  meetingSubmitting.value = true
  meetingError.value = ''
  meetingIdempotencyKey ??= createClientRequestId()
  try {
    await apiRequest<Meeting>('/meetings', { method: 'POST', headers: { 'Idempotency-Key': meetingIdempotencyKey }, body: JSON.stringify(payload) })
    meetingIdempotencyKey = null
    closeMeetingSheet()
    await loadAvailabilityMatrix()
  } catch (error) { meetingError.value = error instanceof ApiError ? error.message : '会议创建失败。' }
  finally { meetingSubmitting.value = false }
}

function featureCodes(value: string): string[] { return [...new Set(value.split(/[，,\s]+/).map((code) => code.trim()).filter(Boolean))] }
function roomPayload(): RoomMutation | null {
  if (!roomForm.code || !roomForm.name || !roomForm.building || !roomForm.floor || !roomForm.roomType || !Number.isSafeInteger(roomForm.capacity) || roomForm.capacity <= 0) return null
  return { code: roomForm.code, name: roomForm.name, building: roomForm.building, floor: roomForm.floor, capacity: roomForm.capacity, roomType: roomForm.roomType, isHot: roomForm.isHot, featureCodes: featureCodes(roomForm.featureCodes) }
}
function resetRoomForm(): void { editingRoom.value = null; adminError.value = ''; Object.assign(roomForm, { code: '', name: '', building: '', floor: '', capacity: 8, roomType: 'STANDARD', isHot: false, featureCodes: '' }) }
function openAdminCreate(): void { resetRoomForm(); adminPanelOpen.value = true }
function closeAdminPanel(): void { adminPanelOpen.value = false; resetRoomForm() }
function beginRoomEdit(room: MeetingRoom): void {
  selectedRoom.value = null
  editingRoom.value = room
  adminError.value = ''
  Object.assign(roomForm, { code: room.code, name: room.name, building: room.building, floor: room.floor, capacity: room.capacity, roomType: room.roomType, isHot: room.isHot, featureCodes: room.features.map((feature) => feature.code).join(', ') })
  adminPanelOpen.value = true
}
async function saveRoom(): Promise<void> {
  const payload = roomPayload()
  if (!payload || adminSubmitting.value) { adminError.value = '请完整填写会议室字段，并确保容量为正整数。'; return }
  adminSubmitting.value = true
  adminError.value = ''
  try {
    if (!editingRoom.value) await apiRequest<MeetingRoom>('/admin/rooms', { method: 'POST', body: JSON.stringify(payload) })
    else await apiRequest<MeetingRoom>(`/admin/rooms/${editingRoom.value.id}`, { method: 'PUT', body: JSON.stringify({ ...payload, expectedVersion: editingRoom.value.version } satisfies RoomUpdateMutation) })
    closeAdminPanel()
    await refreshRooms()
  } catch (error) { adminError.value = error instanceof ApiError ? error.message : '会议室保存失败。' }
  finally { adminSubmitting.value = false }
}
function requestRoomStatus(room: MeetingRoom): void { selectedRoom.value = null; adminError.value = ''; roomStatusReason.value = ''; pendingStatusRoom.value = room }
function closeRoomStatusDialog(): void {
  if (statusSubmitting.value) return
  pendingStatusRoom.value = null
  roomStatusReason.value = ''
}
async function confirmRoomStatus(): Promise<void> {
  const room = pendingStatusRoom.value
  if (!room || statusSubmitting.value) return
  const nextStatus = room.status === 'ACTIVE' ? 'INACTIVE' : 'ACTIVE'
  const reason = roomStatusReason.value.trim()
  if (nextStatus === 'INACTIVE' && reason.length === 0) {
    adminError.value = '停用会议室前请填写失效原因。'
    return
  }
  statusSubmitting.value = true
  adminError.value = ''
  try {
    const request: RoomStatusMutation = {
      status: nextStatus,
      expectedVersion: room.version,
      ...(nextStatus === 'INACTIVE' ? { reason } : {}),
    }
    await apiRequest<MeetingRoom>(`/admin/rooms/${room.id}/status`, { method: 'PATCH', body: JSON.stringify(request) })
    pendingStatusRoom.value = null
    roomStatusReason.value = ''
    await refreshRooms()
  } catch (error) { adminError.value = error instanceof ApiError ? error.message : '会议室状态更新失败。' }
  finally { statusSubmitting.value = false }
}

function closeAllOverlays(): void { selectedRoom.value = null; meetingSheetOpen.value = false; selectedBookingRoom.value = null; adminPanelOpen.value = false; pendingStatusRoom.value = null; roomStatusReason.value = '' }

onMounted(async () => { await loadRooms(); await loadAvailabilityMatrix() })
</script>
