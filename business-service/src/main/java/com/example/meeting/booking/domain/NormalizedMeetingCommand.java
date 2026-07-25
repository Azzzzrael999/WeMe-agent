package com.example.meeting.booking.domain;

import java.util.List;

public record NormalizedMeetingCommand(
    String title,
    String meetingType,
    long roomId,
    MeetingSchedule schedule,
    List<Long> requiredParticipantIds,
    List<Long> optionalParticipantIds,
    boolean createVideoConference) {

  public NormalizedMeetingCommand {
    requiredParticipantIds = List.copyOf(requiredParticipantIds);
    optionalParticipantIds = List.copyOf(optionalParticipantIds);
  }

  public int participantCount() {
    return requiredParticipantIds.size() + optionalParticipantIds.size();
  }
}
