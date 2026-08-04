package com.example.meeting.booking.application;

import com.example.meeting.agentgateway.internal.AgentToolDtos.BookingDraftView;
import com.example.meeting.agentgateway.internal.AgentToolDtos.CreateDraftResponse;
import com.example.meeting.agentgateway.internal.AgentToolDtos.DraftParticipantView;
import com.example.meeting.auth.infrastructure.UserMapper;
import com.example.meeting.auth.infrastructure.UserProfileRow;
import com.example.meeting.booking.domain.BookingDraftRecord;
import com.example.meeting.booking.domain.NormalizedMeetingCommand;
import com.example.meeting.booking.infrastructure.BookingDraftMapper;
import com.example.meeting.common.security.AgentToolContext;
import com.example.meeting.meeting.api.CreateMeetingRequest;
import com.example.meeting.room.domain.MeetingRoom;
import com.example.meeting.room.infrastructure.MeetingRoomMapper;
import java.time.Clock;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.List;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class BookingDraftService {

  private final BookingDraftMapper draftMapper;
  private final MeetingCommandFactory commandFactory;
  private final BookingValidator bookingValidator;
  private final MeetingRequestHasher requestHasher;
  private final DraftPayloadCodec payloadCodec;
  private final MeetingRoomMapper roomMapper;
  private final UserMapper userMapper;
  private final BookingProperties properties;
  private final Clock clock;
  private final ZoneId zoneId;

  public BookingDraftService(
      BookingDraftMapper draftMapper,
      MeetingCommandFactory commandFactory,
      BookingValidator bookingValidator,
      MeetingRequestHasher requestHasher,
      DraftPayloadCodec payloadCodec,
      MeetingRoomMapper roomMapper,
      UserMapper userMapper,
      BookingProperties properties,
      Clock clock,
      @Value("${app.timezone}") String timezone) {
    this.draftMapper = draftMapper;
    this.commandFactory = commandFactory;
    this.bookingValidator = bookingValidator;
    this.requestHasher = requestHasher;
    this.payloadCodec = payloadCodec;
    this.roomMapper = roomMapper;
    this.userMapper = userMapper;
    this.properties = properties;
    this.clock = clock;
    this.zoneId = ZoneId.of(timezone);
  }

  @Transactional
  public CreateDraftResponse create(CreateMeetingRequest request, AgentToolContext context) {
    NormalizedMeetingCommand command = commandFactory.create(request, context.userId());
    bookingValidator.validate(command);
    CreateMeetingRequest payload = payloadCodec.fromCommand(command);
    LocalDateTime now = LocalDateTime.now(clock);
    LocalDateTime expiresAt = now.plusMinutes(properties.draftTtlMinutes());
    draftMapper.invalidatePendingForRun(context.userId(), context.runId(), "CREATE", now);
    BookingDraftRecord draft = new BookingDraftRecord();
    draft.setConfirmationToken("cfm_" + UUID.randomUUID().toString().replace("-", ""));
    draft.setUserId(context.userId());
    draft.setRunId(context.runId());
    draft.setToolCallId(context.toolCallId());
    draft.setOperation("CREATE");
    draft.setPayloadJson(payloadCodec.write(payload));
    draft.setPayloadHash(requestHasher.hash(command));
    draft.setStatus("PENDING");
    draft.setVersion(0);
    draft.setExpiresAt(expiresAt);
    draft.setCreatedAt(now);
    draftMapper.insert(draft);
    return new CreateDraftResponse(
        draft.getConfirmationToken(), expiresAt.atZone(zoneId).toOffsetDateTime(), toView(command));
  }

  BookingDraftView toView(NormalizedMeetingCommand command) {
    MeetingRoom room = roomMapper.selectById(command.roomId());
    return new BookingDraftView(
        command.title(),
        command.roomId(),
        room.getName(),
        command.schedule().startAt(),
        command.schedule().endAt(),
        participants(command.requiredParticipantIds()),
        participants(command.optionalParticipantIds()),
        false);
  }

  private List<DraftParticipantView> participants(List<Long> employeeIds) {
    return employeeIds.stream()
        .map(
            employeeId -> {
              UserProfileRow profile = userMapper.findProfileById(employeeId).orElseThrow();
              return new DraftParticipantView(employeeId, profile.getDisplayName());
            })
        .toList();
  }
}
