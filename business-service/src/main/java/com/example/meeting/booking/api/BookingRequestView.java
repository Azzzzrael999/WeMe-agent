package com.example.meeting.booking.api;

import java.time.OffsetDateTime;

public record BookingRequestView(
    String requestNo,
    String status,
    Long meetingId,
    String errorCode,
    String errorMessage,
    OffsetDateTime createdAt,
    OffsetDateTime updatedAt) {}
