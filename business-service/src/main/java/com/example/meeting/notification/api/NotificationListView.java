package com.example.meeting.notification.api;

import java.util.List;

public record NotificationListView(List<NotificationItemView> items, long total, long unreadCount) {
  public NotificationListView {
    items = List.copyOf(items);
  }
}
