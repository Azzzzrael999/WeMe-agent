package com.example.meeting.booking.application;

import com.example.meeting.agentgateway.internal.AgentToolDtos.ConfirmBookingResponse;
import com.example.meeting.booking.domain.BookingDraftRecord;
import com.example.meeting.booking.domain.IdempotencyRecord;
import com.example.meeting.booking.infrastructure.BookingDraftMapper;
import com.example.meeting.booking.infrastructure.IdempotencyMapper;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.json.StoredJson;
import com.example.meeting.common.security.AgentToolContext;
import com.example.meeting.meeting.api.MeetingView;
import com.example.meeting.meeting.application.MeetingApplicationService;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Clock;
import java.time.LocalDateTime;
import java.util.Optional;
import java.util.function.Supplier;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class MutationConfirmationService {

  public static final String RESCHEDULE_OPERATION = "CONFIRM_AGENT_RESCHEDULE";
  public static final String CANCEL_OPERATION = "CONFIRM_AGENT_CANCEL";

  private final BookingDraftMapper draftMapper;
  private final IdempotencyMapper idempotencyMapper;
  private final IdempotencyKeyCoordinator coordinator;
  private final ConfirmationIdempotencySupport idempotencySupport;
  private final MeetingApplicationService meetingService;
  private final ObjectMapper objectMapper;
  private final Clock clock;

  public MutationConfirmationService(
      BookingDraftMapper draftMapper,
      IdempotencyMapper idempotencyMapper,
      IdempotencyKeyCoordinator coordinator,
      ConfirmationIdempotencySupport idempotencySupport,
      MeetingApplicationService meetingService,
      ObjectMapper objectMapper,
      Clock clock) {
    this.draftMapper = draftMapper;
    this.idempotencyMapper = idempotencyMapper;
    this.coordinator = coordinator;
    this.idempotencySupport = idempotencySupport;
    this.meetingService = meetingService;
    this.objectMapper = objectMapper;
    this.clock = clock;
  }

  @Transactional
  public ConfirmBookingResponse confirmReschedule(
      String token, String rawIdempotencyKey, AgentToolContext context) {
    return confirm(
        token,
        rawIdempotencyKey,
        context,
        "RESCHEDULE",
        RESCHEDULE_OPERATION,
        () -> {
          BookingDraftRecord draft = requiredDraft(token, context.userId(), "RESCHEDULE");
          RescheduleDraftPayload payload =
              read(draft.getPayloadJson(), RescheduleDraftPayload.class);
          MeetingView meeting =
              meetingService.update(
                  payload.meetingId(), payload.request(), context.authenticatedUser());
          return new ConfirmBookingResponse("SUCCESS", meeting.id(), null);
        });
  }

  @Transactional
  public ConfirmBookingResponse confirmCancellation(
      String token, String rawIdempotencyKey, AgentToolContext context) {
    return confirm(
        token,
        rawIdempotencyKey,
        context,
        "CANCEL",
        CANCEL_OPERATION,
        () -> {
          BookingDraftRecord draft = requiredDraft(token, context.userId(), "CANCEL");
          CancellationDraftPayload payload =
              read(draft.getPayloadJson(), CancellationDraftPayload.class);
          MeetingView meeting =
              meetingService.cancel(
                  payload.meetingId(), payload.expectedVersion(), context.authenticatedUser());
          return new ConfirmBookingResponse("SUCCESS", meeting.id(), null);
        });
  }

  private ConfirmBookingResponse confirm(
      String token,
      String rawIdempotencyKey,
      AgentToolContext context,
      String draftOperation,
      String idempotencyOperation,
      Supplier<ConfirmBookingResponse> action) {
    String idempotencyKey = coordinator.normalize(rawIdempotencyKey);
    BookingDraftRecord snapshot =
        draftMapper
            .findByToken(token)
            .orElseThrow(() -> new BusinessException(ErrorCode.DRAFT_EXPIRED));
    if (!snapshot.getUserId().equals(context.userId())) {
      throw new BusinessException(ErrorCode.FORBIDDEN);
    }
    String requestHash = idempotencySupport.requestHash(token, snapshot.getPayloadHash());
    return coordinator.execute(
        context.userId(),
        idempotencyOperation,
        idempotencyKey,
        () ->
            confirmLocked(
                token,
                context.userId(),
                idempotencyKey,
                requestHash,
                draftOperation,
                idempotencyOperation,
                action));
  }

  private ConfirmBookingResponse confirmLocked(
      String token,
      long userId,
      String idempotencyKey,
      String requestHash,
      String draftOperation,
      String idempotencyOperation,
      Supplier<ConfirmBookingResponse> action) {
    LocalDateTime now = LocalDateTime.now(clock);
    Optional<IdempotencyRecord> current =
        idempotencyMapper.findByKey(userId, idempotencyOperation, idempotencyKey);
    if (current.isPresent()) {
      Optional<ConfirmBookingResponse> replay =
          idempotencySupport.replay(current.get(), requestHash, now);
      if (replay.isPresent()) {
        return replay.get();
      }
    }
    idempotencyMapper.deleteExpired(userId, idempotencyOperation, idempotencyKey, now);
    IdempotencyRecord idempotency =
        idempotencySupport.newRecord(
            idempotencyOperation, userId, idempotencyKey, requestHash, now);
    try {
      idempotencyMapper.insert(idempotency);
    } catch (DuplicateKeyException exception) {
      IdempotencyRecord existing =
          idempotencyMapper
              .findByKeyForUpdate(userId, idempotencyOperation, idempotencyKey)
              .orElseThrow(() -> exception);
      return idempotencySupport.replay(existing, requestHash, now).orElseThrow(() -> exception);
    }
    BookingDraftRecord draft = requiredDraftForUpdate(token, userId, draftOperation, now);
    ConfirmBookingResponse response = action.get();
    if (draftMapper.markUsed(draft.getId(), draft.getVersion(), now) != 1) {
      throw new BusinessException(ErrorCode.DRAFT_ALREADY_USED);
    }
    if (idempotencyMapper.markSucceeded(idempotency.getId(), idempotencySupport.write(response))
        != 1) {
      throw new IllegalStateException("Mutation confirmation idempotency transition failed");
    }
    return response;
  }

  private BookingDraftRecord requiredDraft(String token, long userId, String operation) {
    BookingDraftRecord draft =
        draftMapper
            .findByToken(token)
            .orElseThrow(() -> new BusinessException(ErrorCode.DRAFT_EXPIRED));
    validateDraft(draft, userId, operation, LocalDateTime.now(clock));
    return draft;
  }

  private BookingDraftRecord requiredDraftForUpdate(
      String token, long userId, String operation, LocalDateTime now) {
    BookingDraftRecord draft =
        draftMapper
            .findByTokenForUpdate(token)
            .orElseThrow(() -> new BusinessException(ErrorCode.DRAFT_EXPIRED));
    validateDraft(draft, userId, operation, now);
    return draft;
  }

  private void validateDraft(
      BookingDraftRecord draft, long userId, String operation, LocalDateTime now) {
    if (!draft.getUserId().equals(userId)) {
      throw new BusinessException(ErrorCode.FORBIDDEN);
    }
    if (!operation.equals(draft.getOperation()) || !"PENDING".equals(draft.getStatus())) {
      throw new BusinessException(ErrorCode.DRAFT_ALREADY_USED);
    }
    if (!draft.getExpiresAt().isAfter(now)) {
      throw new BusinessException(ErrorCode.DRAFT_EXPIRED);
    }
  }

  private <T> T read(String json, Class<T> type) {
    try {
      return StoredJson.read(objectMapper, json, type);
    } catch (JsonProcessingException exception) {
      throw new IllegalStateException("Stored mutation draft is invalid", exception);
    }
  }
}
