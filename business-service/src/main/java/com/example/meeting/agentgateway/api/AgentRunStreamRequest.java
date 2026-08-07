package com.example.meeting.agentgateway.api;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record AgentRunStreamRequest(
    @Size(max = 64) String threadId,
    @NotBlank @Size(max = 4000) String message,
    @NotBlank @Size(max = 80) String clientRequestId,
    @Size(max = 64) String baseRunId) {}
