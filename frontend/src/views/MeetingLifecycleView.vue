<template>
  <AppShell
    title="会前会后"
    description="管理会前准备、会后记录与行动项。"
    eyebrow="协作 / 会前会后"
  >
    <template #actions>
      <button
        class="ui-button ui-button--outline"
        type="button"
        :disabled="meetingListLoading || lifecycleLoading || selectedMeetingId === null"
        @click="loadLifecycle()"
      >
        <RefreshCw :size="16" aria-hidden="true" />刷新当前会议
      </button>
    </template>

    <section class="content-panel lifecycle-meeting-picker" aria-labelledby="lifecycle-meeting-picker-title">
      <div>
        <p class="eyebrow">会议范围</p>
        <h2 id="lifecycle-meeting-picker-title">选择可见会议</h2>
        <p>选择一场会议开始准备或整理会后记录。</p>
      </div>
      <label v-if="meetings.length > 0">
        <span class="sr-only">选择会议</span>
        <select v-model.number="selectedMeetingId" :disabled="meetingListLoading" @change="handleMeetingSelection">
          <option v-for="meeting in meetings" :key="meeting.id" :value="meeting.id">
            {{ meetingOptionLabel(meeting) }}
          </option>
        </select>
      </label>
    </section>

    <LoadingState
      v-if="meetingListLoading"
      title="正在加载可见会议"
      description="正在同步你可访问的会议。"
    />
    <ErrorState
      v-else-if="meetingListError"
      title="会议列表加载失败"
      :message="meetingListError"
      retryable
      @retry="loadMeetingChoices"
    />
    <EmptyState
      v-else-if="meetings.length === 0"
      title="暂无可处理的会议"
      description="创建或参与会议后，可在这里维护会前准备和会后执行。"
      icon="calendar"
    />
    <LoadingState
      v-else-if="lifecycleLoading"
      title="正在同步会议生命周期"
      description="正在同步议程、材料和会后记录。"
    />
    <ErrorState
      v-else-if="lifecycleError"
      title="生命周期数据加载失败"
      :message="lifecycleError"
      retryable
      @retry="loadLifecycle"
    />

    <template v-else-if="lifecycle">
      <section class="content-panel lifecycle-meeting-summary">
        <div class="lifecycle-meeting-summary__title">
          <div class="lifecycle-heading-icon"><CalendarDays :size="18" aria-hidden="true" /></div>
          <div>
            <h2>{{ lifecycle.meeting.title }}</h2>
          </div>
          <StatusBadge :status="lifecycle.meeting.status" />
        </div>
        <dl>
          <div><dt><Clock3 :size="14" aria-hidden="true" />时间</dt><dd>{{ formatDateTime(lifecycle.meeting.startAt) }} — {{ formatDateTime(lifecycle.meeting.endAt) }}</dd></div>
          <div><dt><MapPin :size="14" aria-hidden="true" />会议室</dt><dd>{{ lifecycle.meeting.roomName }}</dd></div>
          <div><dt><Users :size="14" aria-hidden="true" />组织者</dt><dd>{{ lifecycle.meeting.organizerName }} · {{ lifecycle.meeting.participants.length }} 位参会者</dd></div>
        </dl>
      </section>

      <p v-if="operationNotice" class="lifecycle-feedback lifecycle-feedback--success" role="status">
        {{ operationNotice }}
      </p>
      <p v-if="operationError" class="lifecycle-feedback lifecycle-feedback--error" role="alert">
        {{ operationError }}
      </p>

      <section v-if="lifecycle.meeting.status === 'CANCELLED'" class="content-panel lifecycle-terminal-state">
        <AlertTriangle :size="24" aria-hidden="true" />
        <div>
          <h2>会议已取消</h2>
          <p>取消会议只保留历史准备信息供查看，不能再编辑准备内容或生成会后记录。</p>
        </div>
      </section>

      <template v-else-if="lifecycle.meeting.status === 'CONFIRMED'">
        <section v-if="!isFutureMeeting" class="content-panel lifecycle-terminal-state">
          <Clock3 :size="24" aria-hidden="true" />
          <div>
            <h2>等待会议自动完成</h2>
            <p>会议已开始或已经到达结束时间。服务端转换为 COMPLETED 后，才可提交文本会议记录。</p>
          </div>
        </section>

        <div class="lifecycle-layout">
          <section class="content-panel lifecycle-checklist" aria-labelledby="preparation-checklist-title">
            <header class="lifecycle-section-heading">
              <div>
                <p class="eyebrow">动态检查</p>
                <h2 id="preparation-checklist-title">准备清单</h2>
                <p>每次读取都按当前会议、房间、人员、议程和材料事实重新计算。</p>
              </div>
              <StatusBadge
                :status="lifecycle.preparation.checklist.status"
                :label="lifecycle.preparation.checklist.status === 'READY' ? '准备就绪' : '需要处理'"
              />
            </header>
            <ul>
              <li v-for="item in lifecycle.preparation.checklist.items" :key="item.code" :class="{ 'is-passed': item.passed }">
                <CheckCircle2 v-if="item.passed" :size="17" aria-hidden="true" />
                <AlertTriangle v-else :size="17" aria-hidden="true" />
                <div><strong>{{ checklistLabel(item.code) }}</strong><p>{{ item.message }}</p></div>
              </li>
            </ul>
            <p class="lifecycle-generated-at">生成于 {{ formatDateTime(lifecycle.preparation.checklist.generatedAt) }}</p>
          </section>

          <section class="content-panel lifecycle-preparation-editor" aria-labelledby="agenda-editor-title">
            <header class="lifecycle-section-heading">
              <div>
                <p class="eyebrow">会前准备</p>
                <h2 id="agenda-editor-title">会议议程</h2>
                <p>按顺序维护议题、负责人和预计时长。</p>
              </div>
            </header>

            <div v-if="agendaForms.length === 0" class="lifecycle-inline-empty">尚未配置议题。</div>
            <article v-for="(item, index) in agendaForms" :key="item.clientId" class="lifecycle-editor-row">
              <div class="lifecycle-row-order"><span>{{ index + 1 }}</span></div>
              <div class="lifecycle-row-fields lifecycle-row-fields--agenda">
                <label><span>议题</span><input v-model.trim="item.topic" maxlength="200" :disabled="!canEditPreparation" /></label>
                <label><span>负责人</span><select v-model.number="item.ownerEmployeeId" :disabled="!canEditPreparation"><option v-for="person in meetingPeople" :key="person.id" :value="person.id">{{ person.name }}</option></select></label>
                <label><span>预计分钟</span><input v-model.number="item.plannedMinutes" type="number" min="5" max="240" step="5" :disabled="!canEditPreparation" /></label>
              </div>
              <div v-if="canEditPreparation" class="lifecycle-row-actions">
                <button class="icon-button" type="button" :disabled="index === 0" aria-label="上移议题" @click="moveItem(agendaForms, index, -1)"><ArrowUp :size="15" aria-hidden="true" /></button>
                <button class="icon-button" type="button" :disabled="index === agendaForms.length - 1" aria-label="下移议题" @click="moveItem(agendaForms, index, 1)"><ArrowDown :size="15" aria-hidden="true" /></button>
                <button class="icon-button lifecycle-delete-button" type="button" aria-label="删除议题" @click="agendaForms.splice(index, 1)"><Trash2 :size="15" aria-hidden="true" /></button>
              </div>
            </article>
            <button v-if="canEditPreparation" class="ui-button ui-button--outline ui-button--sm" type="button" :disabled="agendaForms.length >= 30" @click="addAgendaItem">
              <Plus :size="15" aria-hidden="true" />添加议题
            </button>
          </section>

          <section class="content-panel lifecycle-preparation-editor lifecycle-preparation-editor--materials" aria-labelledby="materials-editor-title">
            <header class="lifecycle-section-heading">
              <div>
                <p class="eyebrow">材料元数据</p>
                <h2 id="materials-editor-title">评审材料</h2>
                <p>仅记录名称、负责人、版本和就绪状态，不上传或保存附件。</p>
              </div>
            </header>

            <div v-if="materialForms.length === 0" class="lifecycle-inline-empty">尚未登记材料。</div>
            <article v-for="(item, index) in materialForms" :key="item.clientId" class="lifecycle-editor-row lifecycle-editor-row--material">
              <div class="lifecycle-row-order"><span>{{ index + 1 }}</span></div>
              <div class="lifecycle-row-fields lifecycle-row-fields--material">
                <label><span>材料名称</span><input v-model.trim="item.title" maxlength="200" :disabled="!canEditPreparation" /></label>
                <label><span>负责人</span><select v-model.number="item.ownerEmployeeId" :disabled="!canEditPreparation"><option v-for="person in meetingPeople" :key="person.id" :value="person.id">{{ person.name }}</option></select></label>
                <label><span>版本</span><input v-model.trim="item.versionLabel" maxlength="64" placeholder="例如 v3" :disabled="!canEditPreparation" /></label>
                <label><span>状态</span><select v-model="item.status" :disabled="!canEditPreparation"><option value="MISSING">缺失</option><option value="READY">已就绪</option></select></label>
                <label class="lifecycle-checkbox"><input v-model="item.required" type="checkbox" :disabled="!canEditPreparation" /><span>必需材料</span></label>
                <label class="lifecycle-material-note"><span>备注</span><input v-model.trim="item.note" maxlength="500" :disabled="!canEditPreparation" /></label>
              </div>
              <div v-if="canEditPreparation" class="lifecycle-row-actions">
                <button class="icon-button" type="button" :disabled="index === 0" aria-label="上移材料" @click="moveItem(materialForms, index, -1)"><ArrowUp :size="15" aria-hidden="true" /></button>
                <button class="icon-button" type="button" :disabled="index === materialForms.length - 1" aria-label="下移材料" @click="moveItem(materialForms, index, 1)"><ArrowDown :size="15" aria-hidden="true" /></button>
                <button class="icon-button lifecycle-delete-button" type="button" aria-label="删除材料" @click="materialForms.splice(index, 1)"><Trash2 :size="15" aria-hidden="true" /></button>
              </div>
            </article>
            <div v-if="canEditPreparation" class="lifecycle-editor-footer">
              <button class="ui-button ui-button--outline ui-button--sm" type="button" :disabled="materialForms.length >= 50" @click="addMaterialItem">
                <Plus :size="15" aria-hidden="true" />添加材料
              </button>
              <span>议程合计 {{ agendaTotalMinutes }} 分钟 / 会议 {{ meetingDurationMinutes }} 分钟</span>
              <button class="ui-button ui-button--default" type="button" :disabled="preparationSaving" @click="savePreparation">
                {{ preparationSaving ? '正在保存…' : '保存会前准备' }}
              </button>
            </div>
            <p v-else class="lifecycle-permission-note">当前账号可查看准备内容，但只有会议组织者或管理员可以修改。</p>
          </section>
        </div>
      </template>

      <template v-else-if="lifecycle.meeting.status === 'COMPLETED'">
        <section v-if="hasFormalPostMeetingRecord" class="content-panel lifecycle-formal-record" aria-labelledby="formal-record-title">
          <header class="lifecycle-section-heading">
            <div>
              <p class="eyebrow">正式会后记录</p>
              <h2 id="formal-record-title">纪要与决策</h2>
              <p>以下内容已经人工接受并写入业务记录。</p>
            </div>
            <StatusBadge status="ACCEPTED" label="已接受" />
          </header>
          <div v-if="lifecycle.postMeeting.minutes" class="lifecycle-minutes-grid">
            <article><span>会议背景</span><p>{{ lifecycle.postMeeting.minutes.background || '—' }}</p></article>
            <article><span>讨论摘要</span><p>{{ lifecycle.postMeeting.minutes.discussionSummary || '—' }}</p></article>
            <article><span>最终结论</span><p>{{ lifecycle.postMeeting.minutes.conclusion || '—' }}</p></article>
          </div>
          <div class="lifecycle-confirmation-meta">
            确认人 {{ employeeName(lifecycle.postMeeting.minutes?.confirmedBy) }} ·
            {{ formatDateTime(lifecycle.postMeeting.minutes?.confirmedAt) }}
          </div>
          <section class="lifecycle-record-list">
            <h3>决策记录</h3>
            <p v-if="lifecycle.postMeeting.decisions.length === 0" class="lifecycle-inline-empty">没有正式决策记录。</p>
            <article v-for="decision in lifecycle.postMeeting.decisions" :key="decision.id">
              <span class="lifecycle-sequence">{{ decision.sequenceNo }}</span>
              <div><strong>{{ decision.content }}</strong><p>{{ decision.rationale || '未记录额外依据。' }}</p></div>
            </article>
          </section>
        </section>

        <section v-if="hasFormalPostMeetingRecord" class="content-panel lifecycle-action-items" aria-labelledby="formal-actions-title">
          <header class="lifecycle-section-heading">
            <div>
              <p class="eyebrow">执行闭环</p>
              <h2 id="formal-actions-title">行动项</h2>
              <p>负责人、会议组织者或管理员可按允许的状态流转更新。</p>
            </div>
          </header>
          <EmptyState v-if="lifecycle.postMeeting.actionItems.length === 0" title="没有行动项" description="本次会议没有形成需要跟踪的正式任务。" icon="check" />
          <article v-for="item in lifecycle.postMeeting.actionItems" v-else :key="item.id" class="lifecycle-action-card">
            <div class="lifecycle-action-card__main">
              <div><span>行动项 {{ item.sequenceNo }}</span><StatusBadge :status="item.status" /></div>
              <h3>{{ item.title }}</h3>
              <p>{{ item.description || '未记录补充说明。' }}</p>
              <small>负责人 {{ item.assigneeName }} · 截止 {{ formatDateTime(item.dueAt) }}</small>
            </div>
            <div v-if="canUpdateActionItem(item)" class="lifecycle-action-card__controls">
              <select :value="actionStatusEdits[item.id] ?? item.status" :disabled="actionUpdatingId === item.id" @change="setActionStatus(item.id, $event)">
                <option v-for="status in availableActionStatuses(item.status)" :key="status" :value="status">{{ actionStatusLabel(status) }}</option>
              </select>
              <button class="ui-button ui-button--outline ui-button--sm" type="button" :disabled="actionUpdatingId === item.id || (actionStatusEdits[item.id] ?? item.status) === item.status" @click="saveActionStatus(item)">
                {{ actionUpdatingId === item.id ? '更新中…' : '更新状态' }}
              </button>
            </div>
            <p v-else class="lifecycle-permission-note">当前账号只有查看权限。</p>
          </article>
        </section>

        <section v-if="draft?.status === 'PROCESSING'" class="content-panel lifecycle-terminal-state">
          <span class="spinner" aria-hidden="true" />
          <div><h2>正在生成会后草案</h2><p>Requirement Agent 正在把文本记录转换为纪要、决策和行动项，完成后请刷新。</p></div>
        </section>

        <section v-if="shouldShowTranscriptForm" class="content-panel lifecycle-transcript" aria-labelledby="transcript-title">
          <header class="lifecycle-section-heading">
            <div>
              <p class="eyebrow">文本会议记录</p>
              <h2 id="transcript-title">生成会后草案</h2>
              <p>提交纯文本记录，由现有 Agent 生成待审内容；此步骤不会写入正式纪要或行动项。</p>
            </div>
            <StatusBadge v-if="draft" :status="draft.status" />
          </header>
          <p v-if="draft?.status === 'FAILED'" class="lifecycle-feedback lifecycle-feedback--error">
            上一次生成失败{{ draft.errorCode ? `（${draft.errorCode}）` : '' }}，可修改文本后显式重试。
          </p>
          <p v-if="draft?.status === 'REJECTED'" class="lifecycle-feedback">
            上一版草案已拒绝，正式业务记录未发生变化。可重新提交会议记录生成新草案。
          </p>
          <label>
            <span>会议记录</span>
            <textarea v-model="transcript" rows="10" maxlength="20000" :disabled="!lifecycle.permissions.canSubmitRecord || draftSubmitting" placeholder="粘贴会议背景、讨论过程、结论、负责人和期望截止时间。" />
          </label>
          <div class="lifecycle-editor-footer">
            <span>已输入 {{ transcript.trim().length }} 字</span>
            <button v-if="lifecycle.permissions.canSubmitRecord" class="ui-button ui-button--default" type="button" :disabled="draftSubmitting || transcript.trim().length === 0" @click="submitTranscript">
              {{ draftSubmitting ? '正在生成…' : '生成待审草案' }}
            </button>
          </div>
          <p v-if="!lifecycle.permissions.canSubmitRecord" class="lifecycle-permission-note">只有会议组织者或管理员可以提交会议记录。</p>
        </section>

        <section v-if="draft?.status === 'PENDING_REVIEW' && draftEditor" class="content-panel lifecycle-draft-review" aria-labelledby="draft-review-title">
          <header class="lifecycle-section-heading">
            <div>
              <p class="eyebrow">HITL 审核</p>
              <h2 id="draft-review-title">会后草案</h2>
              <p>编辑只保存为新草案，之后仍需再次点击接受。</p>
            </div>
            <div class="lifecycle-heading-actions">
              <RouterLink v-if="draft.agentRunId" class="text-button" :to="{ name: 'agent-run', params: { runId: draft.agentRunId } }">查看 Agent 运行</RouterLink>
              <StatusBadge status="PENDING_REVIEW" />
            </div>
          </header>

          <fieldset :disabled="!lifecycle.permissions.canReviewDraft || draftReviewing">
            <legend>纪要</legend>
            <div class="lifecycle-draft-grid">
              <label><span>会议背景</span><textarea v-model.trim="draftEditor.minutes.background" rows="4" maxlength="2000" /></label>
              <label><span>讨论摘要</span><textarea v-model.trim="draftEditor.minutes.discussionSummary" rows="4" maxlength="10000" /></label>
              <label><span>最终结论</span><textarea v-model.trim="draftEditor.minutes.conclusion" rows="4" maxlength="2000" /></label>
            </div>
          </fieldset>

          <fieldset :disabled="!lifecycle.permissions.canReviewDraft || draftReviewing">
            <legend>决策</legend>
            <p v-if="draftEditor.decisions.length === 0" class="lifecycle-inline-empty">草案中没有决策。</p>
            <article v-for="(decision, index) in draftEditor.decisions" :key="`decision-${index}`" class="lifecycle-draft-row">
              <span class="lifecycle-sequence">{{ index + 1 }}</span>
              <div><label><span>决策内容</span><input v-model.trim="decision.content" maxlength="1000" /></label><label><span>依据</span><input v-model.trim="decision.rationale" maxlength="1000" /></label></div>
              <button v-if="lifecycle.permissions.canReviewDraft" class="icon-button lifecycle-delete-button" type="button" aria-label="删除决策" @click="draftEditor.decisions.splice(index, 1)"><Trash2 :size="15" aria-hidden="true" /></button>
            </article>
            <button v-if="lifecycle.permissions.canReviewDraft" class="ui-button ui-button--outline ui-button--sm" type="button" :disabled="draftEditor.decisions.length >= 20" @click="addDraftDecision"><Plus :size="15" aria-hidden="true" />添加决策</button>
          </fieldset>

          <fieldset :disabled="!lifecycle.permissions.canReviewDraft || draftReviewing">
            <legend>行动项</legend>
            <p v-if="draftEditor.actionItems.length === 0" class="lifecycle-inline-empty">草案中没有行动项。</p>
            <article v-for="(item, index) in draftEditor.actionItems" :key="`action-${index}`" class="lifecycle-draft-row lifecycle-draft-row--action">
              <span class="lifecycle-sequence">{{ index + 1 }}</span>
              <div class="lifecycle-draft-action-grid">
                <label><span>任务</span><input v-model.trim="item.title" maxlength="200" /></label>
                <label><span>负责人</span><select v-model.number="item.assigneeEmployeeId"><option :value="0">待指定</option><option v-for="person in meetingPeople" :key="person.id" :value="person.id">{{ person.name }}</option></select></label>
                <label><span>截止时间</span><input v-model="item.dueAt" type="datetime-local" step="60" /></label>
                <label class="lifecycle-draft-description"><span>说明</span><input v-model.trim="item.description" maxlength="1000" /></label>
              </div>
              <button v-if="lifecycle.permissions.canReviewDraft" class="icon-button lifecycle-delete-button" type="button" aria-label="删除行动项" @click="draftEditor.actionItems.splice(index, 1)"><Trash2 :size="15" aria-hidden="true" /></button>
            </article>
            <button v-if="lifecycle.permissions.canReviewDraft" class="ui-button ui-button--outline ui-button--sm" type="button" :disabled="draftEditor.actionItems.length >= 50" @click="addDraftAction"><Plus :size="15" aria-hidden="true" />添加行动项</button>
          </fieldset>

          <footer v-if="lifecycle.permissions.canReviewDraft" class="lifecycle-review-actions">
            <p v-if="draftDirty">当前有未保存修改。请先保存编辑，再审核接受。</p>
            <p v-else>接受后将在一个业务事务中写入正式纪要、决策和行动项。</p>
            <div>
              <button class="ui-button ui-button--destructive" type="button" :disabled="draftReviewing" @click="reviewDraft('REJECT')">拒绝草案</button>
              <button class="ui-button ui-button--outline" type="button" :disabled="draftReviewing || !draftDirty" @click="reviewDraft('EDIT')">保存编辑（需再确认）</button>
              <button class="ui-button ui-button--default" type="button" :disabled="draftReviewing || draftDirty" @click="reviewDraft('ACCEPT')">接受并正式写入</button>
            </div>
          </footer>
          <p v-else class="lifecycle-permission-note">当前账号可查看草案，但只有会议组织者或管理员可以审核。</p>
        </section>

        <section v-if="draft?.status === 'ACCEPTED' && !hasFormalPostMeetingRecord" class="content-panel lifecycle-terminal-state">
          <ClipboardCheck :size="24" aria-hidden="true" />
          <div><h2>草案已接受</h2><p>正式记录正在同步，请刷新当前会议。</p></div>
        </section>

        <section v-if="draft?.status === 'PENDING_REVIEW' && !draftEditor" class="content-panel lifecycle-terminal-state">
          <AlertTriangle :size="24" aria-hidden="true" />
          <div><h2>草案内容暂不可用</h2><p>服务端返回了待审核状态，但没有可展示的结构化内容。请刷新；在内容恢复前不会执行审核写入。</p></div>
        </section>
      </template>

      <section v-else class="content-panel lifecycle-terminal-state">
        <FileText :size="24" aria-hidden="true" />
        <div><h2>当前状态仅支持查看</h2><p>该会议状态暂不允许编辑会前准备或生成会后记录。</p></div>
      </section>
    </template>
  </AppShell>
