package com.example.meeting.booking.application;

import com.example.meeting.agentgateway.internal.AgentToolDtos.ConfirmBookingResponse;
import com.example.meeting.booking.domain.BookingDraftRecord;
import com.example.meeting.booking.domain.IdempotencyRecord;
import com.example.meeting.booking.domain.NormalizedMeetingCommand;
import com.example.meeting.booking.infrastructure.BookingDraftMapper;
import com.example.meeting.booking.infrastructure.IdempotencyMapper;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import java.time.Clock;
import java.time.LocalDateTime;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class SynchronousDraftConfirmationService {

  private final BookingDraftMapper draftMapper;
  private final IdempotencyMapper idempotencyMapper;
  private final ConfirmationIdempotencySupport idempotencySupport;
  private final BookingTransactionService bookingTransactionService;
  private final BookingCompletionWriter completionWriter;
  private final DraftPayloadCodec payloadCodec;
  private final Clock clock;

  public SynchronousDraftConfirmationService(
      BookingDraftMapper draftMapper,
      IdempotencyMapper idempotencyMapper,
      ConfirmationIdempotencySupport idempotencySupport,
      BookingTransactionService bookingTransactionService,
      BookingCompletionWriter completionWriter,
      DraftPayloadCodec payloadCodec,
      Clock clock) {
    this.draftMapper = draftMapper;
    this.idempotencyMapper = idempotencyMapper;
    this.idempotencySupport = idempotencySupport;
    this.bookingTransactionService = bookingTransactionService;
    this.completionWriter = completionWriter;
    this.payloadCodec = payloadCodec;
    this.clock = clock;
  }

  @Transactional
  public ConfirmBookingResponse confirm(
      String confirmationToken,
      long userId,
      String traceId,
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
    NormalizedMeetingCommand command = payloadCodec.toCommand(draft.getPayloadJson(), userId);
    long meetingId =
        bookingTransactionService.createAgentMeeting(command, userId, draft.getRunId(), null);
    if (draftMapper.markUsed(draft.getId(), draft.getVersion(), now) != 1) {
      throw new BusinessException(ErrorCode.DRAFT_ALREADY_USED);
    }
    completionWriter.writeConfirmed(meetingId, null, userId, traceId, draft.getRunId(), false);
    ConfirmBookingResponse response = new ConfirmBookingResponse("SUCCESS", meetingId, null);
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
}
