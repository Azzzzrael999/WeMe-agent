<template>
  <AppShell title="会议室">
    <section class="content-panel" aria-labelledby="room-list-title">
      <div class="section-heading compact-heading">
        <div>
          <h2 id="room-list-title">会议室与设备</h2>
          <p class="muted">员工可查询可用会议室；管理员可以维护会议室和热门标记。</p>
        </div>
        <button class="secondary-button" type="button" :disabled="loading" @click="loadRooms">
          {{ loading ? '加载中…' : '刷新' }}
        </button>
      </div>

      <p v-if="errorMessage" class="error-message" role="alert">{{ errorMessage }}</p>
      <p v-else-if="loading" class="status-message" aria-live="polite">正在加载会议室…</p>
      <div v-else-if="rooms.length === 0" class="empty-state">暂无可显示的会议室。</div>

      <div v-else class="room-grid">
        <article v-for="room in rooms" :key="room.id" class="room-card">
          <div class="room-card-heading">
            <div>
              <p class="room-code">{{ room.code }}</p>
              <h3>{{ room.name }}</h3>
            </div>
            <div class="badges">
              <span v-if="room.isHot" class="badge badge-hot">热门</span>
              <span class="badge" :class="room.status === 'ACTIVE' ? 'badge-success' : 'badge-danger'">{{ room.status }}</span>
            </div>
          </div>

          <dl class="room-facts">
            <div><dt>位置</dt><dd>{{ room.building }} · {{ room.floor }}</dd></div>
            <div><dt>容量</dt><dd>{{ room.capacity }} 人</dd></div>
            <div><dt>类型</dt><dd>{{ room.roomType }}</dd></div>
          </dl>

          <div class="feature-list" aria-label="会议室设备">
            <span v-for="feature in room.features" :key="feature.code" class="feature-chip">{{ feature.name }}</span>
            <span v-if="room.features.length === 0" class="muted">暂无设备标签</span>
          </div>

          <div v-if="isAdmin" class="room-admin-actions">
            <button class="secondary-button" type="button" @click="beginRoomEdit(room)">编辑</button>
            <button class="danger-button" type="button" @click="toggleRoomStatus(room)">
              {{ room.status === 'ACTIVE' ? '停用' : '启用' }}
            </button>
          </div>
        </article>
      </div>
      <p v-if="!loading && !errorMessage" class="result-count">共 {{ total }} 间会议室</p>
    </section>

    <section class="content-panel availability-panel" aria-labelledby="availability-title">
      <div class="section-heading compact-heading">
        <div>
          <h2 id="availability-title">可用时间</h2>
          <p class="muted">按 30 分钟槽位显示；所有输入均为 Asia/Shanghai。</p>
        </div>
      </div>
      <form class="form-grid availability-form" @submit.prevent="loadAvailability">
        <label>
          <span>会议室</span>
          <select v-model.number="availabilityRoomId" required :disabled="availabilityLoading || rooms.length === 0">
            <option :value="0" disabled>请选择会议室</option>
            <option v-for="room in rooms" :key="room.id" :value="room.id">{{ room.name }}</option>
          </select>
        </label>
        <label><span>开始</span><input v-model="availabilityFrom" type="datetime-local" step="1800" required :disabled="availabilityLoading" /></label>
        <label><span>结束</span><input v-model="availabilityTo" type="datetime-local" step="1800" required :disabled="availabilityLoading" /></label>
        <p v-if="availabilityError" class="error-message form-span-2" role="alert">{{ availabilityError }}</p>
        <div class="form-actions form-span-2"><button class="secondary-button" type="submit" :disabled="availabilityLoading">{{ availabilityLoading ? '查询中…' : '查询可用时间' }}</button></div>
      </form>
      <div v-if="availability" class="availability-slots" aria-live="polite">
        <p class="muted">{{ formatDateTime(availability.from) }} 至 {{ formatDateTime(availability.to) }}</p>
        <span
          v-for="slot in availability.availableSlots"
          :key="`${slot.startAt}-${slot.endAt}`"
          class="availability-slot"
          :class="slot.available ? 'availability-slot--free' : 'availability-slot--busy'"
        >
          {{ formatDateTime(slot.startAt) }} {{ slot.available ? '可用' : '已占用' }}
        </span>
        <p v-if="availability.availableSlots.length === 0" class="empty-state compact-empty">此窗口没有可展示的槽位。</p>
      </div>
    </section>

    <section v-if="isAdmin" class="content-panel room-admin-panel" aria-labelledby="room-admin-title">
      <div class="section-heading compact-heading">
        <div>
          <p class="eyebrow">管理员</p>
          <h2 id="room-admin-title">{{ editingRoom ? '修改会议室' : '新增会议室' }}</h2>
          <p class="muted">写入使用 Java 管理接口；更新和启停都携带当前版本。</p>
        </div>
        <button v-if="editingRoom" class="secondary-button" type="button" :disabled="adminSubmitting" @click="resetRoomForm">新增会议室</button>
      </div>
      <form class="form-grid" @submit.prevent="saveRoom">
        <label><span>编码</span><input v-model.trim="roomForm.code" maxlength="32" required :disabled="adminSubmitting" /></label>
        <label><span>名称</span><input v-model.trim="roomForm.name" maxlength="64" required :disabled="adminSubmitting" /></label>
        <label><span>楼栋</span><input v-model.trim="roomForm.building" maxlength="64" required :disabled="adminSubmitting" /></label>
        <label><span>楼层</span><input v-model.trim="roomForm.floor" maxlength="32" required :disabled="adminSubmitting" /></label>
        <label><span>容量</span><input v-model.number="roomForm.capacity" type="number" min="1" required :disabled="adminSubmitting" /></label>
        <label><span>类型</span><input v-model.trim="roomForm.roomType" maxlength="32" required :disabled="adminSubmitting" /></label>
        <label class="form-span-2"><span>设备代码</span><input v-model.trim="roomForm.featureCodes" placeholder="WHITEBOARD, LARGE_SCREEN" :disabled="adminSubmitting" /></label>
        <label class="checkbox-label"><input v-model="roomForm.isHot" type="checkbox" :disabled="adminSubmitting" /><span>热门会议室</span></label>
        <p v-if="adminError" class="error-message form-span-2" role="alert">{{ adminError }}</p>
        <div class="form-actions form-span-2"><button class="primary-button" type="submit" :disabled="adminSubmitting">{{ adminSubmitting ? '正在保存…' : editingRoom ? '保存修改' : '新增会议室' }}</button></div>
      </form>
    </section>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { ApiError, apiRequest } from '../api/client'
