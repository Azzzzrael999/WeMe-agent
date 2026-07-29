package com.example.meeting.room.application;

import com.example.meeting.booking.domain.TimeSlotCalculator;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.web.ApiErrorDetail;
import com.example.meeting.room.api.RoomAvailabilitySlotView;
import com.example.meeting.room.api.RoomAvailabilityView;
import com.example.meeting.room.api.RoomItemView;
import com.example.meeting.room.infrastructure.MeetingRoomMapper;
import com.example.meeting.room.infrastructure.RoomOccupiedSlotRow;
import java.time.Duration;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class RoomAvailabilityService {

  private static final Duration SLOT_DURATION = Duration.ofMinutes(30);
  private static final Duration MAX_WINDOW = Duration.ofDays(14);

  private final RoomQueryService roomQueryService;
  private final MeetingRoomMapper meetingRoomMapper;
  private final TimeSlotCalculator timeSlotCalculator;

  public RoomAvailabilityService(
      RoomQueryService roomQueryService,
      MeetingRoomMapper meetingRoomMapper,
      TimeSlotCalculator timeSlotCalculator) {
    this.roomQueryService = roomQueryService;
    this.meetingRoomMapper = meetingRoomMapper;
    this.timeSlotCalculator = timeSlotCalculator;
  }

  @Transactional(readOnly = true)
  public RoomAvailabilityView findAvailability(
      long roomId, OffsetDateTime rawFrom, OffsetDateTime rawTo, AuthenticatedUser actor) {
    RoomItemView room = roomQueryService.findVisibleRoom(roomId, actor);
    Window window = validateWindow(rawFrom, rawTo);
    Set<LocalDateTime> occupiedStarts =
        "ACTIVE".equals(room.status())
            ? occupiedStarts(roomId, window.from(), window.to())
            : Set.of();

    List<RoomAvailabilitySlotView> slots =
        slots(window.from(), window.to(), "ACTIVE".equals(room.status()), occupiedStarts);
    return new RoomAvailabilityView(roomId, window.from(), window.to(), slots);
  }

  private Set<LocalDateTime> occupiedStarts(long roomId, OffsetDateTime from, OffsetDateTime to) {
    Set<LocalDateTime> starts = new HashSet<>();
    for (RoomOccupiedSlotRow slot :
        meetingRoomMapper.findOccupiedSlots(roomId, from.toLocalDateTime(), to.toLocalDateTime())) {
      starts.add(slot.getStartAt());
    }
    return starts;
  }

  private List<RoomAvailabilitySlotView> slots(
      OffsetDateTime from,
      OffsetDateTime to,
      boolean roomActive,
      Set<LocalDateTime> occupiedStarts) {
    java.util.ArrayList<RoomAvailabilitySlotView> slots = new java.util.ArrayList<>();
    OffsetDateTime cursor = from;
    while (cursor.isBefore(to)) {
      OffsetDateTime end = cursor.plus(SLOT_DURATION);
      slots.add(
          new RoomAvailabilitySlotView(
              cursor, end, roomActive && !occupiedStarts.contains(cursor.toLocalDateTime())));
      cursor = end;
    }
    return slots;
  }

  private Window validateWindow(OffsetDateTime rawFrom, OffsetDateTime rawTo) {
    OffsetDateTime from = validateTime(rawFrom, "from");
    OffsetDateTime to = validateTime(rawTo, "to");
    if (!to.isAfter(from)) {
      throw validation("to", "INVALID_TIME_RANGE", "to 必须晚于 from");
    }
    if (Duration.between(from, to).compareTo(MAX_WINDOW) > 0) {
      throw validation("to", "QUERY_WINDOW_TOO_LARGE", "查询时间窗口不能超过 14 天");
    }
    return new Window(from, to);
  }

  private OffsetDateTime validateTime(OffsetDateTime rawValue, String field) {
    if (rawValue == null) {
      throw validation(field, "REQUIRED", field + " 不能为空");
    }
    OffsetDateTime value = timeSlotCalculator.normalizeQueryTime(rawValue, field);
    boolean validMinute = value.getMinute() == 0 || value.getMinute() == 30;
    if (!validMinute || value.getSecond() != 0 || value.getNano() != 0) {
      throw validation(field, "INVALID_SLOT_BOUNDARY", field + " 必须落在整点或半点");
    }
    return value;
  }

  private BusinessException validation(String field, String reason, String message) {
    return new BusinessException(
        ErrorCode.VALIDATION_ERROR, message, List.of(new ApiErrorDetail(field, reason)));
  }

  private record Window(OffsetDateTime from, OffsetDateTime to) {}
}
