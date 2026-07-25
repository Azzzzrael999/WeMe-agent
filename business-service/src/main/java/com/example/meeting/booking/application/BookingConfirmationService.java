package com.example.meeting.booking.application;

import com.example.meeting.agentgateway.internal.AgentToolDtos.ConfirmBookingResponse;
import com.example.meeting.booking.domain.BookingDraftRecord;
import com.example.meeting.booking.domain.IdempotencyRecord;
import com.example.meeting.booking.domain.NormalizedMeetingCommand;
import com.example.meeting.booking.domain.SlotHoldReservation;
import com.example.meeting.booking.infrastructure.BookingDraftMapper;
import com.example.meeting.booking.infrastructure.IdempotencyMapper;
import com.example.meeting.booking.infrastructure.RedisSlotHoldService;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AgentToolContext;
import com.example.meeting.room.domain.MeetingRoom;
import com.example.meeting.room.infrastructure.MeetingRoomMapper;
import java.time.Clock;
import java.time.LocalDateTime;
import java.util.Optional;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;

@Service
public class BookingConfirmationService {

  private final BookingDraftMapper draftMapper;
  private final IdempotencyMapper idempotencyMapper;
  private final IdempotencyKeyCoordinator idempotencyCoordinator;
  private final ConfirmationIdempotencySupport idempotencySupport;
  private final DraftPayloadCodec payloadCodec;
  private final BookingValidator bookingValidator;
  private final RedisSlotHoldService slotHoldService;
  private final MeetingRoomMapper roomMapper;
  private final SynchronousDraftConfirmationService synchronousService;
  private final HotBookingAcceptanceService hotService;
  private final BookingProperties properties;
  private final Clock clock;

  public BookingConfirmationService(
      BookingDraftMapper draftMapper,
      IdempotencyMapper idempotencyMapper,
      IdempotencyKeyCoordinator idempotencyCoordinator,
      ConfirmationIdempotencySupport idempotencySupport,
      DraftPayloadCodec payloadCodec,
      BookingValidator bookingValidator,
      RedisSlotHoldService slotHoldService,
      MeetingRoomMapper roomMapper,
      SynchronousDraftConfirmationService synchronousService,
      HotBookingAcceptanceService hotService,
      BookingProperties properties,
      Clock clock) {
    this.draftMapper = draftMapper;
    this.idempotencyMapper = idempotencyMapper;
    this.idempotencyCoordinator = idempotencyCoordinator;
    this.idempotencySupport = idempotencySupport;
    this.payloadCodec = payloadCodec;
    this.bookingValidator = bookingValidator;
    this.slotHoldService = slotHoldService;
    this.roomMapper = roomMapper;
    this.synchronousService = synchronousService;
    this.hotService = hotService;
    this.properties = properties;
    this.clock = clock;
  }

  public ConfirmBookingResponse confirm(
      String confirmationToken, String rawIdempotencyKey, AgentToolContext context) {
    if (confirmationToken == null
        || confirmationToken.isBlank()
        || confirmationToken.length() > 80) {
      throw new BusinessException(ErrorCode.DRAFT_EXPIRED);
    }
    String idempotencyKey = idempotencyCoordinator.normalize(rawIdempotencyKey);
    BookingDraftRecord draft =
        draftMapper
            .findByToken(confirmationToken)
            .orElseThrow(() -> new BusinessException(ErrorCode.DRAFT_EXPIRED));
    if (!draft.getUserId().equals(context.userId())) {
      throw new BusinessException(ErrorCode.FORBIDDEN);
    }
    String requestHash = idempotencySupport.requestHash(confirmationToken, draft.getPayloadHash());
    return idempotencyCoordinator.execute(
        context.userId(),
        ConfirmationIdempotencySupport.OPERATION,
        idempotencyKey,
        () -> confirmLocked(draft, confirmationToken, idempotencyKey, requestHash, context));
  }

  private ConfirmBookingResponse confirmLocked(
      BookingDraftRecord draft,
      String confirmationToken,
      String idempotencyKey,
      String requestHash,
      AgentToolContext context) {
    LocalDateTime now = LocalDateTime.now(clock);
    Optional<IdempotencyRecord> existing =
        idempotencyMapper.findByKey(
            context.userId(), ConfirmationIdempotencySupport.OPERATION, idempotencyKey);
    if (existing.isPresent()) {
      Optional<ConfirmBookingResponse> replay =
          idempotencySupport.replay(existing.get(), requestHash, now);
      if (replay.isPresent()) {
        return replay.get();
      }
    }
    if (!"PENDING".equals(draft.getStatus())) {
      throw new BusinessException(ErrorCode.DRAFT_ALREADY_USED);
    }
    if (!draft.getExpiresAt().isAfter(now)) {
      throw new BusinessException(ErrorCode.DRAFT_EXPIRED);
    }
    NormalizedMeetingCommand command =
        payloadCodec.toCommand(draft.getPayloadJson(), context.userId());
    bookingValidator.validate(command);
    MeetingRoom room = roomMapper.selectById(command.roomId());
    if (Boolean.TRUE.equals(room.getIsHot())) {
      if (!properties.hotBookingEnabled()) {
        throw new BusinessException(ErrorCode.DEPENDENCY_UNAVAILABLE, "热门异步预约当前不可用");
      }
      return hotService.accept(
          confirmationToken,
          context.userId(),
          context.traceId(),
          context.toolCallId(),
          idempotencyKey,
          requestHash);
    }
    SlotHoldReservation hold =
        slotHoldService.acquire(
            command, context.userId(), "confirm:" + confirmationToken + ':' + idempotencyKey);
    try {
      return synchronousService.confirm(
          confirmationToken, context.userId(), context.traceId(), idempotencyKey, requestHash);
    } catch (DataIntegrityViolationException exception) {
      throw new BusinessException(ErrorCode.BOOKING_CONFLICT);
    } finally {
      slotHoldService.release(hold);
    }
  }
}
