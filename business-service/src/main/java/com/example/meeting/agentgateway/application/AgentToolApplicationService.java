package com.example.meeting.agentgateway.application;

import com.example.meeting.agentgateway.audit.AgentToolAuditService;
import com.example.meeting.agentgateway.internal.AgentToolDtos.CancellationPreviewRequest;
import com.example.meeting.agentgateway.internal.AgentToolDtos.ConfirmAuditRequest;
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
import com.example.meeting.agentgateway.internal.AgentToolQueryService;
import com.example.meeting.booking.application.BookingConfirmationService;
import com.example.meeting.booking.application.BookingDraftService;
import com.example.meeting.booking.application.MutationConfirmationService;
import com.example.meeting.booking.application.MutationDraftService;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AgentToolContext;
import com.example.meeting.meeting.api.CreateMeetingRequest;
import org.springframework.stereotype.Service;

@Service
public class AgentToolApplicationService {
  private final AgentToolQueryService queryService;
  private final BookingDraftService draftService;
  private final BookingConfirmationService confirmationService;
  private final MutationDraftService mutationDraftService;
  private final MutationConfirmationService mutationConfirmationService;
  private final AgentToolAuditService auditService;
  private final jakarta.validation.Validator validator;

  public AgentToolApplicationService(
      AgentToolQueryService queryService,
      BookingDraftService draftService,
      BookingConfirmationService confirmationService,
      MutationDraftService mutationDraftService,
      MutationConfirmationService mutationConfirmationService,
      AgentToolAuditService auditService,
      jakarta.validation.Validator validator) {
    this.queryService = queryService;
    this.draftService = draftService;
    this.confirmationService = confirmationService;
    this.mutationDraftService = mutationDraftService;
    this.mutationConfirmationService = mutationConfirmationService;
    this.auditService = auditService;
    this.validator = validator;
  }

  public ResolveEmployeesResponse resolveEmployees(
      ResolveEmployeesRequest body, AgentToolContext context) {
    validate(body);
    return auditService.execute(
        context,
        "resolve_employees",
        "READ",
        body,
        ResolveEmployeesResponse.class,
        () -> queryService.resolveEmployees(body));
  }

  public ResolveParticipantScopeResponse resolveParticipantScope(
      ResolveParticipantScopeRequest body, AgentToolContext context) {
    validate(body);
    return auditService.execute(
        context,
        "resolve_participant_scope",
        "READ",
        body,
        ResolveParticipantScopeResponse.class,
        () -> queryService.resolveParticipantScope(body, context));
  }

  public FreeBusyResponse freeBusy(FreeBusyRequest body, AgentToolContext context) {
    validate(body);
    return auditService.execute(
        context,
        "get_employee_free_busy",
        "READ",
        body,
        FreeBusyResponse.class,
        () -> queryService.getFreeBusy(body, context));
  }

  public SearchRoomsResponse searchRooms(SearchRoomsRequest body, AgentToolContext context) {
    validate(body);
    return auditService.execute(
        context,
        "search_available_rooms",
        "READ",
        body,
        SearchRoomsResponse.class,
        () -> queryService.searchAvailableRooms(body, context));
  }

  public RecentMeetingResponse recentMeeting(RecentMeetingRequest body, AgentToolContext context) {
    validate(body);
    return auditService.execute(
        context,
        "get_recent_meeting",
        "READ",
        body,
        RecentMeetingResponse.class,
        () -> queryService.recentMeetings(body, context));
  }

  public CreateDraftResponse createDraft(CreateMeetingRequest body, AgentToolContext context) {
    validate(body);
    return auditService.execute(
        context,
        "create_booking_draft",
        "DRAFT",
        body,
        CreateDraftResponse.class,
        () -> draftService.create(body, context));
  }

  public ConfirmBookingResponse confirmDraft(
      String confirmationToken, String idempotencyKey, AgentToolContext context) {
    validate(new ConfirmCommand(confirmationToken, idempotencyKey));
    ConfirmAuditRequest auditRequest = new ConfirmAuditRequest(confirmationToken, idempotencyKey);
    return auditService.execute(
        context,
        "confirm_booking",
        "WRITE",
        auditRequest,
        ConfirmBookingResponse.class,
        () -> confirmationService.confirm(confirmationToken, idempotencyKey, context));
  }

  public CreateRescheduleDraftResponse createRescheduleDraft(
      RescheduleDraftRequest body, AgentToolContext context) {
    validate(body);
    return auditService.execute(
        context,
        "create_reschedule_draft",
        "DRAFT",
        body,
        CreateRescheduleDraftResponse.class,
        () -> mutationDraftService.createReschedule(body, context));
  }

  public ConfirmBookingResponse confirmRescheduleDraft(
      String confirmationToken, String idempotencyKey, AgentToolContext context) {
    validate(new ConfirmCommand(confirmationToken, idempotencyKey));
    ConfirmAuditRequest auditRequest = new ConfirmAuditRequest(confirmationToken, idempotencyKey);
    return auditService.execute(
        context,
        "confirm_reschedule",
        "WRITE",
        auditRequest,
        ConfirmBookingResponse.class,
        () ->
            mutationConfirmationService.confirmReschedule(
                confirmationToken, idempotencyKey, context));
  }

  public CreateCancellationPreviewResponse createCancellationPreview(
      CancellationPreviewRequest body, AgentToolContext context) {
    validate(body);
    return auditService.execute(
        context,
        "create_cancellation_preview",
        "DRAFT",
        body,
        CreateCancellationPreviewResponse.class,
        () -> mutationDraftService.createCancellation(body, context));
  }

  public ConfirmBookingResponse confirmCancellationPreview(
      String confirmationToken, String idempotencyKey, AgentToolContext context) {
    validate(new ConfirmCommand(confirmationToken, idempotencyKey));
    ConfirmAuditRequest auditRequest = new ConfirmAuditRequest(confirmationToken, idempotencyKey);
    return auditService.execute(
        context,
        "confirm_cancellation",
        "WRITE",
        auditRequest,
        ConfirmBookingResponse.class,
        () ->
            mutationConfirmationService.confirmCancellation(
                confirmationToken, idempotencyKey, context));
  }

  public record ConfirmCommand(
      @jakarta.validation.constraints.NotBlank @jakarta.validation.constraints.Size(max = 80) String confirmationToken,
      @jakarta.validation.constraints.NotBlank @jakarta.validation.constraints.Size(max = 128) String idempotencyKey) {}

  private void validate(Object command) {
    if (!validator.validate(command).isEmpty()) {
      throw new BusinessException(ErrorCode.VALIDATION_ERROR);
    }
  }
}