</template>

<script setup lang="ts">
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CalendarDays,
  CheckCircle2,
  ClipboardCheck,
  Clock3,
  FileText,
  MapPin,
  Plus,
  RefreshCw,
  Trash2,
  Users,
} from '@lucide/vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ApiError } from '../api/client'
import {
  createPostMeetingDraft,
  getMeetingLifecycle,
  listVisibleMeetings,
  reviewPostMeetingDraft,
  saveMeetingPreparation,
  updateMeetingActionItem,
} from '../api/meeting-lifecycle'
import type {
  Meeting,
  MeetingActionItem,
  MeetingActionItemStatus,
  MeetingLifecycle,
  MeetingMaterialStatus,
  PostMeetingDraftContent,
} from '../api/types'
import { authStore } from '../auth/store'
import AppShell from '../components/AppShell.vue'
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import LoadingState from '../components/LoadingState.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { createClientRequestId, formatDateTime, toShanghaiDateTimeLocal, toShanghaiOffset } from '../utils/format'
import { isTechnicalDemoMeeting } from '../utils/labels'

interface AgendaFormItem {
  clientId: string
  topic: string
  ownerEmployeeId: number
  plannedMinutes: number
}

interface MaterialFormItem {
  clientId: string
  title: string
  ownerEmployeeId: number
  required: boolean
  status: MeetingMaterialStatus
  versionLabel: string
  note: string
}

