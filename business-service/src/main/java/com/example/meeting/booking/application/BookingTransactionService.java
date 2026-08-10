package com.example.meeting.booking.application;

import com.example.meeting.booking.domain.EmployeeBusySlotRecord;
import com.example.meeting.booking.domain.IdempotencyRecord;
import com.example.meeting.booking.domain.MeetingRoomSlotRecord;
import com.example.meeting.booking.domain.NormalizedMeetingCommand;
import com.example.meeting.booking.domain.TimeSlot;
import com.example.meeting.booking.infrastructure.EmployeeBusySlotMapper;
import com.example.meeting.booking.infrastructure.IdempotencyMapper;
import com.example.meeting.booking.infrastructure.MeetingRoomSlotMapper;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.meeting.application.MeetingNumberGenerator;
import com.example.meeting.meeting.domain.MeetingParticipantRecord;
import com.example.meeting.meeting.domain.MeetingRecord;
import com.example.meeting.meeting.infrastructure.MeetingMapper;
import com.example.meeting.meeting.infrastructure.MeetingParticipantMapper;
import com.example.meeting.replan.application.ReplanCaseLifecycleService;
import java.time.Clock;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class BookingTransactionService {

  public static final String CREATE_OPERATION = "CREATE_MANUAL_MEETING";

  private final MeetingMapper meetingMapper;
  private final MeetingParticipantMapper participantMapper;
  private final MeetingRoomSlotMapper roomSlotMapper;
  private final EmployeeBusySlotMapper busySlotMapper;
  private final IdempotencyMapper idempotencyMapper;
  private final IdempotencySupport idempotencySupport;
  private final BookingValidator bookingValidator;
  private final BookingProperties bookingProperties;
  private final MeetingNumberGenerator meetingNumberGenerator;
  private final BookingCompletionWriter completionWriter;
  private final ReplanCaseLifecycleService replanCaseLifecycleService;
  private final Clock clock;

  public BookingTransactionService(
      MeetingMapper meetingMapper,
      MeetingParticipantMapper participantMapper,
      MeetingRoomSlotMapper roomSlotMapper,
      EmployeeBusySlotMapper busySlotMapper,
      IdempotencyMapper idempotencyMapper,
      IdempotencySupport idempotencySupport,
      BookingValidator bookingValidator,
      BookingProperties bookingProperties,
      MeetingNumberGenerator meetingNumberGenerator,
      BookingCompletionWriter completionWriter,
      ReplanCaseLifecycleService replanCaseLifecycleService,
      Clock clock) {
    this.meetingMapper = meetingMapper;
    this.participantMapper = participantMapper;
    this.roomSlotMapper = roomSlotMapper;
    this.busySlotMapper = busySlotMapper;
    this.idempotencyMapper = idempotencyMapper;
    this.idempotencySupport = idempotencySupport;
    this.bookingValidator = bookingValidator;
    this.bookingProperties = bookingProperties;
    this.meetingNumberGenerator = meetingNumberGenerator;
    this.completionWriter = completionWriter;
    this.replanCaseLifecycleService = replanCaseLifecycleService;
    this.clock = clock;
  }

  @Transactional
  public long create(
      NormalizedMeetingCommand command,
      long organizerId,
      String idempotencyKey,
      String requestHash) {
    LocalDateTime now = LocalDateTime.now(clock);
    idempotencyMapper.deleteExpired(organizerId, CREATE_OPERATION, idempotencyKey, now);

    IdempotencyRecord idempotency = new IdempotencyRecord();
    idempotency.setUserId(organizerId);
    idempotency.setOperation(CREATE_OPERATION);
    idempotency.setIdempotencyKey(idempotencyKey);
    idempotency.setRequestHash(requestHash);
    idempotency.setStatus("PROCESSING");
    idempotency.setExpiresAt(now.plusHours(bookingProperties.idempotencyTtlHours()));
    try {
      idempotencyMapper.insert(idempotency);
    } catch (DuplicateKeyException duplicateKeyException) {
      IdempotencyRecord existing =
          idempotencyMapper
              .findByKeyForUpdate(organizerId, CREATE_OPERATION, idempotencyKey)
              .orElseThrow(() -> duplicateKeyException);
      return idempotencySupport.replayRequired(existing, requestHash);
    }

    bookingValidator.validate(command);

    MeetingRecord meeting = createMeeting(command, organizerId, "MANUAL", null, null, now);
    meetingMapper.insert(meeting);
    writeParticipantsAndSlots(meeting.getId(), command);
    completionWriter.writeConfirmed(meeting.getId(), null, organizerId, null, null, false);

    int updated =
        idempotencyMapper.markSucceeded(
            idempotency.getId(), idempotencySupport.responseJson(meeting.getId()));
    if (updated != 1) {
      throw new IllegalStateException("Idempotency state transition failed");
    }
    return meeting.getId();
  }

  @Transactional(propagation = Propagation.MANDATORY)
  public long createAgentMeeting(
      NormalizedMeetingCommand command, long organizerId, String runId, String requestNo) {
    bookingValidator.validate(command);
    LocalDateTime now = LocalDateTime.now(clock);
    MeetingRecord meeting = createMeeting(command, organizerId, "AGENT", runId, requestNo, now);
    meetingMapper.insert(meeting);
    writeParticipantsAndSlots(meeting.getId(), command);
    return meeting.getId();
  }

  @Transactional
  public long update(
      long meetingId,
      NormalizedMeetingCommand command,
      int expectedVersion,
      AuthenticatedUser actor) {
    return updateInternal(meetingId, command, expectedVersion, actor, null, null);
  }

  @Transactional
  public long updateForReplan(
      long meetingId,
      NormalizedMeetingCommand command,
      int expectedVersion,
      AuthenticatedUser actor,
      long caseId,
      int expectedCaseVersion) {
    return updateInternal(meetingId, command, expectedVersion, actor, caseId, expectedCaseVersion);
  }

  private long updateInternal(
      long meetingId,
      NormalizedMeetingCommand command,
      int expectedVersion,
      AuthenticatedUser actor,
      Long replanCaseId,
      Integer expectedCaseVersion) {
    MeetingRecord meeting = findLocked(meetingId);
    assertManagePermission(meeting, actor);
    if (!"CONFIRMED".equals(meeting.getStatus()) || meeting.getVersion() != expectedVersion) {
      throw new BusinessException(ErrorCode.MEETING_STATE_CONFLICT);
    }
    bookingValidator.validate(command);
    List<Long> previousParticipantIds = participantMapper.findEmployeeIdsByMeetingId(meetingId);

    LocalDateTime now = LocalDateTime.now(clock);
    int updated =
        meetingMapper.updateConfirmedMeeting(
            meetingId,
            command.title(),
            command.meetingType(),
            command.roomId(),
            command.schedule().localStartAt(),
            command.schedule().localEndAt(),
            expectedVersion,
            now);
    if (updated != 1) {
      throw new BusinessException(ErrorCode.MEETING_STATE_CONFLICT);
    }

    roomSlotMapper.deleteByMeetingId(meetingId);
    busySlotMapper.deleteByMeetingId(meetingId);
    participantMapper.deleteByMeetingId(meetingId);
    writeParticipantsAndSlots(meetingId, command);
    completionWriter.writeChanged(meetingId, meeting.getOrganizerId(), previousParticipantIds);
    if (replanCaseId == null) {
      replanCaseLifecycleService.resolveAfterMeetingUpdate(
          meetingId,
          command.roomId(),
          command.schedule().localStartAt(),
          command.schedule().localEndAt());
    } else {
      replanCaseLifecycleService.resolveQuick(
          replanCaseId,
          meetingId,
          expectedCaseVersion,
          command.roomId(),
          command.schedule().localStartAt(),
          command.schedule().localEndAt());
    }
    return meetingId;
  }

  @Transactional
  public long cancel(long meetingId, Integer expectedVersion, AuthenticatedUser actor) {
    MeetingRecord meeting = findLocked(meetingId);
    assertManagePermission(meeting, actor);
    if (!"CONFIRMED".equals(meeting.getStatus())
        || (expectedVersion != null && meeting.getVersion() != expectedVersion)) {
      throw new BusinessException(ErrorCode.MEETING_STATE_CONFLICT);
    }
    LocalDateTime now = LocalDateTime.now(clock);
    if (meetingMapper.cancelConfirmedMeeting(meetingId, now) != 1) {
      throw new BusinessException(ErrorCode.MEETING_STATE_CONFLICT);
    }
    roomSlotMapper.deleteByMeetingId(meetingId);
    busySlotMapper.deleteByMeetingId(meetingId);
    completionWriter.writeCancelled(meetingId, meeting.getOrganizerId());
    replanCaseLifecycleService.cancelAfterMeetingCancellation(meetingId);
    return meetingId;
  }

  private MeetingRecord createMeeting(
      NormalizedMeetingCommand command,
      long organizerId,
      String source,
      String runId,
      String requestNo,
      LocalDateTime now) {
    MeetingRecord meeting = new MeetingRecord();
    meeting.setMeetingNo(meetingNumberGenerator.next(command));
    meeting.setTitle(command.title());
    meeting.setMeetingType(command.meetingType());
    meeting.setOrganizerId(organizerId);
    meeting.setRoomId(command.roomId());
    meeting.setStartAt(command.schedule().localStartAt());
    meeting.setEndAt(command.schedule().localEndAt());
    meeting.setStatus("CONFIRMED");
    meeting.setSource(source);
    meeting.setRunId(runId);
    meeting.setRequestNo(requestNo);
    meeting.setVersion(0);
    meeting.setCreatedAt(now);
    meeting.setUpdatedAt(now);
    return meeting;
  }

  private void writeParticipantsAndSlots(long meetingId, NormalizedMeetingCommand command) {
    List<MeetingParticipantRecord> participants = new ArrayList<>();
    for (Long employeeId : command.requiredParticipantIds()) {
      participants.add(new MeetingParticipantRecord(meetingId, employeeId, "REQUIRED"));
    }
    for (Long employeeId : command.optionalParticipantIds()) {
      participants.add(new MeetingParticipantRecord(meetingId, employeeId, "OPTIONAL"));
    }
    participantMapper.insertBatch(participants);

    List<MeetingRoomSlotRecord> roomSlots = new ArrayList<>();
    for (TimeSlot slot : command.schedule().slots()) {
      roomSlots.add(
          new MeetingRoomSlotRecord(
              meetingId,
              command.roomId(),
              slot.bookingDate(),
              slot.slotIndex(),
              slot.startAt(),
              slot.endAt()));
    }
    roomSlotMapper.insertBatch(roomSlots);

    List<EmployeeBusySlotRecord> busySlots = new ArrayList<>();
    for (Long employeeId : command.requiredParticipantIds()) {
      for (TimeSlot slot : command.schedule().slots()) {
        busySlots.add(
            new EmployeeBusySlotRecord(
                meetingId,
                employeeId,
                slot.bookingDate(),
                slot.slotIndex(),
                slot.startAt(),
                slot.endAt()));
      }
    }
    busySlotMapper.insertBatch(busySlots);
  }

  private MeetingRecord findLocked(long meetingId) {
    return meetingMapper
        .findByIdForUpdate(meetingId)
        .orElseThrow(() -> new BusinessException(ErrorCode.MEETING_NOT_FOUND));
  }

  private void assertManagePermission(MeetingRecord meeting, AuthenticatedUser actor) {
    if (actor.roles().contains("ADMIN") || meeting.getOrganizerId() == actor.userId()) {
      return;
    }
    if (participantMapper.countParticipant(meeting.getId(), actor.userId()) > 0) {
      throw new BusinessException(ErrorCode.FORBIDDEN);
    }
    throw new BusinessException(ErrorCode.MEETING_NOT_FOUND);
  }
}
