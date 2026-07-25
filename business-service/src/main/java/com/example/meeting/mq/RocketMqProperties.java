package com.example.meeting.mq;

import jakarta.validation.constraints.NotBlank;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

@Validated
@ConfigurationProperties(prefix = "app.rocketmq")
public record RocketMqProperties(
    boolean enabled,
    @NotBlank String nameServer,
    @NotBlank String bookingTopic,
    @NotBlank String domainTopic,
    @NotBlank String bookingConsumerGroup,
    @NotBlank String resultConsumerGroup) {}
