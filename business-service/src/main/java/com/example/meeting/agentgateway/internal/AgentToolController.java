package com.example.meeting.agentgateway.internal;

import com.example.meeting.agentgateway.application.AgentToolApplicationService;
import com.example.meeting.agentgateway.internal.AgentToolDtos.CancellationPreviewRequest;
import com.example.meeting.agentgateway.internal.AgentToolDtos.ConfirmBookingResponse;
import com.example.meeting.agentgateway.internal.AgentToolDtos.CreateCancellationPreviewResponse;
import com.example.meeting.agentgateway.internal.AgentToolDtos.CreateDraftResponse;
import com.example.meeting.agentgateway.internal.AgentToolDtos.CreateRescheduleDraftResponse;
import com.example.meeting.agentgateway.internal.AgentToolDtos.FreeBusyRequest;
import com.example.meeting.agentgateway.internal.AgentToolDtos.FreeBusyResponse;
import com.example.meeting.agentgateway.internal.AgentToolDtos.RecentMeetingRequest;
import com.example.meeting.agentgateway.internal.AgentToolDtos.RecentMeetingResponse;
import com.example.meeting.agentgateway.internal.AgentToolDtos.RescheduleDraftRequest;
import com.example.meeting.agentgateway.internal.AgentToolDtos.ResolveEmployeesRequest;
import com.example.meeting.agentgateway.internal.AgentToolDtos.ResolveEmployeesResponse;
import com.example.meeting.agentgateway.internal.AgentToolDtos.ResolveParticipantScopeRequest;
import com.example.meeting.agentgateway.internal.AgentToolDtos.ResolveParticipantScopeResponse;
import com.example.meeting.agentgateway.internal.AgentToolDtos.SearchRoomsRequest;
import com.example.meeting.agentgateway.internal.AgentToolDtos.SearchRoomsResponse;
import com.example.meeting.common.security.AgentToolContext;
import com.example.meeting.common.web.ApiResponseFactory;
import com.example.meeting.common.web.ApiSuccess;
import com.example.meeting.meeting.api.CreateMeetingRequest;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/internal/v1/tools")
public class AgentToolController {

  private final AgentToolApplicationService application;
  private final ApiResponseFactory responseFactory;

  public AgentToolController(
      AgentToolApplicationService application, ApiResponseFactory responseFactory) {
    this.application = application;
    this.responseFactory = responseFactory;
  }

  @PostMapping("/resolve-employees")
  public ApiSuccess<ResolveEmployeesResponse> resolveEmployees(
      @Valid @RequestBody ResolveEmployeesRequest body,
      @RequestAttribute(AgentToolContext.REQUEST_ATTRIBUTE) AgentToolContext context,
      HttpServletRequest request) {
    ResolveEmployeesResponse result = application.resolveEmployees(body, context);
    return responseFactory.success(result, request);
  }

  @PostMapping("/resolve-participant-scope")
  public ApiSuccess<ResolveParticipantScopeResponse> resolveParticipantScope(
      @Valid @RequestBody ResolveParticipantScopeRequest body,
      @RequestAttribute(AgentToolContext.REQUEST_ATTRIBUTE) AgentToolContext context,
      HttpServletRequest request) {
    ResolveParticipantScopeResponse result = application.resolveParticipantScope(body, context);
    return responseFactory.success(result, request);
  }

  @PostMapping("/get-employee-free-busy")
  public ApiSuccess<FreeBusyResponse> freeBusy(
      @Valid @RequestBody FreeBusyRequest body,
      @RequestAttribute(AgentToolContext.REQUEST_ATTRIBUTE) AgentToolContext context,
      HttpServletRequest request) {
    FreeBusyResponse result = application.freeBusy(body, context);
    return responseFactory.success(result, request);
  }

