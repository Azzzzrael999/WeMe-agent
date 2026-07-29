package com.example.meeting.room.infrastructure;

import java.time.LocalDateTime;

public class RoomOccupiedSlotRow {

  private LocalDateTime startAt;
  private LocalDateTime endAt;

  public LocalDateTime getStartAt() {
    return startAt;
  }

  public void setStartAt(LocalDateTime startAt) {
    this.startAt = startAt;
  }

  public LocalDateTime getEndAt() {
    return endAt;
  }

  public void setEndAt(LocalDateTime endAt) {
    this.endAt = endAt;
  }
}
