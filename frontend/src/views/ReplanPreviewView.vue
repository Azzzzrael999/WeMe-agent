<template>
  <AppShell title="异常重排" description="在资源失效时比较原计划与替代方案，保留人工决策边界。" eyebrow="产品预览 / 异常重排">
    <template #actions><ProductPreviewBadge /></template>
    <div class="preview-banner"><ProductPreviewBadge /><p>以下内容使用明确的静态演示数据，不会读取或修改真实会议。</p></div>
    <div class="preview-grid">
      <section class="content-panel preview-event"><span class="preview-icon"><TriangleAlert :size="20" aria-hidden="true" /></span><div><p>资源异常</p><h2>{{ replanPreview.event }}</h2><span>影响 {{ replanPreview.affected.length }} 场会议 · 需人工确认替代计划</span></div></section>
      <section class="content-panel"><h2>受影响会议</h2><ul class="clean-list"><li v-for="meeting in replanPreview.affected" :key="meeting"><CalendarClock :size="16" aria-hidden="true" /><strong>{{ meeting }}</strong><StatusBadge status="CONFLICT" /></li></ul></section>
    </div>
    <section class="content-panel preview-section"><div class="section-heading"><div><h2>计划差异</h2><p>硬约束保持不变，仅展示可解释的替代方案。</p></div></div><PlanDiff :before="replanPreview.before" :after="replanPreview.after" /></section>
    <section class="content-panel preview-section"><h2>约束变化与放宽原因</h2><dl class="preview-facts"><div><dt>约束变化</dt><dd>{{ replanPreview.constraintChange }}</dd></div><div><dt>放宽原因</dt><dd>{{ replanPreview.relaxationReason }}</dd></div></dl><h3>未受影响项</h3><div class="check-list"><span v-for="item in replanPreview.preserved" :key="item"><Check :size="13" aria-hidden="true" />{{ item }}</span></div><button class="ui-button ui-button--default" type="button" @click="notice=true">应用替代计划</button><p v-if="notice" class="preview-notice" role="status">产品预览尚未连接后端，未执行任何写操作。</p></section>
  </AppShell>
</template>
<script setup lang="ts">
import AppShell from '@/components/AppShell.vue'
import { CalendarClock, Check, TriangleAlert } from '@lucide/vue'
import { ref } from 'vue'
import PlanDiff from '@/components/PlanDiff.vue'
import ProductPreviewBadge from '@/components/ProductPreviewBadge.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { replanPreview } from '@/demo/preview'
const notice = ref(false)
</script>
