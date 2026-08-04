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
import com.example.meeting.agentgateway.internal.AgentToolDtos.ResolvedEmployeeView;
import com.example.meeting.agentgateway.internal.AgentToolDtos.SearchRoomsRequest;
import com.example.meeting.agentgateway.internal.AgentToolDtos.SearchRoomsResponse;
import com.example.meeting.auth.infrastructure.UserMapper;
import com.example.meeting.booking.infrastructure.EmployeeBusySlotMapper;
import com.example.meeting.booking.infrastructure.MeetingRoomSlotMapper;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AgentToolContext;
import com.example.meeting.common.web.ApiErrorDetail;
import com.example.meeting.meeting.application.MeetingQueryService;
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
  private final EmployeeBusySlotMapper busySlotMapper;
  private final MeetingRoomSlotMapper roomSlotMapper;
  private final RoomQueryService roomQueryService;
  private final MeetingQueryService meetingQueryService;
  private final ToolTimeWindowValidator timeWindowValidator;
  private final ZoneId zoneId;

  public AgentToolQueryService(
      UserMapper userMapper,
      EmployeeBusySlotMapper busySlotMapper,
      MeetingRoomSlotMapper roomSlotMapper,
      RoomQueryService roomQueryService,
      MeetingQueryService meetingQueryService,
      ToolTimeWindowValidator timeWindowValidator,
      @Value("${app.timezone}") String timezone) {
    this.userMapper = userMapper;
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
  public FreeBusyResponse getFreeBusy(FreeBusyRequest request) {
    List<Long> employeeIds = new ArrayList<>(new LinkedHashSet<>(request.employeeIds()));
    if (userMapper.findActiveIds(employeeIds).size() != employeeIds.size()) {
      throw validation("employeeIds", "EMPLOYEE_NOT_ACTIVE");
    }
    ToolTimeWindowValidator.Window window =
        timeWindowValidator.validate(request.from(), request.to());
    Map<Long, List<BusySlotView>> grouped = new LinkedHashMap<>();
    employeeIds.forEach(id -> grouped.put(id, new ArrayList<>()));
    for (EmployeeBusySlotViewRow row :
        busySlotMapper.findBusySlots(employeeIds, window.from(), window.to())) {
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
  public SearchRoomsResponse searchAvailableRooms(SearchRoomsRequest request) {
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
    Set<Long> busyRooms =
        new LinkedHashSet<>(roomSlotMapper.findBusyRoomIds(roomIds, window.from(), window.to()));
    List<AvailableRoomView> rooms =
        candidates.stream()
            .filter(room -> !busyRooms.contains(room.id()))
            .limit(request.limit())
            .map(this::toAvailableRoom)
            .toList();
    return new SearchRoomsResponse(rooms);
  }

  @Transactional(readOnly = true)
  public RecentMeetingResponse recentMeetings(
      RecentMeetingRequest request, AgentToolContext context) {
    return new RecentMeetingResponse(
        meetingQueryService
            .list(context.authenticatedUser(), null, null, "CONFIRMED", 1, request.limit())
            .items());
  }

  private AvailableRoomView toAvailableRoom(RoomItemView room) {
    return new AvailableRoomView(
        room.id(),
        room.code(),
        room.name(),
        room.building(),
        room.floor(),
        room.capacity(),
        room.roomType(),
        room.hot(),
        room.features().stream().map(feature -> feature.code()).toList());
  }

  private List<String> normalizeText(List<String> values) {
    return values.stream().map(String::trim).filter(value -> !value.isEmpty()).distinct().toList();
  }

  private OffsetDateTime offset(LocalDateTime value) {
    return value.atZone(zoneId).toOffsetDateTime();
  }

  private BusinessException validation(String field, String reason) {
    return new BusinessException(
        ErrorCode.VALIDATION_ERROR, "Tool 参数不符合要求", List.of(new ApiErrorDetail(field, reason)));
  }
}
