<template>
  <header class="page-header">
    <div class="page-header__copy">
      <nav v-if="eyebrow" class="page-header__eyebrow" aria-label="面包屑">{{ eyebrow }}</nav>
      <h1>{{ title }}</h1>
      <p v-if="description">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="page-header__actions"><slot name="actions" /></div>
  </header>
</template>

<script setup lang="ts">
defineProps<{ title: string; description?: string; eyebrow?: string }>()
</script>
