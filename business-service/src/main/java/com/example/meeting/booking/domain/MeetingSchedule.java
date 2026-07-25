package com.example.meeting.booking.domain;

import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.util.List;

public record MeetingSchedule(
    OffsetDateTime startAt,
    OffsetDateTime endAt,
    LocalDateTime localStartAt,
    LocalDateTime localEndAt,
    List<TimeSlot> slots) {

  public MeetingSchedule {
    slots = List.copyOf(slots);
  }
}
