package com.example.meeting.meeting.api;

import java.util.List;

public record MeetingListView(List<MeetingView> items, long total) {

  public MeetingListView {
    items = List.copyOf(items);
  }
}
