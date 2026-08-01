<template>
  <div class="feedback-state feedback-state--empty">
    <div class="feedback-state__icon" aria-hidden="true">{{ icon }}</div>
    <h3>{{ title }}</h3>
    <p>{{ description }}</p>
    <slot />
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ title: string; description: string; icon?: string }>(), { icon: '○' })
</script>
