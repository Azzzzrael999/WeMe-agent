package com.example.meeting.booking.application;

import com.example.meeting.agentgateway.internal.AgentToolDtos.ConfirmBookingResponse;
import com.example.meeting.booking.domain.BookingDraftRecord;
import com.example.meeting.booking.domain.BookingRequestRecord;
import com.example.meeting.booking.domain.IdempotencyRecord;
import com.example.meeting.booking.infrastructure.BookingDraftMapper;
import com.example.meeting.booking.infrastructure.BookingRequestMapper;
import com.example.meeting.booking.infrastructure.IdempotencyMapper;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.meeting.api.CreateMeetingRequest;
import com.example.meeting.mq.BookingCommandPayload;
import com.example.meeting.outbox.MessageOutboxMapper;
import com.example.meeting.outbox.OutboxEventFactory;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Clock;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.UUID;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class HotBookingAcceptanceService {

  private final BookingDraftMapper draftMapper;
  private final BookingRequestMapper requestMapper;
  private final IdempotencyMapper idempotencyMapper;
  private final MessageOutboxMapper outboxMapper;
  private final ConfirmationIdempotencySupport idempotencySupport;
  private final DraftPayloadCodec payloadCodec;
  private final OutboxEventFactory eventFactory;
  private final ObjectMapper objectMapper;
  private final Clock clock;

  public HotBookingAcceptanceService(
      BookingDraftMapper draftMapper,
      BookingRequestMapper requestMapper,
      IdempotencyMapper idempotencyMapper,
      MessageOutboxMapper outboxMapper,
      ConfirmationIdempotencySupport idempotencySupport,
      DraftPayloadCodec payloadCodec,
      OutboxEventFactory eventFactory,
      ObjectMapper objectMapper,
      Clock clock) {
    this.draftMapper = draftMapper;
    this.requestMapper = requestMapper;
    this.idempotencyMapper = idempotencyMapper;
    this.outboxMapper = outboxMapper;
    this.idempotencySupport = idempotencySupport;
    this.payloadCodec = payloadCodec;
    this.eventFactory = eventFactory;
    this.objectMapper = objectMapper;
    this.clock = clock;
  }

  @Transactional
  public ConfirmBookingResponse accept(
      String confirmationToken,
      long userId,
      String traceId,
      String toolCallId,
      String idempotencyKey,
      String requestHash) {
    LocalDateTime now = LocalDateTime.now(clock);
    idempotencyMapper.deleteExpired(
        userId, ConfirmationIdempotencySupport.OPERATION, idempotencyKey, now);
    IdempotencyRecord idempotency =
        idempotencySupport.newRecord(userId, idempotencyKey, requestHash, now);
    try {
      idempotencyMapper.insert(idempotency);
    } catch (DuplicateKeyException exception) {
      IdempotencyRecord existing =
          idempotencyMapper
              .findByKeyForUpdate(userId, ConfirmationIdempotencySupport.OPERATION, idempotencyKey)
              .orElseThrow(() -> exception);
      return idempotencySupport.replay(existing, requestHash, now).orElseThrow(() -> exception);
    }
    BookingDraftRecord draft = lockPendingDraft(confirmationToken, userId, now);
    CreateMeetingRequest draftPayload = payloadCodec.read(draft.getPayloadJson());
    String requestNo = nextRequestNo(now);
    BookingCommandPayload command = payloadCodec.toBookingCommand(requestNo, userId, draftPayload);

    BookingRequestRecord bookingRequest = new BookingRequestRecord();
    bookingRequest.setRequestNo(requestNo);
    bookingRequest.setUserId(userId);
    bookingRequest.setRunId(draft.getRunId());
    bookingRequest.setTraceId(traceId);
    bookingRequest.setToolCallId(toolCallId);
    bookingRequest.setOperation("CREATE");
    bookingRequest.setPayloadJson(write(command));
    bookingRequest.setStatus("PENDING");
    bookingRequest.setCreatedAt(now);
    bookingRequest.setUpdatedAt(now);
    requestMapper.insert(bookingRequest);
    outboxMapper.insert(
        eventFactory.bookingEvent(
            "BOOKING_COMMAND", requestNo, traceId, draft.getRunId(), command));
    if (draftMapper.markUsed(draft.getId(), draft.getVersion(), now) != 1) {
      throw new BusinessException(ErrorCode.DRAFT_ALREADY_USED);
    }
    ConfirmBookingResponse response = new ConfirmBookingResponse("PENDING", null, requestNo);
    if (idempotencyMapper.markSucceeded(idempotency.getId(), idempotencySupport.write(response))
        != 1) {
      throw new IllegalStateException("Confirmation idempotency transition failed");
    }
    return response;
  }

  private BookingDraftRecord lockPendingDraft(
      String confirmationToken, long userId, LocalDateTime now) {
    BookingDraftRecord draft =
        draftMapper
            .findByTokenForUpdate(confirmationToken)
            .orElseThrow(() -> new BusinessException(ErrorCode.DRAFT_EXPIRED));
    if (!draft.getUserId().equals(userId)) {
      throw new BusinessException(ErrorCode.FORBIDDEN);
    }
    if (!"PENDING".equals(draft.getStatus())) {
      throw new BusinessException(ErrorCode.DRAFT_ALREADY_USED);
    }
    if (!draft.getExpiresAt().isAfter(now)) {
      throw new BusinessException(ErrorCode.DRAFT_EXPIRED);
    }
    return draft;
  }

  private String nextRequestNo(LocalDateTime now) {
    return "BR"
        + now.format(DateTimeFormatter.ofPattern("yyyyMMddHHmmss"))
        + UUID.randomUUID().toString().replace("-", "").substring(0, 10).toUpperCase();
  }

  private String write(Object value) {
    try {
      return objectMapper.writeValueAsString(value);
    } catch (JsonProcessingException exception) {
      throw new IllegalStateException("Cannot serialize booking request", exception);
    }
  }
}
