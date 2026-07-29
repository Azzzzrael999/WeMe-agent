package com.example.meeting.room.api;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;

public record UpdateRoomStatusRequest(
    @NotNull @Pattern(regexp = "ACTIVE|INACTIVE") String status,
    @NotNull @Min(0) Integer expectedVersion) {}
