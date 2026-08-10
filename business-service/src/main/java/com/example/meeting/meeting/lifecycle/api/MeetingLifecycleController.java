package com.example.meeting.meeting.lifecycle.api;

import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.trace.TraceIds;
import com.example.meeting.common.web.ApiResponseFactory;
import com.example.meeting.common.web.ApiSuccess;
import com.example.meeting.meeting.lifecycle.application.MeetingLifecycleService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/meetings/{meetingId}")
public class MeetingLifecycleController {

  private final MeetingLifecycleService lifecycleService;
  private final ApiResponseFactory responseFactory;

  public MeetingLifecycleController(
      MeetingLifecycleService lifecycleService, ApiResponseFactory responseFactory) {
    this.lifecycleService = lifecycleService;
    this.responseFactory = responseFactory;
  }

  @GetMapping("/lifecycle")
  public ApiSuccess<MeetingLifecycleView> getLifecycle(
      @PathVariable long meetingId,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(lifecycleService.get(meetingId, actor), request);
  }

  @PutMapping("/preparation")
  public ApiSuccess<MeetingLifecycleView> savePreparation(
      @PathVariable long meetingId,
      @Valid @RequestBody SavePreparationRequest body,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(
        lifecycleService.savePreparation(meetingId, body, actor), request);
  }

  @PostMapping("/post-meeting-drafts")
  public ApiSuccess<MeetingLifecycleView> createDraft(
      @PathVariable long meetingId,
      @Valid @RequestBody CreatePostMeetingDraftRequest body,
      @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(
        lifecycleService.createDraft(
            meetingId, body, idempotencyKey, actor, TraceIds.from(request)),
        request);
  }

  @PostMapping("/post-meeting-drafts/{draftId}/review")
  public ApiSuccess<MeetingLifecycleView> reviewDraft(
      @PathVariable long meetingId,
      @PathVariable long draftId,
      @Valid @RequestBody ReviewPostMeetingDraftRequest body,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(
        lifecycleService.reviewDraft(meetingId, draftId, body, actor), request);
  }

  @PatchMapping("/action-items/{actionItemId}")
  public ApiSuccess<MeetingLifecycleView.ActionItemView> updateActionItem(
      @PathVariable long meetingId,
      @PathVariable long actionItemId,
      @Valid @RequestBody UpdateActionItemRequest body,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(
        lifecycleService.updateActionItem(meetingId, actionItemId, body, actor), request);
  }
}
