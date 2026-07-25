package com.example.meeting.outbox;

import com.example.meeting.mq.MqMessagePublisher;
import com.example.meeting.mq.RocketMqProperties;
import java.time.Clock;
import java.time.LocalDateTime;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class OutboxPublisher {

  private static final Logger LOGGER = LoggerFactory.getLogger(OutboxPublisher.class);
  private static final int BATCH_SIZE = 50;
  private static final long SENDING_LEASE_SECONDS = 30;

  private final MessageOutboxMapper mapper;
  private final MqMessagePublisher messagePublisher;
  private final OutboxProperties properties;
  private final RocketMqProperties rocketMqProperties;
  private final Clock clock;

  public OutboxPublisher(
      MessageOutboxMapper mapper,
      MqMessagePublisher messagePublisher,
      OutboxProperties properties,
      RocketMqProperties rocketMqProperties,
      Clock clock) {
    this.mapper = mapper;
    this.messagePublisher = messagePublisher;
    this.properties = properties;
    this.rocketMqProperties = rocketMqProperties;
    this.clock = clock;
  }

  @Scheduled(fixedDelayString = "${app.outbox.publish-interval-millis}")
  public void publishReady() {
    if (!rocketMqProperties.enabled()) {
      return;
    }
    LocalDateTime now = LocalDateTime.now(clock);
    for (MessageOutboxRecord record : mapper.findReady(now, BATCH_SIZE)) {
      if (mapper.claim(record.getId(), now, now.plusSeconds(SENDING_LEASE_SECONDS)) != 1) {
        continue;
      }
      try {
        messagePublisher.publish(record);
        mapper.markSent(record.getId(), LocalDateTime.now(clock));
      } catch (RuntimeException exception) {
        int attempt = record.getRetryCount() + 1;
        String status = attempt >= properties.maxRetries() ? "DEAD" : "RETRY";
        long delaySeconds = Math.min(60, 1L << Math.min(attempt, 6));
        mapper.markFailed(
            record.getId(), status, LocalDateTime.now(clock).plusSeconds(delaySeconds));
        LOGGER.warn(
            "Outbox publish failed eventId={} attempt={} status={} cause={}",
            record.getEventId(),
            attempt,
            status,
            exception.getClass().getSimpleName());
      }
    }
  }
}
