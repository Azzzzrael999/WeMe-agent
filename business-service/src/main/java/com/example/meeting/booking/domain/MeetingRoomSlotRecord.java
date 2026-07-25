package com.example.meeting.booking.domain;

import java.time.LocalDate;
import java.time.LocalDateTime;

public record MeetingRoomSlotRecord(
    long meetingId,
    long roomId,
    LocalDate bookingDate,
    short slotIndex,
    LocalDateTime startAt,
    LocalDateTime endAt) {}
