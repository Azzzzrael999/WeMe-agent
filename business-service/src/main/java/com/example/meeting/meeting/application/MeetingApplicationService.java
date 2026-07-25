package com.example.meeting.meeting.application;

import com.example.meeting.booking.application.BookingTransactionService;
import com.example.meeting.booking.application.BookingValidator;
import com.example.meeting.booking.application.IdempotencyKeyCoordinator;
import com.example.meeting.booking.application.IdempotencySupport;
import com.example.meeting.booking.application.MeetingCommandFactory;
import com.example.meeting.booking.application.MeetingRequestHasher;
import com.example.meeting.booking.domain.IdempotencyRecord;
import com.example.meeting.booking.domain.NormalizedMeetingCommand;
import com.example.meeting.booking.domain.SlotHoldReservation;
import com.example.meeting.booking.domain.TimeSlotCalculator;
import com.example.meeting.booking.infrastructure.IdempotencyMapper;
import com.example.meeting.booking.infrastructure.RedisSlotHoldService;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.web.ApiErrorDetail;
import com.example.meeting.meeting.api.CreateMeetingRequest;
import com.example.meeting.meeting.api.MeetingListView;
import com.example.meeting.meeting.api.MeetingView;
import com.example.meeting.meeting.api.UpdateMeetingRequest;
import com.example.meeting.meeting.domain.MeetingRecord;
import java.time.Clock;
import java.time.Duration;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.format.DateTimeParseException;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import java.util.Set;
import java.util.concurrent.ThreadLocalRandom;
import java.util.function.LongSupplier;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.dao.PessimisticLockingFailureException;
import org.springframework.stereotype.Service;

@Service
public class MeetingApplicationService {

  private static final int DEADLOCK_RETRIES = 2;
  private static final int DEFAULT_PAGE = 1;
  private static final int DEFAULT_PAGE_SIZE = 20;
  private static final int MAX_PAGE_SIZE = 100;
  private static final Duration MAX_QUERY_WINDOW = Duration.ofDays(14);
  private static final Set<String> MEETING_STATUSES = Set.of("CONFIRMED", "CANCELLED", "COMPLETED");

  private final MeetingCommandFactory commandFactory;
  private final MeetingRequestHasher requestHasher;
  private final IdempotencyKeyCoordinator idempotencyCoordinator;
  private final IdempotencyMapper idempotencyMapper;
  private final IdempotencySupport idempotencySupport;
  private final BookingValidator bookingValidator;
  private final RedisSlotHoldService slotHoldService;
  private final BookingTransactionService transactionService;
  private final MeetingQueryService queryService;
  private final TimeSlotCalculator timeSlotCalculator;
  private final Clock clock;

  public MeetingApplicationService(
      MeetingCommandFactory commandFactory,
      MeetingRequestHasher requestHasher,
      IdempotencyKeyCoordinator idempotencyCoordinator,
      IdempotencyMapper idempotencyMapper,
      IdempotencySupport idempotencySupport,
      BookingValidator bookingValidator,
      RedisSlotHoldService slotHoldService,
      BookingTransactionService transactionService,
      MeetingQueryService queryService,
      TimeSlotCalculator timeSlotCalculator,
      Clock clock) {
    this.commandFactory = commandFactory;
    this.requestHasher = requestHasher;
    this.idempotencyCoordinator = idempotencyCoordinator;
    this.idempotencyMapper = idempotencyMapper;
    this.idempotencySupport = idempotencySupport;
    this.bookingValidator = bookingValidator;
    this.slotHoldService = slotHoldService;
    this.transactionService = transactionService;
    this.queryService = queryService;
    this.timeSlotCalculator = timeSlotCalculator;
    this.clock = clock;
  }

  public MeetingView create(
      CreateMeetingRequest request, String rawIdempotencyKey, AuthenticatedUser organizer) {
    String idempotencyKey = idempotencyCoordinator.normalize(rawIdempotencyKey);
    NormalizedMeetingCommand command = commandFactory.create(request, organizer.userId());
    String requestHash = requestHasher.hash(command);
    return idempotencyCoordinator.execute(
        organizer.userId(),
        BookingTransactionService.CREATE_OPERATION,
        idempotencyKey,
        () -> createLocked(command, idempotencyKey, requestHash, organizer));
  }

  public MeetingView update(long meetingId, UpdateMeetingRequest request, AuthenticatedUser actor) {
    MeetingRecord snapshot = queryService.findManageableSnapshot(meetingId, actor);
    if (!"CONFIRMED".equals(snapshot.getStatus())
        || snapshot.getVersion() != request.expectedVersion()) {
      throw new BusinessException(ErrorCode.MEETING_STATE_CONFLICT);
    }
    NormalizedMeetingCommand command = commandFactory.update(request, snapshot.getOrganizerId());
    bookingValidator.validate(command);
    SlotHoldReservation hold =
        slotHoldService.acquire(
            command, actor.userId(), "update:" + meetingId + ":" + request.expectedVersion());
    try {
      runWrite(
          () -> transactionService.update(meetingId, command, request.expectedVersion(), actor));
      return queryService.getVisible(meetingId, actor);
    } finally {
      slotHoldService.release(hold);
    }
  }

