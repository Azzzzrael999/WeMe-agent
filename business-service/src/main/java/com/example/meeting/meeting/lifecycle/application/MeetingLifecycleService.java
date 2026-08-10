package com.example.meeting.meeting.lifecycle.application;

import com.example.meeting.booking.application.IdempotencyKeyCoordinator;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.meeting.api.MeetingView;
import com.example.meeting.meeting.application.MeetingQueryService;
import com.example.meeting.meeting.lifecycle.api.CreatePostMeetingDraftRequest;
import com.example.meeting.meeting.lifecycle.api.MeetingLifecycleView;
import com.example.meeting.meeting.lifecycle.api.ReviewPostMeetingDraftRequest;
import com.example.meeting.meeting.lifecycle.api.SavePreparationRequest;
import com.example.meeting.meeting.lifecycle.api.UpdateActionItemRequest;
import com.example.meeting.meeting.lifecycle.client.PostMeetingAgentClient;
import com.example.meeting.meeting.lifecycle.client.PostMeetingAgentClient.AgentDraftResponse;
import com.example.meeting.meeting.lifecycle.client.PostMeetingAgentClient.AgentOutputException;
import java.util.UUID;
import org.springframework.stereotype.Service;

@Service
public class MeetingLifecycleService {

  private static final String DRAFT_OPERATION = "CREATE_POST_MEETING_DRAFT";

  private final MeetingLifecycleQueryService queryService;
  private final MeetingQueryService meetingQueryService;
  private final MeetingPreparationWriter preparationWriter;
  private final PostMeetingDraftWriter draftWriter;
  private final PostMeetingAgentClient agentClient;
  private final IdempotencyKeyCoordinator idempotencyCoordinator;

  public MeetingLifecycleService(
      MeetingLifecycleQueryService queryService,
      MeetingQueryService meetingQueryService,
      MeetingPreparationWriter preparationWriter,
      PostMeetingDraftWriter draftWriter,
      PostMeetingAgentClient agentClient,
      IdempotencyKeyCoordinator idempotencyCoordinator) {
    this.queryService = queryService;
    this.meetingQueryService = meetingQueryService;
    this.preparationWriter = preparationWriter;
    this.draftWriter = draftWriter;
    this.agentClient = agentClient;
    this.idempotencyCoordinator = idempotencyCoordinator;
  }

  public MeetingLifecycleView get(long meetingId, AuthenticatedUser actor) {
    return queryService.get(meetingId, actor);
  }

  public MeetingLifecycleView savePreparation(
      long meetingId, SavePreparationRequest request, AuthenticatedUser actor) {
    preparationWriter.save(meetingId, request, actor);
    return queryService.get(meetingId, actor);
  }

  public MeetingLifecycleView createDraft(
      long meetingId,
      CreatePostMeetingDraftRequest request,
      String rawIdempotencyKey,
      AuthenticatedUser actor,
      String traceId) {
    String requestId = idempotencyCoordinator.normalize(rawIdempotencyKey);
    String transcript = request.transcript().trim();
    return idempotencyCoordinator.execute(
        actor.userId(),
        DRAFT_OPERATION,
        requestId,
        () -> createDraftLocked(meetingId, requestId, transcript, actor, traceId));
  }

  public MeetingLifecycleView reviewDraft(
      long meetingId,
      long draftId,
      ReviewPostMeetingDraftRequest request,
      AuthenticatedUser actor) {
    MeetingView meeting = meetingQueryService.getVisible(meetingId, actor);
    draftWriter.review(meetingId, draftId, request, meeting, actor);
    return queryService.get(meetingId, actor);
  }

  public MeetingLifecycleView.ActionItemView updateActionItem(
      long meetingId, long actionItemId, UpdateActionItemRequest request, AuthenticatedUser actor) {
    draftWriter.updateActionItem(meetingId, actionItemId, request, actor);
    return queryService.getActionItem(meetingId, actionItemId);
  }

  private MeetingLifecycleView createDraftLocked(
      long meetingId,
      String requestId,
      String transcript,
      AuthenticatedUser actor,
      String traceId) {
    String runId = "run_" + UUID.randomUUID().toString().replace("-", "");
    PostMeetingDraftWriter.DraftAttempt attempt =
        draftWriter.begin(meetingId, requestId, runId, transcript, actor);
    if (!attempt.callAgent()) {
      return queryService.get(meetingId, actor);
    }
    MeetingView meeting = meetingQueryService.getVisible(meetingId, actor);
    try {
      AgentDraftResponse response =
          agentClient.generate(meeting, transcript, actor, traceId, attempt.runId());
      draftWriter.complete(
          meetingId, attempt.draftId(), attempt.version(), response.draft(), meeting);
    } catch (AgentOutputException exception) {
      draftWriter.fail(attempt.draftId(), attempt.version(), "AGENT_OUTPUT_INVALID");
    } catch (BusinessException exception) {
      if (exception.errorCode() == ErrorCode.AGENT_UNAVAILABLE) {
        draftWriter.fail(attempt.draftId(), attempt.version(), ErrorCode.AGENT_UNAVAILABLE.name());
      } else if (exception.errorCode() == ErrorCode.VALIDATION_ERROR) {
        draftWriter.fail(attempt.draftId(), attempt.version(), "AGENT_OUTPUT_INVALID");
      } else if (exception.errorCode() == ErrorCode.POST_MEETING_DRAFT_STATE_CONFLICT) {
        draftWriter.fail(attempt.draftId(), attempt.version(), "MEETING_STATE_CHANGED");
      } else {
        throw exception;
      }
    }
    return queryService.get(meetingId, actor);
  }
}
