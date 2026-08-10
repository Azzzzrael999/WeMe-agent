package com.example.meeting.meeting.lifecycle.api;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

public record UpdateActionItemRequest(
    @NotBlank(message = "REQUIRED") @Pattern(regexp = "OPEN|IN_PROGRESS|DONE", message = "INVALID_STATUS") String status,
    @Min(value = 0, message = "INVALID_VERSION") int expectedVersion) {}
