package com.example.meeting.meeting.lifecycle.application;

import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.web.ApiErrorDetail;
import com.example.meeting.meeting.api.MeetingView;
import com.example.meeting.meeting.domain.MeetingRecord;
import com.example.meeting.meeting.infrastructure.MeetingMapper;
import com.example.meeting.meeting.infrastructure.MeetingParticipantMapper;
import com.example.meeting.meeting.lifecycle.api.PostMeetingDraftContent;
import com.example.meeting.meeting.lifecycle.api.ReviewPostMeetingDraftRequest;
import com.example.meeting.meeting.lifecycle.api.UpdateActionItemRequest;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleMapper;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleMapper.DraftInsert;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.ActionItemRow;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.DecisionRow;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.DraftRow;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Clock;
import java.time.LocalDateTime;
import java.util.List;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

@Component
public class PostMeetingDraftWriter {

  private final MeetingMapper meetingMapper;
  private final MeetingParticipantMapper participantMapper;
  private final MeetingLifecycleMapper lifecycleMapper;
  private final PostMeetingDraftValidator validator;
  private final ObjectMapper objectMapper;
  private final Clock clock;

  public PostMeetingDraftWriter(
      MeetingMapper meetingMapper,
      MeetingParticipantMapper participantMapper,
      MeetingLifecycleMapper lifecycleMapper,
      PostMeetingDraftValidator validator,
      ObjectMapper objectMapper,
      Clock clock) {
    this.meetingMapper = meetingMapper;
    this.participantMapper = participantMapper;
    this.lifecycleMapper = lifecycleMapper;
    this.validator = validator;
    this.objectMapper = objectMapper;
    this.clock = clock;
  }

  @Transactional
  public DraftAttempt begin(
      long meetingId, String requestId, String runId, String transcript, AuthenticatedUser actor) {
    MeetingRecord meeting = lockCompletedManageableMeeting(meetingId, actor);
    DraftRow existing = lifecycleMapper.findDraftForUpdate(meetingId).orElse(null);
    if (existing != null && existing.requestId().equals(requestId)) {
      if (!existing.transcript().equals(transcript)) {
        throw new BusinessException(ErrorCode.IDEMPOTENCY_KEY_REUSED);
      }
      return new DraftAttempt(existing.id(), existing.version(), existing.agentRunId(), false);
    }
    LocalDateTime now = LocalDateTime.now(clock);
    if (existing == null) {
      DraftInsert insert =
          new DraftInsert(meetingId, requestId, runId, transcript, actor.userId(), now);
      try {
        lifecycleMapper.insertDraft(insert);
      } catch (DataIntegrityViolationException exception) {
        throw new BusinessException(ErrorCode.POST_MEETING_DRAFT_STATE_CONFLICT);
      }
      return new DraftAttempt(insert.getId(), 0, runId, true);
    }
    if (!"FAILED".equals(existing.status()) && !"REJECTED".equals(existing.status())) {
      throw new BusinessException(ErrorCode.POST_MEETING_DRAFT_STATE_CONFLICT);
    }
    if (lifecycleMapper.restartDraft(
            existing.id(), requestId, runId, transcript, actor.userId(), existing.version(), now)
        != 1) {
      throw new BusinessException(ErrorCode.POST_MEETING_DRAFT_STATE_CONFLICT);
    }
    // Keep the locked meeting read meaningful and explicit: draft creation never changes it.
    if (!"COMPLETED".equals(meeting.getStatus())) {
      throw new BusinessException(ErrorCode.POST_MEETING_DRAFT_STATE_CONFLICT);
    }
    return new DraftAttempt(existing.id(), existing.version() + 1, runId, true);
  }

  @Transactional
  public void complete(
      long meetingId,
      long draftId,
      int expectedVersion,
      PostMeetingDraftContent content,
      MeetingView meeting) {
    MeetingRecord current =
        meetingMapper
            .findByIdForUpdate(meetingId)
            .orElseThrow(() -> new BusinessException(ErrorCode.MEETING_NOT_FOUND));
    if (!"COMPLETED".equals(current.getStatus())) {
      throw new BusinessException(ErrorCode.POST_MEETING_DRAFT_STATE_CONFLICT);
    }
    PostMeetingDraftContent normalized = validator.validateAndNormalize(content, meeting);
    if (lifecycleMapper.completeDraft(
            draftId, expectedVersion, serialize(normalized), LocalDateTime.now(clock))
        != 1) {
      throw new BusinessException(ErrorCode.POST_MEETING_DRAFT_STATE_CONFLICT);
    }
  }