interface EditablePostMeetingContent {
  minutes: { background: string; discussionSummary: string; conclusion: string }
  decisions: Array<{ content: string; rationale: string }>
  actionItems: Array<{
    title: string
    description: string
    assigneeEmployeeId: number
    dueAt: string
  }>
}

const route = useRoute()
const router = useRouter()
const meetings = ref<Meeting[]>([])
const selectedMeetingId = ref<number | null>(null)
const meetingListLoading = ref(true)
const meetingListError = ref('')
const lifecycle = ref<MeetingLifecycle | null>(null)
const lifecycleLoading = ref(false)
const lifecycleError = ref('')
const operationError = ref('')
const operationNotice = ref('')
const agendaForms = ref<AgendaFormItem[]>([])
const materialForms = ref<MaterialFormItem[]>([])
const preparationSaving = ref(false)
const transcript = ref('')
const draftSubmitting = ref(false)
const draftReviewing = ref(false)
const draftEditor = ref<EditablePostMeetingContent | null>(null)
const draftSnapshot = ref('')
const actionStatusEdits = reactive<Record<number, MeetingActionItemStatus>>({})
const actionUpdatingId = ref<number | null>(null)
let formSequence = 0
let lifecycleRequestEpoch = 0
let draftIdempotencyKey: string | null = null
let draftIdempotencyTranscript = ''

