package com.example.meeting.room.api;

import java.time.OffsetDateTime;

public record RoomAvailabilitySlotView(
    OffsetDateTime startAt, OffsetDateTime endAt, boolean available) {}
