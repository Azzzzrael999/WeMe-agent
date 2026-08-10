package com.example.meeting.notification.api;

import java.time.OffsetDateTime;

public record NotificationItemView(
    long id,
    String type,
    String title,
    String content,
    Long relatedMeetingId,
    Long relatedReplanCaseId,
    OffsetDateTime readAt,
    OffsetDateTime createdAt) {}
