<template>
  <div class="meeting-calendar" aria-label="会议日历">
    <div class="meeting-calendar__scroller">
      <div class="meeting-calendar__grid" :style="gridStyle">
        <div class="meeting-calendar__corner" aria-hidden="true">时间</div>
        <div v-for="day in days" :key="day" class="meeting-calendar__day-heading">
          <span>{{ weekday(day) }}</span>
          <strong>{{ shortDate(day) }}</strong>
        </div>

        <div class="meeting-calendar__times" :style="bodyHeightStyle" aria-hidden="true">
          <span
            v-for="hour in hourLabels"
            :key="hour.minute"
            :style="{ top: `${hour.offset}px` }"
          >{{ hour.label }}</span>
        </div>

        <div
          v-for="day in days"
          :key="`body-${day}`"
          class="meeting-calendar__day"
          :style="bodyHeightStyle"
        >
          <div
            v-for="line in halfHourLines"
            :key="line"
            class="meeting-calendar__line"
            :class="{ 'meeting-calendar__line--hour': line % 2 === 0 }"
            :style="{ top: `${line * slotHeight}px` }"
          />
          <button
            v-for="meeting in meetingsForDay(day)"
            :key="meeting.id"
            class="meeting-calendar__event"
            :class="statusClass(meeting.status)"
            :style="eventStyle(meeting)"
            type="button"
            :aria-label="`${meeting.title}，${time(meeting.startAt)} 至 ${time(meeting.endAt)}，${meeting.roomName}`"
            @click="$emit('select', meeting)"
          >
            <strong>{{ meeting.title }}</strong>
            <span>{{ time(meeting.startAt) }} · {{ meeting.roomName }}</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { Meeting } from '../api/types'

const props = defineProps<{
  meetings: readonly Meeting[]
  days: readonly string[]
}>()

defineEmits<{ select: [meeting: Meeting] }>()

const slotHeight = 34

function shanghaiParts(value: string): { date: string; hour: number; minute: number } {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(new Date(value))
  const read = (type: Intl.DateTimeFormatPartTypes): string =>
    parts.find((part) => part.type === type)?.value ?? '00'
  return {
    date: `${read('year')}-${read('month')}-${read('day')}`,
    hour: Number.parseInt(read('hour'), 10) % 24,
    minute: Number.parseInt(read('minute'), 10),
  }
}

const bounds = { start: 8, end: 24 } as const

const halfHourLines = (bounds.end - bounds.start) * 2
const bodyHeightStyle = { height: `${halfHourLines * slotHeight}px` }
const gridStyle = computed(() => ({
  gridTemplateColumns: `76px repeat(${Math.max(props.days.length, 1)}, minmax(154px, 1fr))`,
}))
const hourLabels = computed(() =>
  Array.from({ length: bounds.end - bounds.start + 1 }, (_, index) => ({
    minute: (bounds.start + index) * 60,
    offset: index * slotHeight * 2,
    label: `${String((bounds.start + index) % 24).padStart(2, '0')}:00`,
  })),
)

function meetingsForDay(day: string): Meeting[] {
  return props.meetings.filter((meeting) => shanghaiParts(meeting.startAt).date === day)
}

function eventStyle(meeting: Meeting): Record<string, string> {
  const start = shanghaiParts(meeting.startAt)
  const durationMinutes = Math.max(30, (new Date(meeting.endAt).getTime() - new Date(meeting.startAt).getTime()) / 60_000)
  const startMinutes = start.hour * 60 + start.minute - bounds.start * 60
  return {
    top: `${(startMinutes / 30) * slotHeight + 2}px`,
    height: `${Math.max(slotHeight - 4, (durationMinutes / 30) * slotHeight - 4)}px`,
  }
}

function statusClass(status: string): string {
  if (status === 'CONFIRMED' || status === 'COMPLETED') return 'meeting-calendar__event--confirmed'
  if (status === 'PENDING' || status === 'PROCESSING') return 'meeting-calendar__event--pending'
  return 'meeting-calendar__event--inactive'
}

function time(value: string): string {
  const parts = shanghaiParts(value)
  return `${String(parts.hour).padStart(2, '0')}:${String(parts.minute).padStart(2, '0')}`
}

function shortDate(day: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: 'numeric',
    day: 'numeric',
  }).format(new Date(`${day}T00:00:00+08:00`))
}

function weekday(day: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    weekday: 'short',
  }).format(new Date(`${day}T00:00:00+08:00`))
}
</script>
