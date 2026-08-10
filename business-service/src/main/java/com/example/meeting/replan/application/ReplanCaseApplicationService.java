package com.example.meeting.replan.application;

import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.json.StoredJson;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.web.ApiErrorDetail;
import com.example.meeting.meeting.api.MeetingView;
import com.example.meeting.meeting.application.MeetingApplicationService;
import com.example.meeting.meeting.application.MeetingQueryService;
import com.example.meeting.replan.api.ReplanAlternativeView;
import com.example.meeting.replan.api.ReplanAlternativesView;
import com.example.meeting.replan.api.ReplanCaseListView;
import com.example.meeting.replan.api.ReplanCaseView;
import com.example.meeting.replan.api.ReplanFailedRoomView;
import com.example.meeting.replan.api.ResolveReplanCaseRequest;
import com.example.meeting.replan.domain.ReplanCaseRecord;
import com.example.meeting.replan.infrastructure.ReplanCaseMapper;
import com.example.meeting.room.api.RoomFeatureView;
import com.example.meeting.room.api.RoomItemView;
import com.example.meeting.room.application.RoomQueryService;
import com.example.meeting.room.infrastructure.MeetingRoomMapper;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Clock;
import java.time.Duration;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ReplanCaseApplicationService {

  private static final Set<String> STATUSES = Set.of("OPEN", "RESOLVED", "RESTORED", "CANCELLED");
  private static final List<String> QUICK_CHANGED = List.of("仅会议室改变");
  private static final List<String> QUICK_PRESERVED =
      List.of("会议时间保持不变", "会议时长保持不变", "参会人员保持不变", "原会议室设备能力完整保留");

  private final ReplanCaseMapper caseMapper;
  private final MeetingQueryService meetingQueryService;
  private final MeetingApplicationService meetingApplicationService;
  private final RoomQueryService roomQueryService;
  private final MeetingRoomMapper roomMapper;
  private final ObjectMapper objectMapper;
  private final Clock clock;

  public ReplanCaseApplicationService(
      ReplanCaseMapper caseMapper,
      MeetingQueryService meetingQueryService,
      MeetingApplicationService meetingApplicationService,
      RoomQueryService roomQueryService,
      MeetingRoomMapper roomMapper,
      ObjectMapper objectMapper,
      Clock clock) {
    this.caseMapper = caseMapper;
    this.meetingQueryService = meetingQueryService;
    this.meetingApplicationService = meetingApplicationService;
    this.roomQueryService = roomQueryService;
    this.roomMapper = roomMapper;
    this.objectMapper = objectMapper;
    this.clock = clock;
  }

  @Transactional(readOnly = true)
  public ReplanCaseListView list(String rawStatus, int page, int size, AuthenticatedUser actor) {
    if (page < 1 || size < 1 || size > 100) {
      throw validation("page", "INVALID_PAGINATION", "分页参数超出允许范围");
    }
    String status = normalizeStatus(rawStatus);
    boolean admin = actor.roles().contains("ADMIN");
    long total = caseMapper.countVisible(actor.userId(), admin, status);
    if (total == 0) {
      return new ReplanCaseListView(List.of(), 0);
    }
    long offset = (long) (page - 1) * size;
    List<ReplanCaseView> items =
        caseMapper.findVisiblePage(actor.userId(), admin, status, size, offset).stream()
            .map(record -> view(record, actor))
            .toList();
    return new ReplanCaseListView(items, total);
  }

  @Transactional(readOnly = true)
  public ReplanCaseView get(long caseId, AuthenticatedUser actor) {
    return view(accessible(caseId, actor), actor);
  }

  @Transactional(readOnly = true)
  public ReplanAlternativesView alternatives(
      long caseId, int requestedLimit, AuthenticatedUser actor) {
    if (requestedLimit < 1 || requestedLimit > 3) {
      throw validation("limit", "INVALID_LIMIT", "limit 必须在 1 到 3 之间");
    }
    ReplanCaseRecord record = accessible(caseId, actor);
    if (!"OPEN".equals(record.getStatus())) {
      throw new BusinessException(ErrorCode.REPLAN_CASE_STATE_CONFLICT);
    }
    MeetingView meeting = meetingQueryService.getVisible(record.getMeetingId(), actor);
    if (!"CONFIRMED".equals(meeting.status())) {
      throw new BusinessException(ErrorCode.REPLAN_CASE_STATE_CONFLICT);
    }
    return alternatives(record, meeting, requestedLimit);
  }

  public ReplanCaseView resolve(
      long caseId, ResolveReplanCaseRequest request, AuthenticatedUser actor) {
    ReplanCaseRecord record = accessible(caseId, actor);
    if (!"OPEN".equals(record.getStatus())
        || record.getVersion() != request.expectedCaseVersion()) {
      throw new BusinessException(ErrorCode.REPLAN_CASE_STATE_CONFLICT);
    }
    MeetingView meeting = meetingQueryService.getVisible(record.getMeetingId(), actor);
    if (!"CONFIRMED".equals(meeting.status())
        || meeting.version() != request.expectedMeetingVersion()) {
      throw new BusinessException(ErrorCode.REPLAN_CASE_STATE_CONFLICT);
    }
    if (!eligibleRooms(record, meeting).stream()
        .anyMatch(room -> room.id().equals(request.roomId()))) {
      throw new BusinessException(ErrorCode.REPLAN_CANDIDATE_STALE);
    }
    try {
      meetingApplicationService.updateForReplan(
          meeting.id(),
          request.roomId(),
          request.expectedMeetingVersion(),
          caseId,
          request.expectedCaseVersion(),
          actor);
    } catch (BusinessException exception) {
      if (exception.errorCode() == ErrorCode.MEETING_STATE_CONFLICT) {
        throw new BusinessException(ErrorCode.REPLAN_CASE_STATE_CONFLICT);
      }
      throw exception;
    }
    return get(caseId, actor);
  }

  private ReplanAlternativesView alternatives(
      ReplanCaseRecord record, MeetingView meeting, int limit) {
    List<ReplanAlternativeView> items =
        eligibleRooms(record, meeting).stream()
            .limit(limit)
            .map(
                room ->
                    new ReplanAlternativeView(
                        room.id(),
                        room.code(),
                        room.name(),
                        room.building(),
                        room.floor(),
                        room.capacity(),
                        room.features(),
                        "原时段无占用，容量满足且完整保留原会议室设备能力"))
            .toList();
    return new ReplanAlternativesView(
        record.getId(),
        record.getVersion(),
        meeting.version(),
        true,
        QUICK_CHANGED,
        QUICK_PRESERVED,
        items);
  }

  private List<RoomItemView> eligibleRooms(ReplanCaseRecord record, MeetingView meeting) {
    ReplanConstraintSnapshot snapshot = snapshot(record);
    Set<String> requiredFeatures =
        snapshot.roomFeatures().stream()
            .map(RoomFeatureView::code)
            .collect(java.util.stream.Collectors.toSet());
    int participantCount = meeting.participants().size();
    String failedBuilding =
        roomMapper.findRoomWithFeaturesById(record.getFailedRoomId()).stream()
            .findFirst()
            .map(row -> row.getBuilding())
            .orElse("");
    return roomQueryService.findActiveRooms().items().stream()
        .filter(room -> !room.id().equals(record.getFailedRoomId()))
        .filter(room -> room.capacity() >= participantCount)
        .filter(
            room ->
                room.features().stream()
                    .map(RoomFeatureView::code)
                    .collect(java.util.stream.Collectors.toSet())
                    .containsAll(requiredFeatures))
        .filter(
            room ->
                roomMapper
                    .findOccupiedSlots(
                        room.id(),
                        meeting.startAt().toLocalDateTime(),
                        meeting.endAt().toLocalDateTime())
                    .isEmpty())
        .sorted(
            Comparator.comparing((RoomItemView room) -> !failedBuilding.equals(room.building()))
                .thenComparingInt(room -> room.capacity() - participantCount)
                .thenComparing(RoomItemView::code))
        .toList();
  }

  private ReplanCaseView view(ReplanCaseRecord record, AuthenticatedUser actor) {
    MeetingView meeting = meetingQueryService.getVisible(record.getMeetingId(), actor);
    ReplanConstraintSnapshot snapshot = snapshot(record);
    List<String> changed = new ArrayList<>();
    List<String> preserved = new ArrayList<>();
    if (meeting.roomId().equals(record.getFailedRoomId())) {
      changed.add("会议室“" + record.getFailedRoomName() + "”已失效");
    } else {
      changed.add("会议室已从“" + record.getFailedRoomName() + "”调整为“" + meeting.roomName() + "”");
    }
    if (sameTime(record, meeting)) {
      preserved.add("会议时间保持不变");
    } else {
      changed.add("会议时间已调整");
    }
    if (sameDuration(record, meeting)) {
      preserved.add("会议时长保持不变");
    } else {
      changed.add("会议时长已调整");
    }
    if (sameParticipants(snapshot, meeting)) {
      preserved.add("参会人员保持不变");
    } else {
      changed.add("参会人员已调整");
    }
    if (roomPreservesFeatures(snapshot, meeting.roomId())) {
      preserved.add("原会议室设备能力完整保留");
    } else {
      changed.add("设备约束已调整");
    }
    return new ReplanCaseView(
        record.getId(),
        record.getCaseNo(),
        record.getMeetingId(),
        record.getOrganizerId(),
        record.getStatus(),
        record.getFailureReason(),
        new ReplanFailedRoomView(record.getFailedRoomId(), record.getFailedRoomName()),
        record.getRoomStatusVersion(),
        offset(record.getOriginalStartAt()),
        offset(record.getOriginalEndAt()),
        meeting,
        changed,
        preserved,
        record.getResolutionType(),
        record.getResolvedRoomId(),
        offset(record.getResolvedStartAt()),
        offset(record.getResolvedEndAt()),
        record.getVersion(),
        offset(record.getCreatedAt()),
        offset(record.getUpdatedAt()),
        offset(record.getResolvedAt()));
  }

  private ReplanCaseRecord accessible(long caseId, AuthenticatedUser actor) {
    ReplanCaseRecord record =
        caseMapper
            .findById(caseId)
            .orElseThrow(() -> new BusinessException(ErrorCode.REPLAN_CASE_NOT_FOUND));
    if (!actor.roles().contains("ADMIN") && record.getOrganizerId() != actor.userId()) {
      throw new BusinessException(ErrorCode.REPLAN_CASE_NOT_FOUND);
    }
    return record;
  }

  private ReplanConstraintSnapshot snapshot(ReplanCaseRecord record) {
    try {
      return StoredJson.read(
          objectMapper, record.getConstraintSnapshot(), ReplanConstraintSnapshot.class);
    } catch (JsonProcessingException exception) {
      throw new IllegalStateException("Stored replan constraint snapshot is invalid", exception);
    }
  }

  private boolean sameTime(ReplanCaseRecord record, MeetingView meeting) {
    return offset(record.getOriginalStartAt()).equals(meeting.startAt())
        && offset(record.getOriginalEndAt()).equals(meeting.endAt());
  }

  private boolean sameDuration(ReplanCaseRecord record, MeetingView meeting) {
    return Duration.between(record.getOriginalStartAt(), record.getOriginalEndAt())
        .equals(Duration.between(meeting.startAt(), meeting.endAt()));
  }

  private boolean sameParticipants(ReplanConstraintSnapshot snapshot, MeetingView meeting) {
    Set<String> original = new HashSet<>();
    snapshot
        .participants()
        .forEach(
            participant ->
                original.add(participant.employeeId() + ":" + participant.participantType()));
    Set<String> current = new HashSet<>();
    meeting
        .participants()
        .forEach(
            participant ->
                current.add(participant.employeeId() + ":" + participant.participantType()));
    return original.equals(current);
  }

  private boolean roomPreservesFeatures(ReplanConstraintSnapshot snapshot, long roomId) {
    Set<String> current =
        roomMapper.findRoomWithFeaturesById(roomId).stream()
            .filter(row -> row.getFeatureCode() != null)
            .map(row -> row.getFeatureCode())
            .collect(java.util.stream.Collectors.toSet());
    return current.containsAll(
        snapshot.roomFeatures().stream().map(RoomFeatureView::code).toList());
  }

  private String normalizeStatus(String rawStatus) {
    if (rawStatus == null || rawStatus.isBlank()) {
      return null;
    }
    String status = rawStatus.trim().toUpperCase(Locale.ROOT);
    if (!STATUSES.contains(status)) {
      throw validation("status", "INVALID_REPLAN_STATUS", "status 不是有效的异常单状态");
    }
    return status;
  }

  private OffsetDateTime offset(LocalDateTime value) {
    return value == null ? null : value.atZone(clock.getZone()).toOffsetDateTime();
  }

  private BusinessException validation(String field, String reason, String message) {
    return new BusinessException(
        ErrorCode.VALIDATION_ERROR, message, List.of(new ApiErrorDetail(field, reason)));
  }
}
