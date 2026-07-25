package com.example.meeting.agentgateway.client;

import jakarta.validation.constraints.NotBlank;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

@Validated
@ConfigurationProperties(prefix = "app.agent-service")
public record AgentServiceProperties(@NotBlank String url, boolean callbackEnabled) {}
