<template>
  <AppShell title="会议制度知识库" description="查阅智能编排使用的会议制度与会议规范。" eyebrow="协作 / 知识库">
    <template v-if="isAdmin" #actions>
      <button class="ui-button ui-button--default" type="button" @click="openUpload">
        <Upload :size="16" aria-hidden="true" />上传文档
      </button>
    </template>

    <section class="knowledge-filters content-panel" aria-label="知识库筛选">
      <label class="knowledge-filter-search"><span>搜索制度</span><input v-model.trim="filters.keyword" type="search" placeholder="标题、部门或文档标识" @keyup.enter="applyFilters" /></label>
      <label><span>文档类型</span><select v-model="filters.documentType"><option value="">全部类型</option><option v-for="option in typeOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select></label>
      <button class="ui-button ui-button--outline" type="button" @click="resetFilters"><RotateCcw :size="15" aria-hidden="true" />重置</button>
      <button class="ui-button ui-button--default" type="button" @click="applyFilters"><Search :size="15" aria-hidden="true" />查询</button>
    </section>

    <ErrorState v-if="listError" :message="listError" retryable @retry="loadDocuments" />
    <div v-else-if="loading" class="feedback-state" aria-live="polite"><span class="spinner" aria-hidden="true" />正在加载制度文档…</div>
    <EmptyState v-else-if="documents.length === 0" title="没有匹配的制度文档" description="调整搜索条件后重试。" icon="search" />
    <section v-else class="knowledge-workspace">
      <aside class="knowledge-list content-panel" aria-label="制度文档列表">
        <button
          v-for="document in documents"
          :key="document.documentId"
          class="knowledge-list-item"
          :class="{ active: selectedId === document.documentId }"
          type="button"
          @click="selectDocument(document.documentId)"
        >
          <span class="knowledge-file-icon" aria-hidden="true"><FileText :size="18" /></span>
          <span class="knowledge-list-copy"><strong>{{ document.title }}</strong><small>{{ typeLabel(document.documentType) }} · {{ document.department }}</small><small>版本 {{ document.version }} · {{ document.chunkCount }} 个检索片段</small></span>
          <span class="knowledge-index-status" :class="`knowledge-index-status--${document.status.toLowerCase()}`">{{ statusLabel(document.status) }}</span>
        </button>
        <footer class="pagination-bar">
          <span>共 {{ total }} 份 · 第 {{ page }} / {{ totalPages }} 页</span>
          <div><button class="ui-button ui-button--outline ui-button--sm" type="button" :disabled="page <= 1" @click="changePage(page - 1)"><ChevronLeft :size="15" aria-hidden="true" />上一页</button><button class="ui-button ui-button--outline ui-button--sm" type="button" :disabled="page >= totalPages" @click="changePage(page + 1)">下一页<ChevronRight :size="15" aria-hidden="true" /></button></div>
        </footer>
      </aside>

      <article class="knowledge-detail content-panel" aria-live="polite">
        <div v-if="detailLoading" class="feedback-state"><span class="spinner" aria-hidden="true" />正在打开文档…</div>
        <ErrorState v-else-if="detailError" :message="detailError" retryable @retry="reloadSelected" />
        <template v-else-if="selectedDocument">
          <header class="knowledge-detail-header">
            <div><p>{{ typeLabel(selectedDocument.documentType) }}</p><h2>{{ selectedDocument.title }}</h2><span>{{ selectedDocument.department }} · 版本 {{ selectedDocument.version }} · {{ selectedDocument.effectiveDate }} 生效</span></div>
            <div v-if="isAdmin" class="knowledge-detail-actions">
              <button v-if="selectedDocument.editable" class="ui-button ui-button--outline ui-button--sm" type="button" @click="openEditor"><Pencil :size="14" aria-hidden="true" />编辑</button>
              <button class="ui-button ui-button--destructive ui-button--sm" type="button" @click="requestDelete"><Trash2 :size="14" aria-hidden="true" />删除</button>
            </div>
          </header>
          <dl class="knowledge-metadata">
            <div><dt>索引状态</dt><dd>{{ statusLabel(selectedDocument.status) }}</dd></div>
            <div><dt>检索片段</dt><dd>{{ selectedDocument.chunkCount }} 个</dd></div>
            <div><dt>源文件</dt><dd>{{ selectedDocument.fileName }}</dd></div>
            <div><dt>最近更新</dt><dd>{{ formatDateTime(selectedDocument.updatedAt) }}</dd></div>
          </dl>
          <p v-if="selectedDocument.mediaType === 'application/pdf' && isAdmin" class="knowledge-pdf-note"><FileType2 :size="15" aria-hidden="true" />PDF 展示的是已提取文本；如需修改，请删除后重新上传同一文档标识。</p>
          <pre class="knowledge-content">{{ selectedDocument.content }}</pre>
        </template>
      </article>
    </section>

    <Teleport to="body">
      <div v-if="uploadOpen" class="drawer-layer">
        <button class="drawer-overlay" type="button" aria-label="关闭上传面板" @click="closeUpload" />
        <aside class="trace-drawer product-sheet" role="dialog" aria-modal="true" aria-labelledby="knowledge-upload-title">
          <header class="product-sheet__header"><div><p>知识库管理</p><h2 id="knowledge-upload-title">上传制度文档</h2></div><button class="icon-button" type="button" aria-label="关闭" @click="closeUpload"><X :size="18" aria-hidden="true" /></button></header>
          <form class="form-grid product-sheet__form" @submit.prevent="uploadDocument">
            <label class="form-span-2"><span>文档文件</span><input ref="fileInput" type="file" accept=".md,.pdf,text/markdown,application/pdf" :disabled="submitting" required @change="onFileChange" /><small>支持 UTF-8 Markdown 或文本型 PDF，最大 5 MiB、可浏览正文最多 50 万字符；扫描件不支持 OCR。</small></label>
            <template v-if="selectedFile?.name.toLowerCase().endsWith('.pdf')">
              <label><span>文档标识</span><input v-model.trim="pdfMetadata.documentId" pattern="doc_[a-z0-9_]+" maxlength="64" placeholder="doc_customer_meeting_policy" required /></label>
              <label><span>标题</span><input v-model.trim="pdfMetadata.title" maxlength="255" required /></label>
              <label><span>类型</span><select v-model="pdfMetadata.documentType"><option v-for="option in typeOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select></label>
              <label><span>适用部门</span><input v-model.trim="pdfMetadata.department" maxlength="64" required /></label>
              <label><span>业务版本</span><input v-model.trim="pdfMetadata.version" maxlength="32" required /></label>
              <label><span>生效日期</span><input v-model="pdfMetadata.effectiveDate" type="date" required /></label>
              <label><span>检索优先级</span><input v-model.number="pdfMetadata.priority" type="number" min="0" max="1000" required /></label>
            </template>
            <p v-if="formError" class="error-message form-span-2" role="alert">{{ formError }}</p>
            <footer class="product-sheet__actions form-span-2"><button class="ui-button ui-button--outline" type="button" :disabled="submitting" @click="closeUpload">返回</button><button class="ui-button ui-button--default" type="submit" :disabled="submitting || selectedFile === null">{{ submitting ? '正在解析并建立索引…' : '上传并建立索引' }}</button></footer>
          </form>
        </aside>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="editorOpen && selectedDocument" class="drawer-layer">
        <button class="drawer-overlay" type="button" aria-label="关闭文档编辑" @click="closeEditor" />
        <aside class="trace-drawer product-sheet knowledge-editor-sheet" role="dialog" aria-modal="true" aria-labelledby="knowledge-editor-title">
          <header class="product-sheet__header"><div><p>Markdown 源文档</p><h2 id="knowledge-editor-title">编辑“{{ selectedDocument.title }}”</h2></div><button class="icon-button" type="button" aria-label="关闭" @click="closeEditor"><X :size="18" aria-hidden="true" /></button></header>
          <form class="product-sheet__form knowledge-editor-form" @submit.prevent="saveDocument"><label><span>完整源文档</span><textarea v-model="editorContent" maxlength="500000" spellcheck="false" :disabled="submitting" required /></label><p>Front Matter 中的 documentId 不可改变；保存后会完整重建该文档的检索片段。</p><p v-if="formError" class="error-message" role="alert">{{ formError }}</p><footer class="product-sheet__actions"><button class="ui-button ui-button--outline" type="button" :disabled="submitting" @click="closeEditor">返回</button><button class="ui-button ui-button--default" type="submit" :disabled="submitting">{{ submitting ? '正在重建索引…' : '保存并重建索引' }}</button></footer></form>
        </aside>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="deleteTarget" class="dialog-layer">
        <button class="drawer-overlay" type="button" aria-label="关闭删除确认" @click="closeDelete" />
        <section class="ui-dialog ui-dialog--sm" role="alertdialog" aria-modal="true" aria-labelledby="knowledge-delete-title"><h2 id="knowledge-delete-title">删除“{{ deleteTarget.title }}”？</h2><p>该文档会立即从制度检索中移除。系统会保留删除标记，重启后不会被种子目录自动恢复。</p><p v-if="deleteError" class="error-message" role="alert">{{ deleteError }}</p><footer><button class="ui-button ui-button--outline" type="button" :disabled="submitting" @click="closeDelete">返回</button><button class="ui-button ui-button--destructive" type="button" :disabled="submitting" @click="deleteDocument">{{ submitting ? '正在删除…' : '确认删除' }}</button></footer></section>
      </div>
    </Teleport>
  </AppShell>