const draft = computed(() => lifecycle.value?.postMeeting.draft ?? null)
const canEditPreparation = computed(() => lifecycle.value?.permissions.canEditPreparation === true && isFutureMeeting.value)
const isFutureMeeting = computed(() => {
  const startAt = lifecycle.value?.meeting.startAt
  return startAt !== undefined && new Date(startAt).getTime() > Date.now()
})
const meetingDurationMinutes = computed(() => {
  const meeting = lifecycle.value?.meeting
  if (!meeting) return 0
  return Math.round((new Date(meeting.endAt).getTime() - new Date(meeting.startAt).getTime()) / 60000)
})
const agendaTotalMinutes = computed(() => agendaForms.value.reduce((total, item) => total + (Number(item.plannedMinutes) || 0), 0))
const meetingPeople = computed(() => {
  const meeting = lifecycle.value?.meeting
  if (!meeting) return []
  const people = new Map<number, string>([[meeting.organizerId, meeting.organizerName]])
  meeting.participants.forEach((participant) => people.set(participant.employeeId, participant.displayName))
  return [...people].map(([id, name]) => ({ id, name }))
})
const hasFormalPostMeetingRecord = computed(() => lifecycle.value?.postMeeting.minutes !== null)
const shouldShowTranscriptForm = computed(() => {
  if (hasFormalPostMeetingRecord.value) return false
  return draft.value === null || ['FAILED', 'REJECTED'].includes(draft.value.status)
})
const draftDirty = computed(() => draftEditor.value !== null && JSON.stringify(draftEditor.value) !== draftSnapshot.value)

