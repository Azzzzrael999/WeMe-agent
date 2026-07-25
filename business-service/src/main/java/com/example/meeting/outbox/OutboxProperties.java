package com.example.meeting.outbox;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

@Validated
@ConfigurationProperties(prefix = "app.outbox")
public record OutboxProperties(
    @Min(100) @Max(60000) long publishIntervalMillis, @Min(1) @Max(100) int maxRetries) {}
