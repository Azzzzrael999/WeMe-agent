package com.example.meeting.booking.domain;

import java.util.List;

public record SlotHoldReservation(List<String> keys, String token, boolean active) {

  public SlotHoldReservation {
    keys = List.copyOf(keys);
  }

  public static SlotHoldReservation degraded() {
    return new SlotHoldReservation(List.of(), "", false);
  }
}
