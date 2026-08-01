<template>
  <WorkspaceShell>
    <PageHeader :title="title" :description="description" :eyebrow="eyebrow"><template v-if="$slots.actions" #actions><slot name="actions" /></template></PageHeader>
    <slot />
  </WorkspaceShell>
</template>

<script setup lang="ts">
import PageHeader from './PageHeader.vue'
import WorkspaceShell from './WorkspaceShell.vue'

defineProps<{
  title: string
  description?: string
  eyebrow?: string
}>()
</script>
