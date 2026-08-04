<template>
  <AppShell title="会前会后" description="聚合会前准备与会后行动草案的产品方向。" eyebrow="产品预览 / 会前会后">
    <template #actions><ProductPreviewBadge /></template>
    <div class="preview-banner"><ProductPreviewBadge /><p>以下内容为静态产品方向预览，不会创建材料或行动项。</p></div>
    <div class="preview-tabs" role="tablist"><button type="button" :class="{active:tab==='before'}" @click="tab='before'">会前准备</button><button type="button" :class="{active:tab==='after'}" @click="tab='after'">会后行动</button></div>
    <section v-if="tab==='before'" class="content-panel lifecycle-list"><header><div><h2>架构评审 · 会前检查</h2><p>自动汇总人员、资源、材料与政策检查。</p></div><StatusBadge status="WAITING_USER_INPUT" label="1 项缺失" /></header><article v-for="item in lifecyclePreview.preparation" :key="item.title"><span class="lifecycle-icon"><Check :size="16" aria-hidden="true" /></span><div><strong>{{ item.title }}</strong><p>{{ item.detail }}</p></div><StatusBadge :status="item.status" /></article></section>
    <section v-else class="content-panel lifecycle-list"><header><div><h2>会议行动项草案</h2><p>建议负责人、期限和依赖仍需人工确认。</p></div><StatusBadge status="WAITING_CONFIRMATION" /></header><div class="decision-callout"><span>会议决策草案</span><p>{{ lifecyclePreview.decision }}</p></div><article v-for="item in lifecyclePreview.actions" :key="item.title"><span class="lifecycle-icon"><Square :size="15" aria-hidden="true" /></span><div><strong>{{ item.title }}</strong><p>{{ item.type }} · 负责人 {{ item.owner }} · 截止 {{ item.due }}</p><small>依赖：{{ item.dependency }}</small></div><StatusBadge :status="item.status" /></article></section>
    <button class="ui-button ui-button--default preview-action" type="button" @click="notice=true">{{ tab==='before' ? '生成准备清单' : '确认行动项草案' }}</button><p v-if="notice" class="preview-notice" role="status">产品预览尚未连接后端，未执行任何写操作。</p>
  </AppShell>
</template>
<script setup lang="ts">
import AppShell from '@/components/AppShell.vue'
import { Check, Square } from '@lucide/vue'
import { ref } from 'vue'
import ProductPreviewBadge from '@/components/ProductPreviewBadge.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { lifecyclePreview } from '@/demo/preview'
const tab = ref<'before' | 'after'>('before')
const notice = ref(false)
</script>