</template>

<script setup lang="ts">
import { ChevronLeft, ChevronRight, FileText, FileType2, Pencil, RotateCcw, Search, Trash2, Upload, X } from '@lucide/vue'
import { computed, onMounted, reactive, ref } from 'vue'

import { ApiError, apiRequest } from '@/api/client'
import type { KnowledgeDocument, KnowledgeDocumentDeleteResult, KnowledgeDocumentListResult, KnowledgeDocumentType } from '@/api/types'
import { authStore } from '@/auth/store'
import AppShell from '@/components/AppShell.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import { useModalFocus } from '@/composables/useModalFocus'
import { formatDateTime } from '@/utils/format'

const PAGE_SIZE = 20
const typeOptions: Array<{ value: KnowledgeDocumentType; label: string }> = [
  { value: 'MEETING_POLICY', label: '会议管理制度' },
  { value: 'MEETING_STANDARD', label: '会议执行规范' },
  { value: 'ROOM_POLICY', label: '会议室制度' },
  { value: 'SECURITY_POLICY', label: '安全与保密' },
  { value: 'EQUIPMENT_GUIDE', label: '设备指引' },
  { value: 'DEPARTMENT_POLICY', label: '部门制度' },
  { value: 'FAQ', label: '常见问题' },
]
const documents = ref<KnowledgeDocument[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(true)
const listError = ref('')
const detailLoading = ref(false)
const detailError = ref('')
const selectedId = ref<string | null>(null)
const selectedDocument = ref<KnowledgeDocument | null>(null)
const uploadOpen = ref(false)
const editorOpen = ref(false)
const deleteTarget = ref<KnowledgeDocument | null>(null)
const deleteError = ref('')
const formError = ref('')
const submitting = ref(false)
const selectedFile = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const editorContent = ref('')
const filters = reactive({ keyword: '', documentType: '' })
const pdfMetadata = reactive({ documentId: '', title: '', documentType: 'MEETING_POLICY' as KnowledgeDocumentType, department: 'ALL', version: '1.0', effectiveDate: new Date().toISOString().slice(0, 10), priority: 100 })
const isAdmin = computed(() => authStore.state.user?.roles.includes('ADMIN') === true)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const modalOpen = computed(() => uploadOpen.value || editorOpen.value || deleteTarget.value !== null)
useModalFocus(modalOpen, closeAllOverlays)

function asMessage(error: unknown, fallback: string): string { return error instanceof ApiError ? error.message : fallback }
function typeLabel(value: string): string { return typeOptions.find((item) => item.value === value)?.label ?? '会议制度' }
function statusLabel(value: string): string { if (value === 'INDEXED') return '已建立索引'; if (value === 'INDEXING') return '索引中'; if (value === 'FAILED') return '索引失败'; return '待处理' }

async function loadDocuments(): Promise<void> {
  loading.value = true; listError.value = ''
  const query = new URLSearchParams({ page: String(page.value), size: String(PAGE_SIZE) })
  if (filters.keyword) query.set('keyword', filters.keyword)
  if (filters.documentType) query.set('documentType', filters.documentType)
  try {
    const result = await apiRequest<KnowledgeDocumentListResult>(`/knowledge-documents?${query.toString()}`)
    documents.value = result.items; total.value = result.total
    if (documents.value.length === 0) { selectedId.value = null; selectedDocument.value = null }
    else if (!documents.value.some((item) => item.documentId === selectedId.value)) { await selectDocument(documents.value[0].documentId) }
  } catch (error) { listError.value = asMessage(error, '制度文档加载失败，请稍后重试。') }
  finally { loading.value = false }
}
async function selectDocument(documentId: string): Promise<void> {
  selectedId.value = documentId; detailLoading.value = true; detailError.value = ''
  try { selectedDocument.value = await apiRequest<KnowledgeDocument>(`/knowledge-documents/${documentId}`) }
  catch (error) { detailError.value = asMessage(error, '文档正文加载失败。'); selectedDocument.value = null }
  finally { detailLoading.value = false }
}
function reloadSelected(): void { if (selectedId.value) void selectDocument(selectedId.value) }
function applyFilters(): void { page.value = 1; void loadDocuments() }
function resetFilters(): void { Object.assign(filters, { keyword: '', documentType: '' }); applyFilters() }
function changePage(value: number): void { page.value = value; void loadDocuments() }
function openUpload(): void { selectedFile.value = null; formError.value = ''; uploadOpen.value = true }
function closeUpload(): void { uploadOpen.value = false; selectedFile.value = null; if (fileInput.value) fileInput.value.value = '' }
function onFileChange(event: Event): void { selectedFile.value = (event.target as HTMLInputElement).files?.[0] ?? null; formError.value = '' }
function openEditor(): void { if (!selectedDocument.value?.editable) return; editorContent.value = selectedDocument.value.content ?? ''; formError.value = ''; editorOpen.value = true }
function closeEditor(): void { editorOpen.value = false; editorContent.value = ''; formError.value = '' }
function requestDelete(): void { deleteError.value = ''; deleteTarget.value = selectedDocument.value }
function closeDelete(): void { deleteTarget.value = null; deleteError.value = '' }
function closeAllOverlays(): void { closeUpload(); closeEditor(); closeDelete() }

async function uploadDocument(): Promise<void> {
  const file = selectedFile.value
  if (file === null || submitting.value) return
  if (file.size <= 0 || file.size > 5 * 1024 * 1024) { formError.value = '文件必须小于 5 MiB。'; return }
  submitting.value = true; formError.value = ''
  const body = new FormData(); body.append('file', file)
  if (file.name.toLowerCase().endsWith('.pdf')) body.append('metadata', JSON.stringify({ ...pdfMetadata, status: 'ACTIVE', timezone: 'Asia/Shanghai' }))
  try {
    const created = await apiRequest<KnowledgeDocument>('/admin/knowledge-documents', { method: 'POST', body })
    closeUpload(); page.value = 1; await loadDocuments(); await selectDocument(created.documentId)
  } catch (error) { formError.value = asMessage(error, '文档上传失败，请检查格式和元数据。') }
  finally { submitting.value = false }
}
async function saveDocument(): Promise<void> {
  const document = selectedDocument.value
  if (document === null || submitting.value) return
  submitting.value = true; formError.value = ''
  try {
    const updated = await apiRequest<KnowledgeDocument>(`/admin/knowledge-documents/${document.documentId}`, { method: 'PUT', body: JSON.stringify({ content: editorContent.value, expectedVersion: document.recordVersion }) })
    closeEditor(); await loadDocuments(); await selectDocument(updated.documentId)
  } catch (error) { formError.value = asMessage(error, '文档保存失败，请刷新后重试。') }
  finally { submitting.value = false }
}
async function deleteDocument(): Promise<void> {
  const document = deleteTarget.value
  if (document === null || submitting.value) return
  submitting.value = true; deleteError.value = ''
  try {
    await apiRequest<KnowledgeDocumentDeleteResult>(`/admin/knowledge-documents/${document.documentId}?expectedVersion=${document.recordVersion}`, { method: 'DELETE' })
    deleteTarget.value = null; selectedId.value = null; selectedDocument.value = null
    if (documents.value.length === 1 && page.value > 1) page.value -= 1
    await loadDocuments()
  } catch (error) { deleteError.value = asMessage(error, '文档删除失败，请刷新后重试。') }
  finally { submitting.value = false }
}

onMounted(() => { void loadDocuments() })
</script>
