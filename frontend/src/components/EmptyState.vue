<template>
  <div class="feedback-state feedback-state--empty">
    <div class="feedback-state__icon" aria-hidden="true">
      <component :is="iconComponent" :size="18" :stroke-width="1.8" />
    </div>
    <h3>{{ title }}</h3>
    <p>{{ description }}</p>
    <slot />
  </div>
</template>

<script setup lang="ts">
import { CalendarClock, CircleCheck, FileSearch, Search, Sparkles } from '@lucide/vue'
import { computed } from 'vue'

const props = withDefaults(defineProps<{ title: string; description: string; icon?: string }>(), { icon: 'search' })

const iconComponent = computed(() => ({
  calendar: CalendarClock,
  check: CircleCheck,
  document: FileSearch,
  search: Search,
  sparkle: Sparkles,
}[props.icon] ?? Search))
</script>
