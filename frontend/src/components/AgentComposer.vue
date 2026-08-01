<template>
  <form class="agent-composer" @submit.prevent="$emit('submit')">
    <label for="agent-request" class="sr-only">描述会议编排需求</label>
    <textarea id="agent-request" :value="modelValue" rows="4" maxlength="4000" :disabled="disabled" placeholder="描述会议目标、时间、参与者与资源要求…" @input="update" />
    <div class="agent-composer__footer">
      <span>{{ modelValue.length }} / 4000 · Asia/Shanghai</span>
      <button class="ui-button ui-button--default" type="submit" :disabled="disabled || modelValue.trim().length === 0">
        <span aria-hidden="true">✦</span>{{ streaming ? '正在编排…' : '开始编排' }}
      </button>
    </div>
  </form>
</template>

<script setup lang="ts">
defineProps<{ modelValue: string; disabled: boolean; streaming: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; submit: [] }>()
function update(event: Event): void { if (event.target instanceof HTMLTextAreaElement) emit('update:modelValue', event.target.value) }
</script>
