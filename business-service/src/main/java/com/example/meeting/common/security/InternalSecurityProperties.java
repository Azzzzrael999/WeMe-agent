package com.example.meeting.common.security;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Positive;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

@Validated
@ConfigurationProperties(prefix = "app.internal")
public record InternalSecurityProperties(
    @NotBlank String serviceToken,
    @NotBlank String agentContextSecret,
    @NotBlank String agentContextAudience,
    @Positive long agentContextExpirationSeconds) {}