  @Transactional
  public void fail(long draftId, int expectedVersion, String errorCode) {
    lifecycleMapper.failDraft(draftId, expectedVersion, errorCode, LocalDateTime.now(clock));
  }

  @Transactional
  public void review(
      long meetingId,
      long draftId,
      ReviewPostMeetingDraftRequest request,
      MeetingView meetingView,
      AuthenticatedUser actor) {
    lockCompletedManageableMeeting(meetingId, actor);
    DraftRow draft =
        lifecycleMapper
            .findDraftForUpdate(meetingId)
            .filter(row -> row.id() == draftId)
            .orElseThrow(() -> new BusinessException(ErrorCode.POST_MEETING_DRAFT_STATE_CONFLICT));
    if (!"PENDING_REVIEW".equals(draft.status()) || draft.version() != request.expectedVersion()) {
      throw new BusinessException(ErrorCode.POST_MEETING_DRAFT_STATE_CONFLICT);
    }
    LocalDateTime now = LocalDateTime.now(clock);
    switch (request.action()) {
      case "EDIT" -> {
        if (request.editedDraft() == null) {
          throw validation("editedDraft", "REQUIRED", "EDIT 必须提供 editedDraft");
        }
        PostMeetingDraftContent normalized =
            validator.validateAndNormalize(request.editedDraft(), meetingView);
        if (lifecycleMapper.editDraft(draftId, draft.version(), serialize(normalized), now) != 1) {
          throw new BusinessException(ErrorCode.POST_MEETING_DRAFT_STATE_CONFLICT);
        }
      }
      case "REJECT" -> {
        requireNoEditedDraft(request);
        if (lifecycleMapper.finishReview(draftId, draft.version(), "REJECTED", actor.userId(), now)
            != 1) {
          throw new BusinessException(ErrorCode.POST_MEETING_DRAFT_STATE_CONFLICT);
        }
      }
      case "ACCEPT" -> {
        requireNoEditedDraft(request);
        PostMeetingDraftContent content =
            validator.validateAndNormalize(deserialize(draft.payloadJson()), meetingView);
        if (lifecycleMapper.findMinutes(meetingId).isPresent()) {
          throw new BusinessException(ErrorCode.POST_MEETING_DRAFT_STATE_CONFLICT);
        }
        lifecycleMapper.insertMinutes(
            meetingId,
            content.minutes().background(),
            content.minutes().discussionSummary(),
            content.minutes().conclusion(),
            actor.userId(),
            now);
        if (!content.decisions().isEmpty()) {
          List<DecisionRow> decisions =
              java.util.stream.IntStream.range(0, content.decisions().size())
                  .mapToObj(
                      index ->
                          new DecisionRow(
                              null,
                              meetingId,
                              index + 1,
                              content.decisions().get(index).content(),
                              content.decisions().get(index).rationale()))
                  .toList();
          lifecycleMapper.insertDecisions(meetingId, decisions);
        }
        if (!content.actionItems().isEmpty()) {
          List<ActionItemRow> actions =
              java.util.stream.IntStream.range(0, content.actionItems().size())
                  .mapToObj(
                      index -> {
                        PostMeetingDraftContent.ActionItemContent action =
                            content.actionItems().get(index);
                        return new ActionItemRow(
                            null,
                            meetingId,
                            index + 1,
                            action.title(),
                            action.description(),
                            action.assigneeEmployeeId(),
                            null,
                            action.dueAt().toLocalDateTime(),
                            "OPEN",
                            0,
                            null,
                            now,
                            now);
                      })
                  .toList();
          lifecycleMapper.insertActionItems(meetingId, actions, now);
        }
        if (lifecycleMapper.finishReview(draftId, draft.version(), "ACCEPTED", actor.userId(), now)
            != 1) {
          throw new BusinessException(ErrorCode.POST_MEETING_DRAFT_STATE_CONFLICT);
        }
      }
      default -> throw validation("action", "INVALID_ACTION", "不支持的审核动作");
    }
  }

