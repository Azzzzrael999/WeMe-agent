<template>
  <button
    :class="cn('ui-button', `ui-button--${variant}`, `ui-button--${size}`, $attrs.class as string)"
    :type="type"
  >
    <slot />
  </button>
</template>

<script setup lang="ts">
import { cn } from '@/lib/utils'

withDefaults(defineProps<{
  variant?: 'default' | 'secondary' | 'ghost' | 'outline' | 'destructive'
  size?: 'default' | 'sm' | 'icon'
  type?: 'button' | 'submit' | 'reset'
}>(), { variant: 'default', size: 'default', type: 'button' })
</script>