function nextClientId(prefix: string): string {
  formSequence += 1
  return `${prefix}-${formSequence}`
}

function parseMeetingId(value: unknown): number | null {
  if (typeof value !== 'string' || !/^\d{1,18}$/.test(value)) return null
  const parsed = Number(value)
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null
}

function meetingOptionLabel(meeting: Meeting): string {
  const status = meeting.status === 'CONFIRMED' ? '已确认' : meeting.status === 'COMPLETED' ? '已完成' : meeting.status === 'CANCELLED' ? '已取消' : '其他状态'
  return `${formatDateTime(meeting.startAt)} · ${meeting.title} · ${status}`
}

function checklistLabel(code: string): string {
  const labels: Record<string, string> = {
    AGENDA_PRESENT: '议程已配置',
    AGENDA_DURATION: '议程时长合理',
    AGENDA_OWNERS: '议题负责人有效',
    MATERIALS_READY: '必需材料已就绪',
    ROOM_ACTIVE: '会议室可用',
    PARTICIPANTS_PRESENT: '必需参会者有效',
  }
  return labels[code] ?? '其他准备项'
}

function actionStatusLabel(status: MeetingActionItemStatus): string {
  return { OPEN: '待处理', IN_PROGRESS: '进行中', DONE: '已完成' }[status]
}

