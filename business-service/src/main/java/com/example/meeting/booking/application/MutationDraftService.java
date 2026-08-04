package com.example.meeting.booking.application;

import com.example.meeting.agentgateway.internal.AgentToolDtos.CancellationPreviewRequest;
import com.example.meeting.agentgateway.internal.AgentToolDtos.CreateCancellationPreviewResponse;
import com.example.meeting.agentgateway.internal.AgentToolDtos.CreateRescheduleDraftResponse;
import com.example.meeting.agentgateway.internal.AgentToolDtos.RescheduleDraftRequest;
import com.example.meeting.booking.domain.BookingDraftRecord;
import com.example.meeting.booking.domain.NormalizedMeetingCommand;
import com.example.meeting.booking.infrastructure.BookingDraftMapper;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AgentToolContext;
import com.example.meeting.meeting.api.MeetingView;
import com.example.meeting.meeting.application.MeetingQueryService;
import com.example.meeting.meeting.domain.MeetingRecord;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Clock;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.HexFormat;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class MutationDraftService {

  private final BookingDraftMapper draftMapper;
  private final MeetingQueryService meetingQueryService;
  private final MeetingCommandFactory commandFactory;
  private final BookingValidator bookingValidator;
  private final BookingDraftService bookingDraftService;
  private final BookingProperties properties;
  private final ObjectMapper objectMapper;
  private final Clock clock;
  private final ZoneId zoneId;

  public MutationDraftService(
      BookingDraftMapper draftMapper,
      MeetingQueryService meetingQueryService,
      MeetingCommandFactory commandFactory,
      BookingValidator bookingValidator,
      BookingDraftService bookingDraftService,
      BookingProperties properties,
      ObjectMapper objectMapper,
      Clock clock,
      @Value("${app.timezone}") String timezone) {
    this.draftMapper = draftMapper;
    this.meetingQueryService = meetingQueryService;
    this.commandFactory = commandFactory;
    this.bookingValidator = bookingValidator;
    this.bookingDraftService = bookingDraftService;
    this.properties = properties;
    this.objectMapper = objectMapper;
    this.clock = clock;
    this.zoneId = ZoneId.of(timezone);
  }

  @Transactional
  public CreateRescheduleDraftResponse createReschedule(
      RescheduleDraftRequest request, AgentToolContext context) {
    MeetingRecord snapshot =
        meetingQueryService.findManageableSnapshot(
            request.meetingId(), context.authenticatedUser());
    if (!"CONFIRMED".equals(snapshot.getStatus())
        || snapshot.getVersion() != request.expectedVersion()) {
      throw new BusinessException(ErrorCode.MEETING_STATE_CONFLICT);
    }
    NormalizedMeetingCommand command =
        commandFactory.update(request.toUpdateRequest(), snapshot.getOrganizerId());
    bookingValidator.validate(command);
    MeetingView before =
        meetingQueryService.getVisible(request.meetingId(), context.authenticatedUser());
    RescheduleDraftPayload payload =
        new RescheduleDraftPayload(request.meetingId(), request.toUpdateRequest());
    BookingDraftRecord draft = insertDraft("RESCHEDULE", payload, context);
    return new CreateRescheduleDraftResponse(
        draft.getConfirmationToken(),
        draft.getExpiresAt().atZone(zoneId).toOffsetDateTime(),
        before,
        bookingDraftService.toView(command));
  }

  @Transactional
  public CreateCancellationPreviewResponse createCancellation(
      CancellationPreviewRequest request, AgentToolContext context) {
    MeetingRecord snapshot =
        meetingQueryService.findManageableSnapshot(
            request.meetingId(), context.authenticatedUser());
    if (!"CONFIRMED".equals(snapshot.getStatus())) {
      throw new BusinessException(ErrorCode.MEETING_STATE_CONFLICT);
    }
    MeetingView meeting =
        meetingQueryService.getVisible(request.meetingId(), context.authenticatedUser());
    BookingDraftRecord draft =
        insertDraft(
            "CANCEL",
            new CancellationDraftPayload(request.meetingId(), snapshot.getVersion()),
            context);
    return new CreateCancellationPreviewResponse(
        draft.getConfirmationToken(),
        draft.getExpiresAt().atZone(zoneId).toOffsetDateTime(),
        meeting);
  }

  private BookingDraftRecord insertDraft(
      String operation, Object payload, AgentToolContext context) {
    LocalDateTime now = LocalDateTime.now(clock);
    // EDIT/replanning creates a fresh confirmation boundary.  Any older mutation draft for the
    // same Run must stop being confirmable before the new token is issued.
    draftMapper.invalidatePendingForRun(context.userId(), context.runId(), operation, now);
    String json = write(payload);
    BookingDraftRecord draft = new BookingDraftRecord();
    draft.setConfirmationToken("cfm_" + UUID.randomUUID().toString().replace("-", ""));
    draft.setUserId(context.userId());
    draft.setRunId(context.runId());
    draft.setToolCallId(context.toolCallId());
    draft.setOperation(operation);
    draft.setPayloadJson(json);
    draft.setPayloadHash(hash(json));
    draft.setStatus("PENDING");
    draft.setVersion(0);
    draft.setExpiresAt(now.plusMinutes(properties.draftTtlMinutes()));
    draft.setCreatedAt(now);
    draftMapper.insert(draft);
    return draft;
  }

  private String write(Object value) {
    try {
      return objectMapper.writeValueAsString(value);
    } catch (JsonProcessingException exception) {
      throw new IllegalStateException("Cannot serialize mutation draft", exception);
    }
  }

  private String hash(String value) {
    try {
      return HexFormat.of()
          .formatHex(
              MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
    } catch (NoSuchAlgorithmException exception) {
      throw new IllegalStateException("SHA-256 is unavailable", exception);
    }
  }
}
