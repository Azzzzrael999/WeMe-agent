package com.example.meeting.agentgateway.api;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

/** Natural-language continuation for a run paused at WAITING_USER_INPUT. */
public record AgentRunInputRequest(
    @NotBlank @Size(max = 4000) String message,
    @NotBlank @Size(max = 80) String clientRequestId,
    @NotNull @Min(1) Integer expectedRevision) {}