  public MeetingView cancel(long meetingId, AuthenticatedUser actor) {
    runWrite(() -> transactionService.cancel(meetingId, actor));
    return queryService.getVisible(meetingId, actor);
  }

  public MeetingView get(long meetingId, AuthenticatedUser actor) {
    return queryService.getVisible(meetingId, actor);
  }

  public MeetingListView list(
      String rawFrom,
      String rawTo,
      String rawStatus,
      String rawPage,
      String rawSize,
      AuthenticatedUser actor) {
    OffsetDateTime from = parseTime(rawFrom, "from");
    OffsetDateTime to = parseTime(rawTo, "to");
    if (from != null && to != null && !to.isAfter(from)) {
      throw validation("to", "INVALID_TIME_RANGE", "to 必须晚于 from");
    }
    if (from != null && to != null && Duration.between(from, to).compareTo(MAX_QUERY_WINDOW) > 0) {
      throw validation("to", "QUERY_WINDOW_TOO_LARGE", "查询时间窗口不能超过 14 天");
    }
    String status = parseStatus(rawStatus);
    int page = parsePageParameter(rawPage, "page", DEFAULT_PAGE, Integer.MAX_VALUE);
    int size = parsePageParameter(rawSize, "size", DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE);
    return queryService.list(
        actor,
        from == null ? null : from.toLocalDateTime(),
        to == null ? null : to.toLocalDateTime(),
        status,
        page,
        size);
  }

  private MeetingView createLocked(
      NormalizedMeetingCommand command,
      String idempotencyKey,
      String requestHash,
      AuthenticatedUser organizer) {
    LocalDateTime now = LocalDateTime.now(clock);
    Optional<IdempotencyRecord> existing =
        idempotencyMapper.findByKey(
            organizer.userId(), BookingTransactionService.CREATE_OPERATION, idempotencyKey);
    if (existing.isPresent()) {
      Optional<Long> replay = idempotencySupport.replay(existing.get(), requestHash, now);
      if (replay.isPresent()) {
        return queryService.getVisible(replay.get(), organizer);
      }
    }

    bookingValidator.validate(command);
    SlotHoldReservation hold =
        slotHoldService.acquire(command, organizer.userId(), "create:" + idempotencyKey);
    try {
      long meetingId =
          runWrite(
              () ->
                  transactionService.create(
                      command, organizer.userId(), idempotencyKey, requestHash));
      return queryService.getVisible(meetingId, organizer);
    } finally {
      slotHoldService.release(hold);
    }
  }

  private long runWrite(LongSupplier write) {
    for (int attempt = 0; ; attempt++) {
      try {
        return write.getAsLong();
      } catch (PessimisticLockingFailureException exception) {
        if (attempt >= DEADLOCK_RETRIES) {
          throw new BusinessException(ErrorCode.DEPENDENCY_UNAVAILABLE, "数据库竞争繁忙，请稍后重试");
        }
        shortBackoff();
      } catch (DuplicateKeyException exception) {
        throw new BusinessException(ErrorCode.BOOKING_CONFLICT);
      } catch (DataIntegrityViolationException exception) {
        throw new BusinessException(ErrorCode.BOOKING_CONFLICT);
      }
    }
  }

  private OffsetDateTime parseTime(String rawValue, String field) {
    if (rawValue == null) {
      return null;
    }
    if (rawValue.isBlank()) {
      throw validation(field, "INVALID_DATE_TIME", field + " 不是有效的带偏移时间");
    }
    try {
      OffsetDateTime parsed = OffsetDateTime.parse(rawValue);
      return timeSlotCalculator.normalizeQueryTime(parsed, field);
    } catch (DateTimeParseException exception) {
      throw validation(field, "INVALID_DATE_TIME", field + " 不是有效的带偏移时间");
    }
  }

  private String parseStatus(String rawStatus) {
    if (rawStatus == null) {
      return null;
    }
    String status = rawStatus.trim().toUpperCase(Locale.ROOT);
    if (!MEETING_STATUSES.contains(status)) {
      throw validation("status", "INVALID_MEETING_STATUS", "status 不是有效的会议状态");
    }
    return status;
  }

  private int parsePageParameter(String rawValue, String field, int defaultValue, int maximum) {
    if (rawValue == null) {
      return defaultValue;
    }
    if (rawValue.isBlank()) {
      throw validation(field, "INVALID_PAGINATION", field + " 必须是正整数");
    }
    try {
      int value = Integer.parseInt(rawValue);
      if (value < 1 || value > maximum) {
        throw validation(field, "INVALID_PAGINATION", field + " 超出允许范围");
      }
      return value;
    } catch (NumberFormatException exception) {
      throw validation(field, "INVALID_PAGINATION", field + " 必须是正整数");
    }
  }

  private void shortBackoff() {
    try {
      Thread.sleep(ThreadLocalRandom.current().nextLong(10, 51));
    } catch (InterruptedException exception) {
      Thread.currentThread().interrupt();
      throw new BusinessException(ErrorCode.DEPENDENCY_UNAVAILABLE, "请求已中断，请稍后重试");
    }
  }

  private BusinessException validation(String field, String reason, String message) {
    return new BusinessException(
        ErrorCode.VALIDATION_ERROR, message, List.of(new ApiErrorDetail(field, reason)));
  }
}
