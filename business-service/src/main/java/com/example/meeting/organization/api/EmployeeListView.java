package com.example.meeting.organization.api;

import java.util.List;

public record EmployeeListView(List<EmployeeItemView> items, long total) {
  public EmployeeListView {
    items = List.copyOf(items);
  }
}
