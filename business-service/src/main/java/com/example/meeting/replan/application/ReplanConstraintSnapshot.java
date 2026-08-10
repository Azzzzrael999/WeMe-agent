package com.example.meeting.replan.application;

import com.example.meeting.meeting.api.MeetingParticipantView;
import com.example.meeting.room.api.RoomFeatureView;
import java.util.List;

public record ReplanConstraintSnapshot(
    String title,
    String meetingType,
    List<MeetingParticipantView> participants,
    List<RoomFeatureView> roomFeatures) {

  public ReplanConstraintSnapshot {
    participants = List.copyOf(participants);
    roomFeatures = List.copyOf(roomFeatures);
  }
}
