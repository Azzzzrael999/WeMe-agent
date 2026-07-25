package com.example.meeting.room.api;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.List;

public record RoomItemView(
    Long id,
    String code,
    String name,
    String building,
    String floor,
    int capacity,
    String roomType,
    @JsonProperty("isHot") boolean hot,
    String status,
    List<RoomFeatureView> features) {

  public RoomItemView {
    features = List.copyOf(features);
  }
}
