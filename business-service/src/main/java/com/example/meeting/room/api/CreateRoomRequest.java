package com.example.meeting.room.api;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import java.util.List;

public record CreateRoomRequest(
    @NotBlank @Size(max = 32) String code,
    @NotBlank @Size(max = 64) String name,
    @NotBlank @Size(max = 64) String building,
    @NotBlank @Size(max = 32) String floor,
    @NotNull @Min(1) @Max(10000) Integer capacity,
    @NotBlank @Size(max = 32) String roomType,
    @NotNull Boolean isHot,
    @NotNull @Size(max = 50) List<@NotBlank @Size(max = 32) String> featureCodes) {}
