package com.example.meeting.auth.api;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record LoginRequest(
    @NotBlank(message = "REQUIRED") @Size(max = 64, message = "TOO_LONG") String username,
    @NotBlank(message = "REQUIRED") @Size(max = 128, message = "TOO_LONG") String password) {}
