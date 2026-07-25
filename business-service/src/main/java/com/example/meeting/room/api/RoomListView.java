package com.example.meeting.room.api;

import java.util.List;

public record RoomListView(List<RoomItemView> items, int total) {

  public RoomListView {
    items = List.copyOf(items);
  }
}