  @PostMapping("/search-available-rooms")
  public ApiSuccess<SearchRoomsResponse> searchRooms(
      @Valid @RequestBody SearchRoomsRequest body,
      @RequestAttribute(AgentToolContext.REQUEST_ATTRIBUTE) AgentToolContext context,
      HttpServletRequest request) {
    SearchRoomsResponse result = application.searchRooms(body, context);
    return responseFactory.success(result, request);
  }

  @PostMapping("/get-recent-meeting")
  public ApiSuccess<RecentMeetingResponse> recentMeeting(
      @Valid @RequestBody RecentMeetingRequest body,
      @RequestAttribute(AgentToolContext.REQUEST_ATTRIBUTE) AgentToolContext context,
      HttpServletRequest request) {
    RecentMeetingResponse result = application.recentMeeting(body, context);
    return responseFactory.success(result, request);
  }

  @PostMapping("/booking-drafts")
  public ApiSuccess<CreateDraftResponse> createDraft(
      @Valid @RequestBody CreateMeetingRequest body,
      @RequestAttribute(AgentToolContext.REQUEST_ATTRIBUTE) AgentToolContext context,
      HttpServletRequest request) {
    CreateDraftResponse result = application.createDraft(body, context);
    return responseFactory.success(result, request);
  }

  @PostMapping("/booking-drafts/{confirmationToken}/confirm")
  public ResponseEntity<ApiSuccess<ConfirmBookingResponse>> confirmDraft(
      @PathVariable String confirmationToken,
      @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey,
      @RequestAttribute(AgentToolContext.REQUEST_ATTRIBUTE) AgentToolContext context,
      HttpServletRequest request) {
    ConfirmBookingResponse result =
        application.confirmDraft(confirmationToken, idempotencyKey, context);
    HttpStatus status = "PENDING".equals(result.status()) ? HttpStatus.ACCEPTED : HttpStatus.OK;
    return ResponseEntity.status(status).body(responseFactory.success(result, request));
  }

  @PostMapping("/reschedule-drafts")
  public ApiSuccess<CreateRescheduleDraftResponse> createRescheduleDraft(
      @Valid @RequestBody RescheduleDraftRequest body,
      @RequestAttribute(AgentToolContext.REQUEST_ATTRIBUTE) AgentToolContext context,
      HttpServletRequest request) {
    CreateRescheduleDraftResponse result = application.createRescheduleDraft(body, context);
    return responseFactory.success(result, request);
  }

  @PostMapping("/reschedule-drafts/{confirmationToken}/confirm")
  public ApiSuccess<ConfirmBookingResponse> confirmRescheduleDraft(
      @PathVariable String confirmationToken,
      @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey,
      @RequestAttribute(AgentToolContext.REQUEST_ATTRIBUTE) AgentToolContext context,
      HttpServletRequest request) {
    ConfirmBookingResponse result =
        application.confirmRescheduleDraft(confirmationToken, idempotencyKey, context);
    return responseFactory.success(result, request);
  }

  @PostMapping("/cancellation-previews")
  public ApiSuccess<CreateCancellationPreviewResponse> createCancellationPreview(
      @Valid @RequestBody CancellationPreviewRequest body,
      @RequestAttribute(AgentToolContext.REQUEST_ATTRIBUTE) AgentToolContext context,
      HttpServletRequest request) {
    CreateCancellationPreviewResponse result = application.createCancellationPreview(body, context);
    return responseFactory.success(result, request);
  }

  @PostMapping("/cancellation-previews/{confirmationToken}/confirm")
  public ApiSuccess<ConfirmBookingResponse> confirmCancellationPreview(
      @PathVariable String confirmationToken,
      @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey,
      @RequestAttribute(AgentToolContext.REQUEST_ATTRIBUTE) AgentToolContext context,
      HttpServletRequest request) {
    ConfirmBookingResponse result =
        application.confirmCancellationPreview(confirmationToken, idempotencyKey, context);
    return responseFactory.success(result, request);
  }
}