function employeeName(employeeId: number | null | undefined): string {
  if (employeeId === null || employeeId === undefined) return '—'
  const meetingPerson = meetingPeople.value.find((person) => person.id === employeeId)
  if (meetingPerson !== undefined) return meetingPerson.name
  const currentUser = authStore.state.user
  return currentUser?.id === employeeId ? currentUser.displayName : '已授权审核人'
}

function sortMeetings(items: Meeting[]): Meeting[] {
  const rank = (status: string): number => status === 'CONFIRMED' ? 0 : status === 'COMPLETED' ? 1 : status === 'CANCELLED' ? 2 : 3
  return [...items].sort((left, right) => {
    const statusRank = rank(left.status) - rank(right.status)
    if (statusRank !== 0) return statusRank
    return left.status === 'CONFIRMED'
      ? left.startAt.localeCompare(right.startAt)
      : right.startAt.localeCompare(left.startAt)
  })
}

async function loadMeetingChoices(): Promise<void> {
  meetingListLoading.value = true
  meetingListError.value = ''
  try {
    const result = await listVisibleMeetings()
    meetings.value = sortMeetings(result.items.filter((meeting) => !isTechnicalDemoMeeting(meeting.title, meeting.meetingType)))
    const routeMeetingId = parseMeetingId(route.query.meetingId)
    const initialMeetingId = routeMeetingId !== null && meetings.value.some((meeting) => meeting.id === routeMeetingId)
      ? routeMeetingId
      : meetings.value[0]?.id ?? null
    selectedMeetingId.value = initialMeetingId
    if (initialMeetingId !== null) await loadLifecycle(false)
  } catch (error) {
    meetingListError.value = userMessage(error, '会议列表加载失败，请稍后重试。')
  } finally {
    meetingListLoading.value = false
  }
}

async function handleMeetingSelection(): Promise<void> {
  if (selectedMeetingId.value === null) return
  await router.replace({ name: 'meeting-lifecycle', query: { meetingId: String(selectedMeetingId.value) } })
  await loadLifecycle()
}

async function loadLifecycle(clearFeedback = true): Promise<void> {
  const meetingId = selectedMeetingId.value
  if (meetingId === null) return
  const epoch = ++lifecycleRequestEpoch
  lifecycleLoading.value = true
  lifecycleError.value = ''
  if (clearFeedback) clearOperationFeedback()
  try {
    const result = await getMeetingLifecycle(meetingId)
    if (epoch !== lifecycleRequestEpoch) return
    applyLifecycle(result)
    if (!meetings.value.some((meeting) => meeting.id === result.meeting.id)) {
      meetings.value = sortMeetings([...meetings.value, result.meeting])
    }
    if (route.query.meetingId !== String(meetingId)) {
      await router.replace({ name: 'meeting-lifecycle', query: { meetingId: String(meetingId) } })
    }
  } catch (error) {
    if (epoch === lifecycleRequestEpoch) lifecycleError.value = userMessage(error, '生命周期数据加载失败，请稍后重试。')
  } finally {
    if (epoch === lifecycleRequestEpoch) lifecycleLoading.value = false
  }
}

function applyLifecycle(result: MeetingLifecycle): void {
  if (lifecycle.value?.meeting.id !== result.meeting.id) {
    transcript.value = ''
    draftIdempotencyKey = null
    draftIdempotencyTranscript = ''
  }
  lifecycle.value = result
  agendaForms.value = result.preparation.agendaItems
    .slice()
    .sort((left, right) => left.sequenceNo - right.sequenceNo)
    .map((item) => ({ clientId: nextClientId('agenda'), topic: item.topic, ownerEmployeeId: item.ownerEmployeeId, plannedMinutes: item.plannedMinutes }))
  materialForms.value = result.preparation.materials
    .slice()
    .sort((left, right) => left.sequenceNo - right.sequenceNo)
    .map((item) => ({
      clientId: nextClientId('material'),
      title: item.title,
      ownerEmployeeId: item.ownerEmployeeId,
      required: item.required,
      status: item.status,
      versionLabel: item.versionLabel ?? '',
      note: item.note ?? '',
    }))
  const content = result.postMeeting.draft?.content
  draftEditor.value = content === null || content === undefined ? null : editableDraft(content)
  draftSnapshot.value = draftEditor.value === null ? '' : JSON.stringify(draftEditor.value)
  Object.keys(actionStatusEdits).forEach((key) => delete actionStatusEdits[Number(key)])
  result.postMeeting.actionItems.forEach((item) => { actionStatusEdits[item.id] = item.status })
}

