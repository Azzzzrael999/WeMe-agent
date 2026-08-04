package com.example.meeting.mq;

import java.time.OffsetDateTime;
import java.util.List;

public record BookingCommandPayload(
    String requestNo,
    long userId,
    String title,
    String meetingType,
    long roomId,
    OffsetDateTime startAt,
    OffsetDateTime endAt,
    List<Long> requiredParticipantIds,
    List<Long> optionalParticipantIds) {

  public BookingCommandPayload {
    requiredParticipantIds = List.copyOf(requiredParticipantIds);
    optionalParticipantIds = List.copyOf(optionalParticipantIds);
  }
}
