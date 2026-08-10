<template>
  <label class="employee-multi-select">
    <span>{{ label }}</span>
    <select
      multiple
      :value="modelValue.map(String)"
      :disabled="disabled"
      :aria-label="label"
      @change="updateSelection"
    >
      <option
        v-for="employee in employees"
        :key="employee.id"
        :value="employee.id"
        :disabled="excludedIds.includes(employee.id)"
      >
        {{ employee.displayName }}{{ employee.departmentName ? ` · ${employee.departmentName}` : '' }}
      </option>
    </select>
    <small>{{ modelValue.length > 0 ? `已选择 ${modelValue.length} 人` : emptyLabel }}</small>
  </label>
</template>

<script setup lang="ts">
import type { EmployeeDirectoryItem } from '@/api/types'

withDefaults(defineProps<{
  label: string
  modelValue: number[]
  employees: readonly EmployeeDirectoryItem[]
  excludedIds?: readonly number[]
  disabled?: boolean
  emptyLabel?: string
}>(), { excludedIds: () => [], disabled: false, emptyLabel: '暂未选择' })

const emit = defineEmits<{ 'update:modelValue': [value: number[]] }>()

function updateSelection(event: Event): void {
  const select = event.target as HTMLSelectElement
  emit('update:modelValue', [...select.selectedOptions].map((option) => Number(option.value)))
}
</script>
