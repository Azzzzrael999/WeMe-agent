package com.example.meeting.organization.api;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record ResetEmployeePasswordRequest(
    @NotBlank @Size(min = 8, max = 72) String newPassword, @Min(0) int expectedVersion) {}
