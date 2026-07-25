package com.example.meeting.meeting.api;

import java.time.OffsetDateTime;
import java.util.List;

public record MeetingView(
    Long id,
    String meetingNo,
    String title,
    String meetingType,
    Long organizerId,
    String organizerName,
    Long roomId,
    String roomCode,
    String roomName,
    OffsetDateTime startAt,
    OffsetDateTime endAt,
    String status,
    String source,
    List<MeetingParticipantView> participants,
    int version,
    OffsetDateTime createdAt,
    OffsetDateTime updatedAt,
    OffsetDateTime cancelledAt) {

  public MeetingView {
    participants = List.copyOf(participants);
  }
}