function editableDraft(content: PostMeetingDraftContent): EditablePostMeetingContent {
  return {
    minutes: { ...content.minutes },
    decisions: content.decisions.map((item) => ({ content: item.content, rationale: item.rationale ?? '' })),
    actionItems: content.actionItems.map((item) => ({
      title: item.title,
      description: item.description ?? '',
      assigneeEmployeeId: item.assigneeEmployeeId ?? 0,
      dueAt: toShanghaiDateTimeLocal(item.dueAt),
    })),
  }
}

function addAgendaItem(): void {
  if (agendaForms.value.length >= 30) return
  agendaForms.value.push({ clientId: nextClientId('agenda'), topic: '', ownerEmployeeId: meetingPeople.value[0]?.id ?? 0, plannedMinutes: 10 })
}

function addMaterialItem(): void {
  if (materialForms.value.length >= 50) return
  materialForms.value.push({ clientId: nextClientId('material'), title: '', ownerEmployeeId: meetingPeople.value[0]?.id ?? 0, required: true, status: 'MISSING', versionLabel: '', note: '' })
}

function moveItem<T>(items: T[], index: number, direction: -1 | 1): void {
  const target = index + direction
  if (target < 0 || target >= items.length) return
  const [item] = items.splice(index, 1)
  if (item !== undefined) items.splice(target, 0, item)
}

function validatePreparation(): string | null {
  if (agendaForms.value.some((item) => item.topic.trim().length === 0 || item.ownerEmployeeId <= 0 || !Number.isInteger(item.plannedMinutes) || item.plannedMinutes < 5 || item.plannedMinutes > 240)) {
    return '请完整填写每个议题的名称、负责人，以及 5–240 分钟的整数时长。'
  }
  if (materialForms.value.some((item) => item.title.trim().length === 0 || item.ownerEmployeeId <= 0)) {
    return '请完整填写每份材料的名称和负责人。'
  }
  if (agendaTotalMinutes.value > meetingDurationMinutes.value) return '议程总时长不能超过会议时长。'
  return null
}

async function savePreparation(): Promise<void> {
  const current = lifecycle.value
  if (!current || preparationSaving.value) return
  clearOperationFeedback()
  const validation = validatePreparation()
  if (validation !== null) { operationError.value = validation; return }
  preparationSaving.value = true
  try {
    const updated = await saveMeetingPreparation(current.meeting.id, {
      expectedVersion: current.preparation.version,
      agendaItems: agendaForms.value.map((item) => ({ topic: item.topic.trim(), ownerEmployeeId: item.ownerEmployeeId, plannedMinutes: item.plannedMinutes })),
      materials: materialForms.value.map((item) => ({
        title: item.title.trim(),
        ownerEmployeeId: item.ownerEmployeeId,
        required: item.required,
        status: item.status,
        versionLabel: emptyToNull(item.versionLabel),
        note: emptyToNull(item.note),
      })),
    })
    applyLifecycle(updated)
    operationNotice.value = '会前准备已保存，动态清单已按最新事实重新计算。'
  } catch (error) {
    await handleMutationError(error, '会前准备保存失败。')
  } finally {
    preparationSaving.value = false
  }
}

async function submitTranscript(): Promise<void> {
  const current = lifecycle.value
  const normalizedTranscript = transcript.value.trim()
  if (!current || draftSubmitting.value || normalizedTranscript.length === 0) return
  clearOperationFeedback()
  if (draftIdempotencyKey === null || draftIdempotencyTranscript !== normalizedTranscript) {
    draftIdempotencyKey = createClientRequestId()
    draftIdempotencyTranscript = normalizedTranscript
  }
  draftSubmitting.value = true
  try {
    const updated = await createPostMeetingDraft(current.meeting.id, normalizedTranscript, draftIdempotencyKey)
    draftIdempotencyKey = null
    draftIdempotencyTranscript = ''
    transcript.value = ''
    applyLifecycle(updated)
    if (draft.value?.status === 'FAILED') {
      operationError.value = `草案生成失败${draft.value.errorCode ? `（${draft.value.errorCode}）` : ''}，正式记录未发生变化。`
    } else if (draft.value?.status === 'PROCESSING') {
      operationNotice.value = '草案请求已受理，Agent 仍在处理，请稍后刷新当前会议。'
    } else {
      operationNotice.value = '会后草案已生成，请核对内容后执行接受、编辑或拒绝。'
    }
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      draftIdempotencyKey = null
      draftIdempotencyTranscript = ''
    }
    await handleMutationError(error, '会后草案生成失败。')
  } finally {
    draftSubmitting.value = false
  }
}

function addDraftDecision(): void {
  if (draftEditor.value !== null && draftEditor.value.decisions.length < 20) draftEditor.value.decisions.push({ content: '', rationale: '' })
}

function addDraftAction(): void {
  if (draftEditor.value !== null && draftEditor.value.actionItems.length < 50) draftEditor.value.actionItems.push({ title: '', description: '', assigneeEmployeeId: 0, dueAt: '' })
}

