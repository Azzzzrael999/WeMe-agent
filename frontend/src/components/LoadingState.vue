<template>
  <div class="feedback-state" role="status" aria-live="polite">
    <span class="spinner" aria-hidden="true" />
    <div><h3>{{ title }}</h3><p v-if="description">{{ description }}</p></div>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ title?: string; description?: string }>(), { title: '正在加载' })
</script>
