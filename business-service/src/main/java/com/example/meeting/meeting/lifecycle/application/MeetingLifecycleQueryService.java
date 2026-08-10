package com.example.meeting.meeting.lifecycle.application;

import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.meeting.api.MeetingView;
import com.example.meeting.meeting.application.MeetingQueryService;
import com.example.meeting.meeting.lifecycle.api.MeetingLifecycleView;
import com.example.meeting.meeting.lifecycle.api.MeetingLifecycleView.ActionItemView;
import com.example.meeting.meeting.lifecycle.api.MeetingLifecycleView.AgendaItemView;
import com.example.meeting.meeting.lifecycle.api.MeetingLifecycleView.DecisionView;
import com.example.meeting.meeting.lifecycle.api.MeetingLifecycleView.DraftView;
import com.example.meeting.meeting.lifecycle.api.MeetingLifecycleView.MaterialView;
import com.example.meeting.meeting.lifecycle.api.MeetingLifecycleView.MinutesView;
import com.example.meeting.meeting.lifecycle.api.MeetingLifecycleView.PermissionsView;
import com.example.meeting.meeting.lifecycle.api.MeetingLifecycleView.PostMeetingView;
import com.example.meeting.meeting.lifecycle.api.MeetingLifecycleView.PreparationView;
import com.example.meeting.meeting.lifecycle.api.PostMeetingDraftContent;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleMapper;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.ActionItemRow;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.AgendaRow;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.DecisionRow;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.DraftRow;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.MaterialRow;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.MinutesRow;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Clock;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.util.List;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class MeetingLifecycleQueryService {

  private final MeetingQueryService meetingQueryService;
  private final MeetingLifecycleMapper mapper;
  private final PreparationChecklistEvaluator checklistEvaluator;
  private final ObjectMapper objectMapper;
  private final Clock clock;

  public MeetingLifecycleQueryService(
      MeetingQueryService meetingQueryService,
      MeetingLifecycleMapper mapper,
      PreparationChecklistEvaluator checklistEvaluator,
      ObjectMapper objectMapper,
      Clock clock) {
    this.meetingQueryService = meetingQueryService;
    this.mapper = mapper;
    this.checklistEvaluator = checklistEvaluator;
    this.objectMapper = objectMapper;
    this.clock = clock;
  }

  @Transactional(readOnly = true)
  public MeetingLifecycleView get(long meetingId, AuthenticatedUser actor) {
    MeetingView meeting = meetingQueryService.getVisible(meetingId, actor);
    List<AgendaRow> agenda = mapper.findAgenda(meetingId);
    List<MaterialRow> materials = mapper.findMaterials(meetingId);
    DraftRow draft = mapper.findDraft(meetingId).orElse(null);
    boolean manager = isManager(meeting, actor);
    OffsetDateTime now = LocalDateTime.now(clock).atZone(clock.getZone()).toOffsetDateTime();
    PermissionsView permissions =
        new PermissionsView(
            manager && "CONFIRMED".equals(meeting.status()) && meeting.startAt().isAfter(now),
            manager
                && "COMPLETED".equals(meeting.status())
                && (draft == null
                    || "FAILED".equals(draft.status())
                    || "REJECTED".equals(draft.status())),
            manager && draft != null && "PENDING_REVIEW".equals(draft.status()));
    return new MeetingLifecycleView(
        meeting,
        permissions,
        new PreparationView(
            mapper.findPreparationVersion(meetingId).orElse(0),
            agenda.stream().map(this::agendaView).toList(),
            materials.stream().map(this::materialView).toList(),
            checklistEvaluator.evaluate(meeting, agenda, materials)),
        new PostMeetingView(
            draft == null ? null : draftView(draft),
            mapper.findMinutes(meetingId).map(this::minutesView).orElse(null),
            mapper.findDecisions(meetingId).stream().map(this::decisionView).toList(),
            mapper.findActionItems(meetingId).stream().map(this::actionView).toList()));
  }

  @Transactional(readOnly = true)
  public ActionItemView getActionItem(long meetingId, long actionItemId) {
    return mapper
        .findActionItem(meetingId, actionItemId)
        .map(this::actionView)
        .orElseThrow(() -> new BusinessException(ErrorCode.ACTION_ITEM_NOT_FOUND));
  }

  public ActionItemView actionView(ActionItemRow row) {
    return new ActionItemView(
        row.id(),
        row.sequenceNo(),
        row.title(),
        row.description(),
        row.assigneeEmployeeId(),
        row.assigneeName(),
        offset(row.dueAt()),
        row.status(),
        row.version(),
        offset(row.completedAt()));
  }

  private boolean isManager(MeetingView meeting, AuthenticatedUser actor) {
    return actor.roles().contains("ADMIN") || meeting.organizerId() == actor.userId();
  }

  private AgendaItemView agendaView(AgendaRow row) {
    return new AgendaItemView(
        row.id(),
        row.sequenceNo(),
        row.topic(),
        row.ownerEmployeeId(),
        row.ownerName(),
        row.plannedMinutes());
  }

  private MaterialView materialView(MaterialRow row) {
    return new MaterialView(
        row.id(),
        row.sequenceNo(),
        row.title(),
        row.ownerEmployeeId(),
        row.ownerName(),
        row.required(),
        row.status(),
        row.versionLabel(),
        row.note());
  }

  private DraftView draftView(DraftRow row) {
    PostMeetingDraftContent content = null;
    if (row.payloadJson() != null) {
      try {
        content = objectMapper.readValue(row.payloadJson(), PostMeetingDraftContent.class);
      } catch (JsonProcessingException exception) {
        throw new BusinessException(ErrorCode.INTERNAL_ERROR, "会后草案数据无法读取");
      }
    }
    return new DraftView(
        row.id(), row.status(), row.version(), row.agentRunId(), row.errorCode(), content);
  }

  private MinutesView minutesView(MinutesRow row) {
    return new MinutesView(
        row.background(),
        row.discussionSummary(),
        row.conclusion(),
        row.confirmedBy(),
        offset(row.confirmedAt()));
  }

  private DecisionView decisionView(DecisionRow row) {
    return new DecisionView(row.id(), row.sequenceNo(), row.content(), row.rationale());
  }

  private OffsetDateTime offset(LocalDateTime value) {
    return value == null ? null : value.atZone(clock.getZone()).toOffsetDateTime();
  }
}
