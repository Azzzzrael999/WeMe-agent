package com.example.meeting.booking.application;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Positive;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

@Validated
@ConfigurationProperties(prefix = "app.booking")
public record BookingProperties(
    @Min(1000) @Max(120000) long holdTtlMillis,
    @Positive @Max(720) long idempotencyTtlHours,
    boolean redisHoldEnabled,
    @Positive @Max(1440) long draftTtlMinutes,
    boolean hotBookingEnabled) {}
