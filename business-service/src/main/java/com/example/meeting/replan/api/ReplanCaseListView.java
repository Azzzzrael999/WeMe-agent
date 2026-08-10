package com.example.meeting.replan.api;

import java.util.List;

public record ReplanCaseListView(List<ReplanCaseView> items, long total) {

  public ReplanCaseListView {
    items = List.copyOf(items);
  }
}
