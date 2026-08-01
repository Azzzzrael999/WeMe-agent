<template>
  <div class="feedback-state feedback-state--error" role="alert">
    <div><h3>{{ title }}</h3><p>{{ message }}</p></div>
    <button v-if="retryable" class="ui-button ui-button--outline ui-button--sm" type="button" @click="$emit('retry')">重试</button>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ title?: string; message: string; retryable?: boolean }>(), { title: '暂时无法完成', retryable: false })
defineEmits<{ retry: [] }>()
</script>
