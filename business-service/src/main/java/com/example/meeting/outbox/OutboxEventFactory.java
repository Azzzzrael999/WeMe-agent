package com.example.meeting.outbox;

import com.example.meeting.mq.RocketMqProperties;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Clock;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.util.UUID;
import org.springframework.stereotype.Component;

@Component
public class OutboxEventFactory {

  private final ObjectMapper objectMapper;
  private final RocketMqProperties rocketMqProperties;
  private final Clock clock;

  public OutboxEventFactory(
      ObjectMapper objectMapper, RocketMqProperties rocketMqProperties, Clock clock) {
    this.objectMapper = objectMapper;
    this.rocketMqProperties = rocketMqProperties;
    this.clock = clock;
  }

  public MessageOutboxRecord bookingEvent(
      String eventType, String aggregateId, String traceId, String runId, Object payload) {
    return create(
        eventType,
        "BOOKING_REQUEST",
        aggregateId,
        rocketMqProperties.bookingTopic(),
        eventType,
        traceId,
        runId,
        payload);
  }

  public MessageOutboxRecord domainEvent(
      String eventType, String aggregateId, String traceId, String runId, Object payload) {
    return create(
        eventType,
        "MEETING",
        aggregateId,
        rocketMqProperties.domainTopic(),
        eventType,
        traceId,
        runId,
        payload);
  }

  private MessageOutboxRecord create(
      String eventType,
      String aggregateType,
      String aggregateId,
      String topic,
      String tag,
      String traceId,
      String runId,
      Object payload) {
    String eventId = "evt_" + UUID.randomUUID().toString().replace("-", "");
    OffsetDateTime occurredAt = OffsetDateTime.now(clock);
    EventEnvelope envelope =
        new EventEnvelope(
            eventId,
            eventType,
            aggregateType,
            aggregateId,
            traceId,
            runId,
            occurredAt,
            1,
            objectMapper.valueToTree(payload));
    MessageOutboxRecord record = new MessageOutboxRecord();
    record.setEventId(eventId);
    record.setEventType(eventType);
    record.setAggregateType(aggregateType);
    record.setAggregateId(aggregateId);
    record.setTopic(topic);
    record.setTag(tag);
    record.setTraceId(traceId);
    record.setRunId(runId);
    record.setPayloadJson(write(envelope));
    record.setStatus("NEW");
    record.setRetryCount(0);
    record.setCreatedAt(LocalDateTime.now(clock));
    return record;
  }

  private String write(Object value) {
    try {
      return objectMapper.writeValueAsString(value);
    } catch (JsonProcessingException exception) {
      throw new IllegalStateException("Cannot serialize Outbox event", exception);
    }
  }
}
