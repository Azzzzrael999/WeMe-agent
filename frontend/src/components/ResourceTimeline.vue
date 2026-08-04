<template>
  <div class="resource-timeline" :aria-label="label">
    <div v-if="normalizedRows.length && normalizedAxis.length" class="resource-timeline__scroller">
      <div class="resource-timeline__grid" :style="gridStyle">
        <div class="resource-timeline__room-head">会议室</div>
        <div v-for="slot in normalizedAxis" :key="slot" class="resource-timeline__time-head">{{ time(slot) }}</div>

        <template v-for="row in normalizedRows" :key="row.room.id">
          <button class="resource-timeline__room" type="button" @click="$emit('select-room', row.room)">
            <strong>{{ row.room.name }}</strong>
            <span>{{ row.room.capacity }} 人 · {{ featureSummary(row.room) }}</span>
          </button>
          <template v-for="slotStart in normalizedAxis" :key="`${row.room.id}-${slotStart}`">
            <div v-if="row.loading" class="resource-timeline__cell resource-timeline__cell--loading" aria-label="正在查询" />
            <div
              v-else-if="row.room.status !== 'ACTIVE'"
              class="resource-timeline__cell resource-timeline__cell--disabled"
              :aria-label="`${row.room.name}已停用`"
            >停用</div>
            <div
              v-else-if="row.error || findSlot(row, slotStart) === undefined"
              class="resource-timeline__cell resource-timeline__cell--unknown"
              :title="row.error || '未返回该槽位'"
            >不可用</div>
            <button
              v-else-if="findSlot(row, slotStart)?.available"
              class="resource-timeline__cell resource-timeline__cell--free"
              type="button"
              :aria-label="`${row.room.name} ${time(slotStart)} 可用，创建会议`"
              @click="$emit('select-slot', row.room, findSlot(row, slotStart)!)"
            >可用</button>
            <div
              v-else
              class="resource-timeline__cell resource-timeline__cell--busy"
              :aria-label="`${row.room.name} ${time(slotStart)} 已占用`"
              title="公共可用性接口不暴露其他会议详情"
            >占用</div>
          </template>
        </template>
      </div>
    </div>
    <div v-else class="resource-timeline__empty">
      <CalendarOff :size="24" aria-hidden="true" />
      <strong>当前筛选没有可显示的资源</strong>
      <span>调整筛选或时间窗口后重新查询。</span>
    </div>
    <p v-if="normalizedRows.some((row) => row.error)" class="resource-timeline__warning" role="status">
      部分会议室可用性查询失败，对应槽位已明确标为不可用。
    </p>
  </div>
</template>

<script setup lang="ts">
import { CalendarOff } from '@lucide/vue'
import { computed } from 'vue'

import type { MeetingRoom, RoomAvailabilitySlot } from '../api/types'

export interface ResourceTimelineRow {
  room: MeetingRoom
  slots: readonly RoomAvailabilitySlot[]
  loading?: boolean
  error?: string
}

const props = withDefaults(defineProps<{
  rows?: readonly ResourceTimelineRow[]
  axis?: readonly string[]
  /** Compatibility for the former single-room timeline used by ChatView. */
  slots?: readonly RoomAvailabilitySlot[]
  label?: string
}>(), { rows: () => [], axis: () => [], slots: () => [], label: '会议室 30 分钟资源时间轴' })

defineEmits<{
  'select-room': [room: MeetingRoom]
  'select-slot': [room: MeetingRoom, slot: RoomAvailabilitySlot]
}>()

const gridStyle = computed(() => ({
  gridTemplateColumns: `210px repeat(${Math.max(normalizedAxis.value.length, 1)}, minmax(72px, 1fr))`,
}))

const compatibilityRoom: MeetingRoom = {
  id: 0,
  code: 'CURRENT',
  name: '当前资源',
  building: '',
  floor: '',
  capacity: 0,
  roomType: '',
  isHot: false,
  status: 'ACTIVE',
  version: 0,
  features: [],
}
const normalizedRows = computed<readonly ResourceTimelineRow[]>(() => {
  if (props.rows.length > 0) return props.rows
  return props.slots.length > 0 ? [{ room: compatibilityRoom, slots: props.slots }] : []
})
const normalizedAxis = computed<readonly string[]>(() => {
  if (props.axis.length > 0) return props.axis
  return props.slots.map((slot) => slot.startAt)
})

function findSlot(row: ResourceTimelineRow, startAt: string): RoomAvailabilitySlot | undefined {
  return row.slots.find((slot) => slot.startAt === startAt)
}

function time(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value))
}

function featureSummary(room: MeetingRoom): string {
  const names = room.features.slice(0, 2).map((feature) => feature.name)
  return names.length > 0 ? names.join(' / ') : '基础配置'
}
</script>