import type {
  MeetingRoom,
  RoomAvailability,
  RoomListResult,
  RoomMutation,
  RoomStatusMutation,
  RoomUpdateMutation,
} from '../api/types'
import { authStore } from '../auth/store'
import AppShell from '../components/AppShell.vue'
import { formatDateTime, toShanghaiOffset } from '../utils/format'

interface RoomForm {
  code: string
  name: string
  building: string
  floor: string
  capacity: number
  roomType: string
  isHot: boolean
  featureCodes: string
}

const rooms = ref<MeetingRoom[]>([])
const total = ref(0)
const loading = ref(true)
const errorMessage = ref('')
const availabilityRoomId = ref(0)
const availabilityFrom = ref('')
const availabilityTo = ref('')
const availability = ref<RoomAvailability | null>(null)
const availabilityLoading = ref(false)
const availabilityError = ref('')
const editingRoom = ref<MeetingRoom | null>(null)
const adminSubmitting = ref(false)
const adminError = ref('')

const roomForm = reactive<RoomForm>({
  code: '',
  name: '',
  building: '',
  floor: '',
  capacity: 8,
  roomType: 'STANDARD',
  isHot: false,
  featureCodes: '',
})

const isAdmin = computed(() => authStore.state.user?.roles.includes('ADMIN') ?? false)

async function loadRooms(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const result = await apiRequest<RoomListResult>('/rooms')
    rooms.value = result.items
    total.value = result.total
    if (availabilityRoomId.value === 0 && result.items.length > 0) {
      availabilityRoomId.value = result.items[0].id
    }
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '会议室加载失败，请稍后重试。'
  } finally {
    loading.value = false
  }
}