function validateDraftEditor(): string | null {
  const editor = draftEditor.value
  const meeting = lifecycle.value?.meeting
  if (!editor || !meeting) return '当前草案不可编辑。'
  if (!editor.minutes.background.trim() || !editor.minutes.discussionSummary.trim() || !editor.minutes.conclusion.trim()) return '纪要的背景、讨论摘要和结论不能为空。'
  if (editor.decisions.some((item) => item.content.trim().length === 0)) return '决策内容不能为空。'
  for (const item of editor.actionItems) {
    if (!item.title.trim() || item.assigneeEmployeeId <= 0 || !item.dueAt) return '每个行动项都必须填写任务、负责人和截止时间。'
    if (new Date(toShanghaiOffset(item.dueAt)).getTime() <= new Date(meeting.endAt).getTime()) return '行动项截止时间必须晚于会议结束时间。'
  }
  return null
}

function draftMutationContent(): PostMeetingDraftContent | null {
  const editor = draftEditor.value
  if (!editor) return null
  return {
    minutes: {
      background: editor.minutes.background.trim(),
      discussionSummary: editor.minutes.discussionSummary.trim(),
      conclusion: editor.minutes.conclusion.trim(),
    },
    decisions: editor.decisions.map((item) => ({ content: item.content.trim(), rationale: item.rationale.trim() })),
    actionItems: editor.actionItems.map((item) => ({
      title: item.title.trim(),
      description: item.description.trim(),
      assigneeEmployeeId: item.assigneeEmployeeId,
      dueAt: toShanghaiOffset(item.dueAt),
    })),
  }
}

async function reviewDraft(action: 'ACCEPT' | 'EDIT' | 'REJECT'): Promise<void> {
  const current = lifecycle.value
  const currentDraft = draft.value
  if (!current || !currentDraft || draftReviewing.value) return
  clearOperationFeedback()
  if (action !== 'REJECT') {
    const validation = validateDraftEditor()
    if (validation !== null) { operationError.value = validation; return }
  }
  if (action === 'ACCEPT' && draftDirty.value) {
    operationError.value = '当前有未保存编辑。请先保存编辑并取得新版本，再接受草案。'
    return
  }
  draftReviewing.value = true
  try {
    const editedDraft = action === 'EDIT' ? draftMutationContent() : null
    const updated = await reviewPostMeetingDraft(current.meeting.id, currentDraft.id, {
      action,
      expectedVersion: currentDraft.version,
      ...(editedDraft === null ? {} : { editedDraft }),
    })
    applyLifecycle(updated)
    operationNotice.value = action === 'EDIT'
      ? '编辑已保存为新草案版本；正式记录尚未写入，请再次确认后接受。'
      : action === 'ACCEPT'
        ? '草案已接受，正式纪要、决策和行动项已写入。'
        : '草案已拒绝，正式业务记录未发生变化。'
  } catch (error) {
    await handleMutationError(error, '草案审核失败。')
  } finally {
    draftReviewing.value = false
  }
}

function canUpdateActionItem(item: MeetingActionItem): boolean {
  const user = authStore.state.user
  const meeting = lifecycle.value?.meeting
  return user?.roles.includes('ADMIN') === true || user?.id === item.assigneeEmployeeId || user?.id === meeting?.organizerId
}

function availableActionStatuses(status: MeetingActionItemStatus): MeetingActionItemStatus[] {
  if (status === 'OPEN') return ['OPEN', 'IN_PROGRESS', 'DONE']
  if (status === 'IN_PROGRESS') return ['IN_PROGRESS', 'OPEN', 'DONE']
  return ['DONE']
}

function setActionStatus(actionItemId: number, event: Event): void {
  actionStatusEdits[actionItemId] = (event.target as HTMLSelectElement).value as MeetingActionItemStatus
}

async function saveActionStatus(item: MeetingActionItem): Promise<void> {
  const current = lifecycle.value
  const status = actionStatusEdits[item.id] ?? item.status
  if (!current || actionUpdatingId.value !== null || status === item.status) return
  clearOperationFeedback()
  actionUpdatingId.value = item.id
  try {
    const updated = await updateMeetingActionItem(current.meeting.id, item.id, { status, expectedVersion: item.version })
    const index = current.postMeeting.actionItems.findIndex((candidate) => candidate.id === item.id)
    if (index >= 0) current.postMeeting.actionItems[index] = updated
    actionStatusEdits[item.id] = updated.status
    operationNotice.value = `行动项“${updated.title}”已更新为${actionStatusLabel(updated.status)}。`
  } catch (error) {
    await handleMutationError(error, '行动项状态更新失败。')
  } finally {
    actionUpdatingId.value = null
  }
}

async function handleMutationError(error: unknown, fallback: string): Promise<void> {
  if (error instanceof ApiError && error.status === 409) {
    await loadLifecycle(false)
    operationError.value = '内容已被其他操作更新，页面已刷新到最新版本，请核对后重试。'
    return
  }
  operationError.value = userMessage(error, fallback)
}

function userMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

function emptyToNull(value: string): string | null {
  const normalized = value.trim()
  return normalized.length === 0 ? null : normalized
}

function clearOperationFeedback(): void {
  operationError.value = ''
  operationNotice.value = ''
}

watch(transcript, (value) => {
  if (value.trim() !== draftIdempotencyTranscript) draftIdempotencyKey = null
})

watch(() => route.query.meetingId, (value, previous) => {
  if (value === previous || meetingListLoading.value) return
  const meetingId = parseMeetingId(value)
  if (meetingId !== null && meetingId !== selectedMeetingId.value) {
    selectedMeetingId.value = meetingId
    void loadLifecycle()
  }
})

onMounted(() => { void loadMeetingChoices() })
</script>
