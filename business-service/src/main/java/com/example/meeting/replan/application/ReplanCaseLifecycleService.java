package com.example.meeting.replan.application;

import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.meeting.domain.MeetingRecord;
import com.example.meeting.meeting.infrastructure.MeetingMapper;
import com.example.meeting.meeting.infrastructure.MeetingParticipantMapper;
import com.example.meeting.meeting.infrastructure.MeetingParticipantViewRow;
import com.example.meeting.notification.NotificationMapper;
import com.example.meeting.notification.NotificationRecord;
import com.example.meeting.replan.domain.ReplanCaseRecord;
import com.example.meeting.replan.infrastructure.ReplanCaseMapper;
import com.example.meeting.room.api.RoomFeatureView;
import com.example.meeting.room.domain.MeetingRoom;
import com.example.meeting.room.infrastructure.MeetingRoomMapper;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Clock;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ReplanCaseLifecycleService {

  private static final DateTimeFormatter CASE_DATE = DateTimeFormatter.ofPattern("yyyyMMdd");

  private final ReplanCaseMapper replanCaseMapper;
  private final MeetingMapper meetingMapper;
  private final MeetingParticipantMapper participantMapper;
  private final MeetingRoomMapper roomMapper;
  private final NotificationMapper notificationMapper;
  private final ObjectMapper objectMapper;
  private final Clock clock;

  public ReplanCaseLifecycleService(
      ReplanCaseMapper replanCaseMapper,
      MeetingMapper meetingMapper,
      MeetingParticipantMapper participantMapper,
      MeetingRoomMapper roomMapper,
      NotificationMapper notificationMapper,
      ObjectMapper objectMapper,
      Clock clock) {
    this.replanCaseMapper = replanCaseMapper;
    this.meetingMapper = meetingMapper;
    this.participantMapper = participantMapper;
    this.roomMapper = roomMapper;
    this.notificationMapper = notificationMapper;
    this.objectMapper = objectMapper;
    this.clock = clock;
  }

  @Transactional(propagation = Propagation.MANDATORY)
  public void createForRoomFailure(
      MeetingRoom failedRoom, String failureReason, int roomStatusVersion) {
    LocalDateTime now = LocalDateTime.now(clock);
    List<RoomFeatureView> features = roomFeatures(failedRoom.getId());
    for (MeetingRecord meeting : meetingMapper.findFutureConfirmedByRoom(failedRoom.getId(), now)) {
      ReplanCaseRecord record = new ReplanCaseRecord();
      record.setCaseNo(nextCaseNo(now));
      record.setMeetingId(meeting.getId());
      record.setOrganizerId(meeting.getOrganizerId());
      record.setFailedRoomId(failedRoom.getId());
      record.setFailedRoomName(failedRoom.getName());
      record.setFailureReason(failureReason);
      record.setRoomStatusVersion(roomStatusVersion);
      record.setOriginalStartAt(meeting.getStartAt());
      record.setOriginalEndAt(meeting.getEndAt());
      record.setConstraintSnapshot(snapshot(meeting, features));
      record.setStatus("OPEN");
      record.setVersion(0);
      record.setCreatedAt(now);
      record.setUpdatedAt(now);
      try {
        replanCaseMapper.insert(record);
      } catch (DuplicateKeyException duplicate) {
        continue;
      }
      writeResourceNotification(
          meeting.getOrganizerId(),
          "RESOURCE_UNAVAILABLE",
          "会议室资源已失效",
          "会议“" + meeting.getTitle() + "”的原会议室已停用：" + failureReason,
          meeting.getId(),
          record.getId(),
          now);
    }
  }

  @Transactional(propagation = Propagation.MANDATORY)
  public void restoreForRoom(long roomId) {
    LocalDateTime now = LocalDateTime.now(clock);
    for (ReplanCaseRecord record : replanCaseMapper.findRestorableByRoom(roomId)) {
      if (replanCaseMapper.restore(record.getId(), now) == 1) {
        MeetingRecord meeting = meetingMapper.selectById(record.getMeetingId());
        writeResourceNotification(
            record.getOrganizerId(),
            "RESOURCE_RESTORED",
            "会议室资源已恢复",
            "会议“" + meeting.getTitle() + "”的原会议室已恢复可用。",
            record.getMeetingId(),
            record.getId(),
            now);
      }
    }
  }

  @Transactional(propagation = Propagation.MANDATORY)
  public void resolveAfterMeetingUpdate(
      long meetingId, long roomId, LocalDateTime startAt, LocalDateTime endAt) {
    replanCaseMapper.resolveOpenForMeeting(
        meetingId, "AGENT_RESCHEDULE", roomId, startAt, endAt, LocalDateTime.now(clock));
  }

  @Transactional(propagation = Propagation.MANDATORY)
  public void resolveQuick(
      long caseId,
      long meetingId,
      int expectedCaseVersion,
      long roomId,
      LocalDateTime startAt,
      LocalDateTime endAt) {
    if (replanCaseMapper.resolveQuick(
            caseId,
            meetingId,
            expectedCaseVersion,
            roomId,
            startAt,
            endAt,
            LocalDateTime.now(clock))
        != 1) {
      throw new BusinessException(ErrorCode.REPLAN_CASE_STATE_CONFLICT);
    }
  }

  @Transactional(propagation = Propagation.MANDATORY)
  public void cancelAfterMeetingCancellation(long meetingId) {
    replanCaseMapper.cancelOpenForMeeting(meetingId, LocalDateTime.now(clock));
  }

  private String snapshot(MeetingRecord meeting, List<RoomFeatureView> features) {
    List<MeetingParticipantViewRow> rows = participantMapper.findViewsByMeetingId(meeting.getId());
    ReplanConstraintSnapshot snapshot =
        new ReplanConstraintSnapshot(
            meeting.getTitle(),
            meeting.getMeetingType(),
            rows.stream()
                .map(
                    row ->
                        new com.example.meeting.meeting.api.MeetingParticipantView(
                            row.getEmployeeId(), row.getDisplayName(), row.getParticipantType()))
                .toList(),
            features);
    try {
      return objectMapper.writeValueAsString(snapshot);
    } catch (JsonProcessingException exception) {
      throw new IllegalStateException("Unable to serialize replan constraint snapshot", exception);
    }
  }

  private List<RoomFeatureView> roomFeatures(long roomId) {
    return roomMapper.findRoomWithFeaturesById(roomId).stream()
        .filter(row -> row.getFeatureCode() != null)
        .map(row -> new RoomFeatureView(row.getFeatureCode(), row.getFeatureName()))
        .toList();
  }

  private void writeResourceNotification(
      long organizerId,
      String type,
      String title,
      String content,
      long meetingId,
      long caseId,
      LocalDateTime now) {
    NotificationRecord notification = new NotificationRecord();
    notification.setUserId(organizerId);
    notification.setType(type);
    notification.setTitle(title);
    notification.setContent(content);
    notification.setRelatedMeetingId(meetingId);
    notification.setRelatedReplanCaseId(caseId);
    notification.setCreatedAt(now);
    notificationMapper.insert(notification);
  }

  private String nextCaseNo(LocalDateTime now) {
    return "RP-"
        + CASE_DATE.format(now)
        + "-"
        + UUID.randomUUID().toString().replace("-", "").substring(0, 12).toUpperCase(Locale.ROOT);
  }
}
