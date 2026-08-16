<template>
  <fieldset class="participant-name-input" :disabled="disabled">
    <legend>{{ label }}</legend>
    <div class="participant-name-input__entry">
      <input
        v-model="nameInput"
        type="text"
        :aria-label="`${label}姓名`"
        placeholder="输入姓名，多个姓名用逗号分隔"
        :disabled="disabled"
        @input="inputError = ''"
        @keydown.enter.prevent="addParticipants"
      />
      <button class="ui-button ui-button--outline ui-button--sm" type="button" :disabled="disabled || nameInput.trim().length === 0" @click="addParticipants">
        添加
      </button>
    </div>
    <p v-if="inputError" class="participant-name-input__error" role="alert">{{ inputError }}</p>
    <ul v-if="selectedParticipants.length > 0" class="participant-name-input__selected" :aria-label="`${label}已添加人员`">
      <li v-for="employee in selectedParticipants" :key="employee.id">
        <span>
          <strong>{{ employee.displayName }}</strong>
          <small v-if="employee.departmentName">{{ employee.departmentName }}</small>
        </span>
        <button type="button" :aria-label="`移除${employee.displayName}`" :disabled="disabled" @click="removeParticipant(employee.id)">移除</button>
      </li>
    </ul>
    <small class="participant-name-input__help">
      {{ selectedParticipants.length > 0 ? `已添加 ${selectedParticipants.length} 人。` : emptyLabel }} 输入姓名即可；重名请补充“姓名（部门）”。
    </small>
  </fieldset>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import type { EmployeeDirectoryItem } from '@/api/types'

const props = withDefaults(defineProps<{
  label: string
  modelValue: number[]
  employees: readonly EmployeeDirectoryItem[]
  excludedIds?: readonly number[]
  disabled?: boolean
  emptyLabel?: string
}>(), { excludedIds: () => [], disabled: false, emptyLabel: '暂未添加人员。' })

const emit = defineEmits<{ 'update:modelValue': [value: number[]] }>()

const nameInput = ref('')
const inputError = ref('')
const selectedParticipants = computed(() => props.modelValue.flatMap((employeeId) => {
  const employee = props.employees.find((item) => item.id === employeeId)
  return employee === undefined ? [] : [employee]
}))

function normalizeName(value: string): string {
  return value.trim().replace(/\s+/gu, '').replace(/\(/gu, '（').replace(/\)/gu, '）')
}

function matchesName(employee: EmployeeDirectoryItem, input: string): boolean {
  const displayName = normalizeName(employee.displayName)
  if (input === displayName) return true
  return employee.departmentName !== null
    && input === `${displayName}（${normalizeName(employee.departmentName)}）`
}

function addParticipants(): void {
  const names = nameInput.value
    .split(/[，,、；;\n]+/u)
    .map((value) => ({ original: value.trim(), normalized: normalizeName(value) }))
    .filter((value) => value.normalized.length > 0)

  if (names.length === 0) {
    inputError.value = '请输入至少一位参会人的姓名。'
    return
  }

  const nextIds = new Set(props.modelValue)
  const errors: string[] = []
  for (const name of names) {
    const matches = props.employees.filter((employee) => matchesName(employee, name.normalized))
    if (matches.length === 0) {
      errors.push(`未找到在职员工“${name.original}”。`)
      continue
    }
    if (matches.length > 1) {
      errors.push(`“${name.original}”存在多位同名员工，请输入“姓名（部门）”。`)
      continue
    }
    const employee = matches[0]
    if (props.excludedIds.includes(employee.id)) {
      errors.push(`“${employee.displayName}”已在另一类参会者中。`)
      continue
    }
    nextIds.add(employee.id)
  }

  if (errors.length > 0) {
    inputError.value = errors.join(' ')
    return
  }

  emit('update:modelValue', [...nextIds].sort((left, right) => left - right))
  nameInput.value = ''
  inputError.value = ''
}

function removeParticipant(employeeId: number): void {
  emit('update:modelValue', props.modelValue.filter((id) => id !== employeeId))
  inputError.value = ''
}
</script>
