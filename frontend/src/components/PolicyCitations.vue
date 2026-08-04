<template>
  <div class="policy-citations">
    <article v-for="citation in citations" :key="citation.chunkId">
      <span class="policy-citations__icon" aria-hidden="true"><BookOpenCheck :size="16" /></span>
      <div>
        <span>可验证政策依据</span>
        <h3>{{ citation.title }}</h3>
        <p>{{ citation.headingPath.join(' / ') }}<template v-if="citation.page"> · 第 {{ citation.page }} 页</template></p>
        <code>{{ citation.chunkId }}</code>
      </div>
    </article>
    <div v-if="citations.length === 0" class="policy-citations__empty">
      <FileQuestion :size="21" aria-hidden="true" />
      <div><strong>未找到可验证证据</strong><p>只有 Agent 返回真实 citation 时才会显示出处，不会根据请求文本补写政策。</p></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { BookOpenCheck, FileQuestion } from '@lucide/vue'

import type { AgentCitation } from '@/api/types'

defineProps<{ citations: readonly AgentCitation[] }>()
</script>
