package com.example.meeting.mq;

import org.springframework.boot.actuate.health.Health;
import org.springframework.boot.actuate.health.HealthIndicator;
import org.springframework.stereotype.Component;

@Component("rocketmqHealthIndicator")
public class RocketMqHealthIndicator implements HealthIndicator {

  private final RocketMqClientManager clientManager;

  public RocketMqHealthIndicator(RocketMqClientManager clientManager) {
    this.clientManager = clientManager;
  }

  @Override
  public Health health() {
    if (clientManager.isDisabled()) {
      return Health.up().withDetail("status", "disabled").build();
    }
    if (clientManager.isReady()) {
      return Health.up().withDetail("producer", "initialized").build();
    }
    return Health.down().withDetail("producer", "not-initialized").build();
  }
}
