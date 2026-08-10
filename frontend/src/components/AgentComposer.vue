<template>
  <form class="agent-composer" @submit.prevent="$emit('submit')">
    <label for="agent-request" class="sr-only">描述会议编排需求</label>
    <textarea
      id="agent-request"
      ref="textareaRef"
      :value="modelValue"
      rows="1"
      maxlength="4000"
      :disabled="disabled"
      placeholder="告诉 MeetOps 会议目标、时间、参与者与资源要求…"
      @input="update"
      @keydown="handleKeydown"
    />
    <div class="agent-composer__footer">
      <span>{{ modelValue.length }} / 4000</span>
      <button
        class="agent-composer__send"
        type="submit"
        :disabled="disabled || modelValue.trim().length === 0"
        :aria-label="streaming ? '正在编排' : '发送会议编排请求'"
        :title="streaming ? '正在编排' : '发送'"
      >
        <LoaderCircle v-if="streaming" class="composer-spinner" :size="18" aria-hidden="true" />
        <ArrowUp v-else :size="19" aria-hidden="true" />
      </button>
    </div>
  </form>
</template>

<script setup lang="ts">
import { ArrowUp, LoaderCircle } from '@lucide/vue'
import { nextTick, onMounted, ref, watch } from 'vue'

const props = defineProps<{ modelValue: string; disabled: boolean; streaming: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; submit: [] }>()
const textareaRef = ref<HTMLTextAreaElement | null>(null)

function resize(): void {
  const textarea = textareaRef.value
  if (textarea === null) return
  textarea.style.height = 'auto'
  textarea.style.height = `${Math.min(textarea.scrollHeight, 176)}px`
}

function update(event: Event): void {
  if (event.target instanceof HTMLTextAreaElement) {
    emit('update:modelValue', event.target.value)
    resize()
  }
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  if (!props.disabled && props.modelValue.trim().length > 0) {
    emit('submit')
  }
}

watch(() => props.modelValue, () => void nextTick(resize))
onMounted(resize)
</script>
