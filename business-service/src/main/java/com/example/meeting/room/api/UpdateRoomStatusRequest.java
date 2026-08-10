package com.example.meeting.room.api;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

public record UpdateRoomStatusRequest(
    @NotNull @Pattern(regexp = "ACTIVE|INACTIVE") String status,
    @NotNull @Min(0) Integer expectedVersion,
    @Size(max = 200, message = "TOO_LONG") String reason) {}