async function loadAvailability(): Promise<void> {
  if (availabilityRoomId.value <= 0 || availabilityFrom.value.length === 0 || availabilityTo.value.length === 0) {
    availabilityError.value = '请选择会议室和完整的时间窗口。'
    return
  }
  if (!/:(00|30)$/.test(availabilityFrom.value) || !/:(00|30)$/.test(availabilityTo.value) || availabilityTo.value <= availabilityFrom.value) {
    availabilityError.value = '时间须为连续的 30 分钟槽位，且结束时间晚于开始时间。'
    return
  }
  availabilityLoading.value = true
  availabilityError.value = ''
  try {
    const query = new URLSearchParams({
      from: toShanghaiOffset(availabilityFrom.value),
      to: toShanghaiOffset(availabilityTo.value),
    })
    availability.value = await apiRequest<RoomAvailability>(
      `/rooms/${availabilityRoomId.value}/availability?${query.toString()}`,
    )
  } catch (error) {
    availabilityError.value = error instanceof ApiError ? error.message : '可用时间查询失败。'
  } finally {
    availabilityLoading.value = false
  }
}

function featureCodes(value: string): string[] {
  return [...new Set(value.split(/[，,\s]+/).map((code) => code.trim()).filter((code) => code.length > 0))]
}

function roomPayload(): RoomMutation | null {
  if (
    roomForm.code.length === 0 ||
    roomForm.name.length === 0 ||
    roomForm.building.length === 0 ||
    roomForm.floor.length === 0 ||
    roomForm.roomType.length === 0 ||
    !Number.isSafeInteger(roomForm.capacity) ||
    roomForm.capacity <= 0
  ) {
    return null
  }
  return {
    code: roomForm.code,
    name: roomForm.name,
    building: roomForm.building,
    floor: roomForm.floor,
    capacity: roomForm.capacity,
    roomType: roomForm.roomType,
    isHot: roomForm.isHot,
    featureCodes: featureCodes(roomForm.featureCodes),
  }
}

function beginRoomEdit(room: MeetingRoom): void {
  editingRoom.value = room
  adminError.value = ''
  roomForm.code = room.code
  roomForm.name = room.name
  roomForm.building = room.building
  roomForm.floor = room.floor
  roomForm.capacity = room.capacity
  roomForm.roomType = room.roomType
  roomForm.isHot = room.isHot
  roomForm.featureCodes = room.features.map((feature) => feature.code).join(', ')
}

function resetRoomForm(): void {
  editingRoom.value = null
  adminError.value = ''
  roomForm.code = ''
  roomForm.name = ''
  roomForm.building = ''
  roomForm.floor = ''
  roomForm.capacity = 8
  roomForm.roomType = 'STANDARD'
  roomForm.isHot = false
  roomForm.featureCodes = ''
}

async function saveRoom(): Promise<void> {
  const payload = roomPayload()
  if (payload === null || adminSubmitting.value) {
    adminError.value = '请完整填写会议室字段，并确保容量为正整数。'
    return
  }
  adminSubmitting.value = true
  adminError.value = ''
  try {
    if (editingRoom.value === null) {
      await apiRequest<MeetingRoom>('/admin/rooms', { method: 'POST', body: JSON.stringify(payload) })
    } else {
      const request: RoomUpdateMutation = { ...payload, expectedVersion: editingRoom.value.version }
      await apiRequest<MeetingRoom>(`/admin/rooms/${editingRoom.value.id}`, {
        method: 'PUT',
        body: JSON.stringify(request),
      })
    }
    resetRoomForm()
    await loadRooms()
  } catch (error) {
    adminError.value = error instanceof ApiError ? error.message : '会议室保存失败。'
  } finally {
    adminSubmitting.value = false
  }
}

async function toggleRoomStatus(room: MeetingRoom): Promise<void> {
  const nextStatus = room.status === 'ACTIVE' ? 'INACTIVE' : 'ACTIVE'
  if (!window.confirm(`确认将“${room.name}”${nextStatus === 'ACTIVE' ? '启用' : '停用'}吗？`)) {
    return
  }
  adminError.value = ''
  try {
    const request: RoomStatusMutation = { status: nextStatus, expectedVersion: room.version }
    await apiRequest<MeetingRoom>(`/admin/rooms/${room.id}/status`, {
      method: 'PATCH',
      body: JSON.stringify(request),
    })
    await loadRooms()
  } catch (error) {
    adminError.value = error instanceof ApiError ? error.message : '会议室状态更新失败。'
  }
}

onMounted(() => {
  void loadRooms()
})
</script>
