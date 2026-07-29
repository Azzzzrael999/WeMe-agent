package com.example.meeting.room.api;

import java.time.OffsetDateTime;
import java.util.List;

public record RoomAvailabilityView(
    long roomId,
    OffsetDateTime from,
    OffsetDateTime to,
    List<RoomAvailabilitySlotView> availableSlots) {

  public RoomAvailabilityView {
    availableSlots = List.copyOf(availableSlots);
  }
}
