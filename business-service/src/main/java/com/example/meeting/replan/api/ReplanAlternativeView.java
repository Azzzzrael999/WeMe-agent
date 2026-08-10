package com.example.meeting.replan.api;

import com.example.meeting.room.api.RoomFeatureView;
import java.util.List;

public record ReplanAlternativeView(
    long roomId,
    String roomCode,
    String roomName,
    String building,
    String floor,
    int capacity,
    List<RoomFeatureView> features,
    String reason) {

  public ReplanAlternativeView {
    features = List.copyOf(features);
  }
}
