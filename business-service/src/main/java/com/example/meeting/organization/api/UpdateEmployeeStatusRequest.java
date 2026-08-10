package com.example.meeting.organization.api;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

public record UpdateEmployeeStatusRequest(
    @NotBlank @Pattern(regexp = "ACTIVE|DISABLED") String status, @Min(0) int expectedVersion) {}
