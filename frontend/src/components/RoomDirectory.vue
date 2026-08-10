<template>
  <div class="room-directory" aria-label="会议室目录">
    <article v-for="room in rooms" :key="room.id" class="room-directory__item">
      <header>
        <button class="room-directory__name" type="button" @click="$emit('detail', room)">
          <Building2 :size="18" aria-hidden="true" />
          <span><strong>{{ room.name }}</strong></span>
        </button>
        <div class="room-directory__badges">
          <span v-if="room.isHot" class="room-directory__hot"><Flame :size="12" aria-hidden="true" />热门</span>
          <StatusBadge :status="room.status" />
        </div>
      </header>
      <dl>
        <div><dt><MapPin :size="14" aria-hidden="true" />位置</dt><dd>{{ room.building }} · {{ room.floor }}</dd></div>
        <div><dt><Users :size="14" aria-hidden="true" />容量</dt><dd>{{ room.capacity }} 人</dd></div>
        <div><dt><DoorOpen :size="14" aria-hidden="true" />类型</dt><dd>{{ roomTypeLabel(room.roomType) }}</dd></div>
      </dl>
      <div class="room-directory__features" :aria-label="`${room.name}设备`">
        <span v-for="feature in room.features" :key="feature.code">{{ feature.name }}</span>
        <span v-if="room.features.length === 0">无设备标签</span>
      </div>
      <footer>
        <button class="ui-button ui-button--outline" type="button" @click="$emit('detail', room)">查看详情</button>
        <template v-if="admin">
          <button class="ui-button ui-button--outline" type="button" @click="$emit('edit', room)"><Pencil :size="14" aria-hidden="true" />编辑</button>
          <button class="ui-button ui-button--outline" type="button" @click="$emit('toggle', room)"><Power :size="14" aria-hidden="true" />{{ room.status === 'ACTIVE' ? '停用' : '启用' }}</button>
        </template>
      </footer>
    </article>
  </div>
</template>

<script setup lang="ts">
import { Building2, DoorOpen, Flame, MapPin, Pencil, Power, Users } from '@lucide/vue'

import type { MeetingRoom } from '../api/types'
import { roomTypeLabel } from '../utils/labels'
import StatusBadge from './StatusBadge.vue'

defineProps<{ rooms: readonly MeetingRoom[]; admin: boolean }>()
defineEmits<{
  detail: [room: MeetingRoom]
  edit: [room: MeetingRoom]
  toggle: [room: MeetingRoom]
}>()
</script>
