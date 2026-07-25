package com.example.meeting.meeting.application;

import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.meeting.api.MeetingListView;
import com.example.meeting.meeting.api.MeetingParticipantView;
import com.example.meeting.meeting.api.MeetingView;
import com.example.meeting.meeting.domain.MeetingRecord;
import com.example.meeting.meeting.infrastructure.MeetingMapper;
import com.example.meeting.meeting.infrastructure.MeetingParticipantMapper;
import com.example.meeting.meeting.infrastructure.MeetingParticipantViewRow;
import com.example.meeting.meeting.infrastructure.MeetingViewRow;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.List;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class MeetingQueryService {

  private final MeetingMapper meetingMapper;
  private final MeetingParticipantMapper participantMapper;
  private final ZoneId zoneId;

  public MeetingQueryService(
      MeetingMapper meetingMapper,
      MeetingParticipantMapper participantMapper,
      @Value("${app.timezone}") String timezone) {
    this.meetingMapper = meetingMapper;
    this.participantMapper = participantMapper;
    this.zoneId = ZoneId.of(timezone);
  }

  @Transactional(readOnly = true)
  public MeetingView getVisible(long meetingId, AuthenticatedUser actor) {
    MeetingViewRow row =
        meetingMapper
            .findViewById(meetingId)
            .orElseThrow(() -> new BusinessException(ErrorCode.MEETING_NOT_FOUND));
    boolean visible =
        actor.roles().contains("ADMIN")
            || row.getOrganizerId() == actor.userId()
            || participantMapper.countParticipant(meetingId, actor.userId()) > 0;
    if (!visible) {
      throw new BusinessException(ErrorCode.MEETING_NOT_FOUND);
    }
    return toView(row);
  }

  @Transactional(readOnly = true)
  public MeetingListView list(
      AuthenticatedUser actor,
      LocalDateTime from,
      LocalDateTime to,
      String status,
      int page,
      int size) {
    boolean admin = actor.roles().contains("ADMIN");
    long total = meetingMapper.countVisibleMeetings(actor.userId(), admin, from, to, status);
    if (total == 0) {
      return new MeetingListView(List.of(), 0);
    }
    long offset = (long) (page - 1) * size;
    List<MeetingView> items =
        meetingMapper
            .findVisibleMeetings(actor.userId(), admin, from, to, status, size, offset)
            .stream()
            .map(this::toView)
            .toList();
    return new MeetingListView(items, total);
  }

  @Transactional(readOnly = true)
  public MeetingRecord findManageableSnapshot(long meetingId, AuthenticatedUser actor) {
    MeetingRecord meeting = meetingMapper.selectById(meetingId);
    if (meeting == null) {
      throw new BusinessException(ErrorCode.MEETING_NOT_FOUND);
    }
    if (actor.roles().contains("ADMIN") || meeting.getOrganizerId() == actor.userId()) {
      return meeting;
    }
    if (participantMapper.countParticipant(meetingId, actor.userId()) > 0) {
      throw new BusinessException(ErrorCode.FORBIDDEN);
    }
    throw new BusinessException(ErrorCode.MEETING_NOT_FOUND);
  }

  private MeetingView toView(MeetingViewRow row) {
    List<MeetingParticipantView> participants =
        participantMapper.findViewsByMeetingId(row.getId()).stream()
            .map(this::toParticipantView)
            .toList();
    return new MeetingView(
        row.getId(),
        row.getMeetingNo(),
        row.getTitle(),
        row.getMeetingType(),
        row.getOrganizerId(),
        row.getOrganizerName(),
        row.getRoomId(),
        row.getRoomCode(),
        row.getRoomName(),
        offset(row.getStartAt()),
        offset(row.getEndAt()),
        row.getStatus(),
        row.getSource(),
        participants,
        row.getVersion(),
        offset(row.getCreatedAt()),
        offset(row.getUpdatedAt()),
        offset(row.getCancelledAt()));
  }

  private MeetingParticipantView toParticipantView(MeetingParticipantViewRow row) {
    return new MeetingParticipantView(
        row.getEmployeeId(), row.getDisplayName(), row.getParticipantType());
  }

  private OffsetDateTime offset(LocalDateTime value) {
    return value == null ? null : value.atZone(zoneId).toOffsetDateTime();
  }
}
