package com.example.meeting.booking.domain;

import java.time.LocalDate;
import java.time.LocalDateTime;

public record TimeSlot(
    LocalDate bookingDate, short slotIndex, LocalDateTime startAt, LocalDateTime endAt) {}