  @Transactional
  public void updateActionItem(
      long meetingId, long actionItemId, UpdateActionItemRequest request, AuthenticatedUser actor) {
    MeetingRecord meeting = meetingMapper.selectById(meetingId);
    if (meeting == null || !isVisible(meeting, actor)) {
      throw new BusinessException(ErrorCode.ACTION_ITEM_NOT_FOUND);
    }
    ActionItemRow action =
        lifecycleMapper
            .findActionItemForUpdate(meetingId, actionItemId)
            .orElseThrow(() -> new BusinessException(ErrorCode.ACTION_ITEM_NOT_FOUND));
    if (!actor.roles().contains("ADMIN")
        && meeting.getOrganizerId() != actor.userId()
        && action.assigneeEmployeeId() != actor.userId()) {
      throw new BusinessException(ErrorCode.ACTION_ITEM_STATE_CONFLICT);
    }
    if (action.version() != request.expectedVersion()
        || !allowedTransition(action.status(), request.status())) {
      throw new BusinessException(ErrorCode.ACTION_ITEM_STATE_CONFLICT);
    }
    LocalDateTime now = LocalDateTime.now(clock);
    if (lifecycleMapper.updateActionStatus(
            meetingId,
            actionItemId,
            request.status(),
            request.expectedVersion(),
            "DONE".equals(request.status()) ? now : null,
            now)
        != 1) {
      throw new BusinessException(ErrorCode.ACTION_ITEM_STATE_CONFLICT);
    }
  }

  private MeetingRecord lockCompletedManageableMeeting(long meetingId, AuthenticatedUser actor) {
    MeetingRecord meeting =
        meetingMapper
            .findByIdForUpdate(meetingId)
            .orElseThrow(() -> new BusinessException(ErrorCode.MEETING_NOT_FOUND));
    if (!actor.roles().contains("ADMIN") && meeting.getOrganizerId() != actor.userId()) {
      if (participantMapper.countParticipant(meetingId, actor.userId()) > 0) {
        throw new BusinessException(ErrorCode.FORBIDDEN);
      }
      throw new BusinessException(ErrorCode.MEETING_NOT_FOUND);
    }
    if (!"COMPLETED".equals(meeting.getStatus())) {
      throw new BusinessException(ErrorCode.POST_MEETING_DRAFT_STATE_CONFLICT);
    }
    return meeting;
  }

  private boolean isVisible(MeetingRecord meeting, AuthenticatedUser actor) {
    return actor.roles().contains("ADMIN")
        || meeting.getOrganizerId() == actor.userId()
        || participantMapper.countParticipant(meeting.getId(), actor.userId()) > 0;
  }

  private boolean allowedTransition(String current, String target) {
    if (current.equals(target) || "DONE".equals(current)) {
      return false;
    }
    return ("OPEN".equals(current) && ("IN_PROGRESS".equals(target) || "DONE".equals(target)))
        || ("IN_PROGRESS".equals(current) && ("OPEN".equals(target) || "DONE".equals(target)));
  }

  private void requireNoEditedDraft(ReviewPostMeetingDraftRequest request) {
    if (request.editedDraft() != null) {
      throw validation("editedDraft", "NOT_ALLOWED", request.action() + " 不接受 editedDraft");
    }
  }

  private String serialize(PostMeetingDraftContent content) {
    try {
      return objectMapper.writeValueAsString(content);
    } catch (JsonProcessingException exception) {
      throw new BusinessException(ErrorCode.INTERNAL_ERROR);
    }
  }

  private PostMeetingDraftContent deserialize(String payload) {
    if (payload == null) {
      throw new BusinessException(ErrorCode.POST_MEETING_DRAFT_STATE_CONFLICT);
    }
    try {
      return objectMapper.readValue(payload, PostMeetingDraftContent.class);
    } catch (JsonProcessingException exception) {
      throw new BusinessException(ErrorCode.INTERNAL_ERROR, "会后草案数据无法读取");
    }
  }

  private BusinessException validation(String field, String reason, String message) {
    return new BusinessException(
        ErrorCode.VALIDATION_ERROR, message, List.of(new ApiErrorDetail(field, reason)));
  }

  public record DraftAttempt(long draftId, int version, String runId, boolean callAgent) {}
}
