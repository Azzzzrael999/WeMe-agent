package com.example.meeting.notification.api;

import java.time.OffsetDateTime;

public record ReadAllResultView(int updatedCount, OffsetDateTime readAt) {}
