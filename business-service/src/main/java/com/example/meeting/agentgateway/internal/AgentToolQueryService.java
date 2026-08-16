package com.example.meeting.agentgateway.internal;

import com.example.meeting.agentgateway.internal.AgentToolDtos.AvailableRoomView;
import com.example.meeting.agentgateway.internal.AgentToolDtos.BusySlotView;
import com.example.meeting.agentgateway.internal.AgentToolDtos.EmployeeFreeBusyView;
import com.example.meeting.agentgateway.internal.AgentToolDtos.FreeBusyRequest;
import com.example.meeting.agentgateway.internal.AgentToolDtos.FreeBusyResponse;
import com.example.meeting.agentgateway.internal.AgentToolDtos.RecentMeetingRequest;
import com.example.meeting.agentgateway.internal.AgentToolDtos.RecentMeetingResponse;
import com.example.meeting.agentgateway.internal.AgentToolDtos.ResolveEmployeesRequest;
import com.example.meeting.agentgateway.internal.AgentToolDtos.ResolveEmployeesResponse;
import com.example.meeting.agentgateway.internal.AgentToolDtos.ResolveParticipantScopeRequest;
import com.example.meeting.agentgateway.internal.AgentToolDtos.ResolveParticipantScopeResponse;
import com.example.meeting.agentgateway.internal.AgentToolDtos.ResolvedEmployeeView;
import com.example.meeting.agentgateway.internal.AgentToolDtos.SearchRoomsRequest;
import com.example.meeting.agentgateway.internal.AgentToolDtos.SearchRoomsResponse;
import com.example.meeting.auth.infrastructure.UserMapper;
import com.example.meeting.auth.infrastructure.UserProfileRow;
import com.example.meeting.booking.infrastructure.EmployeeBusySlotMapper;
import com.example.meeting.booking.infrastructure.MeetingRoomSlotMapper;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AgentToolContext;
import com.example.meeting.common.web.ApiErrorDetail;
import com.example.meeting.meeting.application.MeetingQueryService;
import com.example.meeting.organization.domain.Department;
import com.example.meeting.organization.infrastructure.DepartmentMapper;
import com.example.meeting.room.api.RoomItemView;
import com.example.meeting.room.application.RoomQueryService;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class AgentToolQueryService {

  private final UserMapper userMapper;
  private final DepartmentMapper departmentMapper;
  private final EmployeeBusySlotMapper busySlotMapper;
  private final MeetingRoomSlotMapper roomSlotMapper;
  private final RoomQueryService roomQueryService;
  private final MeetingQueryService meetingQueryService;
  private final ToolTimeWindowValidator timeWindowValidator;
  private final ZoneId zoneId;

  public AgentToolQueryService(
      UserMapper userMapper,
      DepartmentMapper departmentMapper,
      EmployeeBusySlotMapper busySlotMapper,
      MeetingRoomSlotMapper roomSlotMapper,
      RoomQueryService roomQueryService,
      MeetingQueryService meetingQueryService,
      ToolTimeWindowValidator timeWindowValidator,
      @Value("${app.timezone}") String timezone) {
    this.userMapper = userMapper;
    this.departmentMapper = departmentMapper;
    this.busySlotMapper = busySlotMapper;
    this.roomSlotMapper = roomSlotMapper;
    this.roomQueryService = roomQueryService;
    this.meetingQueryService = meetingQueryService;
    this.timeWindowValidator = timeWindowValidator;
    this.zoneId = ZoneId.of(timezone);
  }

  @Transactional(readOnly = true)
  public ResolveEmployeesResponse resolveEmployees(ResolveEmployeesRequest request) {
    List<String> names = normalizeText(request.names());
    List<String> departments = normalizeText(request.departmentNames());
    if (names.isEmpty() && departments.isEmpty()) {
      throw validation("names", "AT_LEAST_ONE_QUERY_REQUIRED");
    }
    List<ResolvedEmployeeRow> rows = userMapper.resolveEmployees(names, departments, 50);
    Set<String> resolvedNames = new LinkedHashSet<>();
    List<ResolvedEmployeeView> employees = new ArrayList<>();
    for (ResolvedEmployeeRow row : rows) {
      resolvedNames.add(row.getDisplayName());
      resolvedNames.add(row.getUsername());
      employees.add(
          new ResolvedEmployeeView(
              row.getEmployeeId(),
              row.getUsername(),
              row.getDisplayName(),
              row.getDepartmentId(),
              row.getDepartmentName(),
              row.getStatus()));
    }
    List<String> unresolved = names.stream().filter(name -> !resolvedNames.contains(name)).toList();
    return new ResolveEmployeesResponse(employees, unresolved);
  }

  @Transactional(readOnly = true)
  public ResolveParticipantScopeResponse resolveParticipantScope(
      ResolveParticipantScopeRequest request, AgentToolContext context) {
    if (!"MY_DEPARTMENT".equals(request.scope())) {
      throw validation("scope", "PARTICIPANT_SCOPE_UNSUPPORTED");
    }
    UserProfileRow current =
        userMapper
            .findProfileById(context.userId())
            .filter(profile -> "ACTIVE".equals(profile.getStatus()))
            .orElseThrow(() -> validation("scope", "CURRENT_USER_NOT_ACTIVE"));
    if (current.getDepartmentId() == null || current.getDepartmentName() == null) {
      throw validation("scope", "CURRENT_USER_DEPARTMENT_MISSING");
    }
    Department department = departmentMapper.selectById(current.getDepartmentId());
    if (department == null || !"ACTIVE".equals(department.getStatus())) {
      throw validation("scope", "CURRENT_USER_DEPARTMENT_INACTIVE");
    }
    List<ResolvedEmployeeRow> rows =
        userMapper.resolveEmployees(List.of(), List.of(current.getDepartmentName()), 51);
    if (rows.isEmpty()) {
      throw validation("scope", "PARTICIPANT_SCOPE_EMPTY");
    }
    if (rows.size() > 50) {
      throw validation("scope", "PARTICIPANT_SCOPE_TOO_LARGE");
    }
    List<ResolvedEmployeeView> members =
        rows.stream()
            .map(
                row ->
                    new ResolvedEmployeeView(
                        row.getEmployeeId(),
                        row.getUsername(),
                        row.getDisplayName(),
                        row.getDepartmentId(),
                        row.getDepartmentName(),
                        row.getStatus()))
            .toList();
    return new ResolveParticipantScopeResponse(
        request.scope(), current.getDepartmentName(), members);
  }

  @Transactional(readOnly = true)
  public FreeBusyResponse getFreeBusy(FreeBusyRequest request, AgentToolContext context) {
    Long excludeMeetingId = validateExcludedMeeting(request.excludeMeetingId(), context);
    List<Long> employeeIds = new ArrayList<>(new LinkedHashSet<>(request.employeeIds()));
    if (userMapper.findActiveIds(employeeIds).size() != employeeIds.size()) {
      throw validation("employeeIds", "EMPLOYEE_NOT_ACTIVE");
    }
    ToolTimeWindowValidator.Window window =
        timeWindowValidator.validate(request.from(), request.to());
    Map<Long, List<BusySlotView>> grouped = new LinkedHashMap<>();
    employeeIds.forEach(id -> grouped.put(id, new ArrayList<>()));
    for (EmployeeBusySlotViewRow row :
        busySlotMapper.findBusySlots(employeeIds, window.from(), window.to(), excludeMeetingId)) {
      grouped
          .get(row.getEmployeeId())
          .add(
              new BusySlotView(
                  row.getMeetingId(), offset(row.getStartAt()), offset(row.getEndAt())));
    }
    List<EmployeeFreeBusyView> employees =
        grouped.entrySet().stream()
            .map(entry -> new EmployeeFreeBusyView(entry.getKey(), entry.getValue()))
            .toList();
    return new FreeBusyResponse(employees);
  }

  @Transactional(readOnly = true)
  public SearchRoomsResponse searchAvailableRooms(
      SearchRoomsRequest request, AgentToolContext context) {
    Long excludeMeetingId = validateExcludedMeeting(request.excludeMeetingId(), context);
    ToolTimeWindowValidator.Window window =
        timeWindowValidator.validate(request.from(), request.to());
    Set<String> requiredFeatures = new LinkedHashSet<>(normalizeText(request.requiredFeatures()));
    List<RoomItemView> candidates =
        roomQueryService.findActiveRooms().items().stream()
            .filter(room -> room.capacity() >= request.minimumCapacity())
            .filter(
                room ->
                    room.features().stream()
                        .map(feature -> feature.code())
                        .collect(java.util.stream.Collectors.toSet())
                        .containsAll(requiredFeatures))
            .toList();
    if (candidates.isEmpty()) {
      return new SearchRoomsResponse(List.of());
    }
    List<Long> roomIds = candidates.stream().map(RoomItemView::id).toList();
    Map<Long, List<BusySlotView>> busySlotsByRoom = new LinkedHashMap<>();
    roomIds.forEach(id -> busySlotsByRoom.put(id, new ArrayList<>()));
    for (MeetingRoomBusySlotViewRow row :
        roomSlotMapper.findBusyRoomSlots(roomIds, window.from(), window.to(), excludeMeetingId)) {
      busySlotsByRoom
          .get(row.getRoomId())
          .add(
              new BusySlotView(
                  row.getMeetingId(), offset(row.getStartAt()), offset(row.getEndAt())));
    }
    List<AvailableRoomView> rooms =
        candidates.stream()
            .limit(request.limit())
            .map(room -> toAvailableRoom(room, busySlotsByRoom.get(room.id())))
            .toList();
    return new SearchRoomsResponse(rooms);
  }

  @Transactional(readOnly = true)
  public RecentMeetingResponse recentMeetings(
      RecentMeetingRequest request, AgentToolContext context) {
    List<com.example.meeting.meeting.api.MeetingView> meetings =
        meetingQueryService
            .list(context.authenticatedUser(), null, null, "CONFIRMED", 1, 100)
            .items()
            .stream()
            .sorted(
                java.util.Comparator.comparing(
                        com.example.meeting.meeting.api.MeetingView::updatedAt)
                    .reversed()
                    .thenComparing(
                        com.example.meeting.meeting.api.MeetingView::id,
                        java.util.Comparator.reverseOrder()))
            .limit(request.limit())
            .toList();
    Map<Long, List<String>> featuresByRoom =
        roomQueryService.findAllRoomsForTrustedFacts().items().stream()
            .collect(
                java.util.stream.Collectors.toMap(
                    RoomItemView::id,
                    room -> room.features().stream().map(feature -> feature.code()).toList()));
    Map<Long, List<String>> featuresByMeeting = new LinkedHashMap<>();
    for (var meeting : meetings) {
      featuresByMeeting.put(meeting.id(), featuresByRoom.getOrDefault(meeting.roomId(), List.of()));
    }
    return new RecentMeetingResponse(meetings, featuresByMeeting);
  }

  private AvailableRoomView toAvailableRoom(RoomItemView room, List<BusySlotView> busySlots) {
    return new AvailableRoomView(
        room.id(),
        room.code(),
        room.name(),
        room.building(),
        room.floor(),
        room.capacity(),
        room.roomType(),
        room.hot(),
        room.features().stream().map(feature -> feature.code()).toList(),
        busySlots);
  }

  private List<String> normalizeText(List<String> values) {
    return values.stream().map(String::trim).filter(value -> !value.isEmpty()).distinct().toList();
  }

  private OffsetDateTime offset(LocalDateTime value) {
    return value.atZone(zoneId).toOffsetDateTime();
  }

  private Long validateExcludedMeeting(Long meetingId, AgentToolContext context) {
    if (meetingId == null) {
      return null;
    }
    var meeting =
        meetingQueryService.findManageableSnapshot(meetingId, context.authenticatedUser());
    if (!"CONFIRMED".equals(meeting.getStatus())) {
      throw validation("excludeMeetingId", "MEETING_NOT_CONFIRMED");
    }
    return meetingId;
  }

  private BusinessException validation(String field, String reason) {
    return new BusinessException(
        ErrorCode.VALIDATION_ERROR, "Tool 参数不符合要求", List.of(new ApiErrorDetail(field, reason)));
  }
}
