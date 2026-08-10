package com.example.meeting.replan.api;

import com.example.meeting.meeting.api.MeetingView;
import java.time.OffsetDateTime;
import java.util.List;

public record ReplanCaseView(
    long id,
    String caseNo,
    long meetingId,
    long organizerId,
    String status,
    String failureReason,
    ReplanFailedRoomView failedRoom,
    int roomStatusVersion,
    OffsetDateTime originalStartAt,
    OffsetDateTime originalEndAt,
    MeetingView currentMeeting,
    List<String> changedConstraints,
    List<String> preservedConstraints,
    String resolutionType,
    Long resolvedRoomId,
    OffsetDateTime resolvedStartAt,
    OffsetDateTime resolvedEndAt,
    int version,
    OffsetDateTime createdAt,
    OffsetDateTime updatedAt,
    OffsetDateTime resolvedAt) {

  public ReplanCaseView {
    changedConstraints = List.copyOf(changedConstraints);
    preservedConstraints = List.copyOf(preservedConstraints);
  }
}
