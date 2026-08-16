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
import { RouterLink } from 'vue-router'

import AppShell from '../components/AppShell.vue'
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import LoadingState from '../components/LoadingState.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useMeetingLifecycle } from '../composables/useMeetingLifecycle'

const {
  actionStatusEdits,
  actionStatusLabel,
  actionUpdatingId,
  addAgendaItem,
  addDraftAction,
  addDraftDecision,
  addMaterialItem,
  agendaForms,
  agendaTotalMinutes,
  availableActionStatuses,
  canEditPreparation,
  canUpdateActionItem,
  checklistLabel,
  draft,
  draftDirty,
  draftEditor,
  draftReviewing,
  draftSubmitting,
  employeeName,
  formatDateTime,
  handleMeetingSelection,
  hasFormalPostMeetingRecord,
  isFutureMeeting,
  lifecycle,
  lifecycleError,
  lifecycleLoading,
  loadLifecycle,
  loadMeetingChoices,
  materialForms,
  meetingDurationMinutes,
  meetingListError,
  meetingListLoading,
  meetingOptionLabel,
  meetingPeople,
  meetings,
  moveItem,
  operationError,
  operationNotice,
  preparationSaving,
  reviewDraft,
  saveActionStatus,
  savePreparation,
  selectedMeetingId,
  setActionStatus,
  shouldShowTranscriptForm,
  submitTranscript,
  transcript,
} = useMeetingLifecycle()
</script>
