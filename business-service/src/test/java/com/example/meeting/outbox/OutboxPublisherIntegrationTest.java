package com.example.meeting.outbox;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

import com.example.meeting.mq.MqMessagePublisher;
import com.example.meeting.mq.RocketMqProperties;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;

@SpringBootTest
@ActiveProfiles("test")
class OutboxPublisherIntegrationTest {

  private static final ZoneId ZONE_ID = ZoneId.of("Asia/Shanghai");
  private static final Clock CLOCK = Clock.fixed(Instant.parse("2026-08-11T04:00:00Z"), ZONE_ID);

  @Autowired private MessageOutboxMapper mapper;
  @Autowired private JdbcTemplate jdbcTemplate;

  @BeforeEach
  void cleanOutbox() {
    jdbcTemplate.update("DELETE FROM message_outbox");
  }

  @Test
  void recoversExpiredSendingLeaseAndMarksEventSent() {
    MessageOutboxRecord record = insert("SENDING", 0, LocalDateTime.now(CLOCK).minusSeconds(1));
    MqMessagePublisher publisher = mock(MqMessagePublisher.class);

    outboxPublisher(publisher, 3).publishReady();

    verify(publisher).publish(any(MessageOutboxRecord.class));
    assertThat(status(record.getId())).isEqualTo("SENT");
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT next_retry_at FROM message_outbox WHERE id=?",
                LocalDateTime.class,
                record.getId()))
        .isNull();
  }

  @Test
  void publishFailureRetriesThenMovesToDeadAtConfiguredLimit() {
    MessageOutboxRecord record = insert("NEW", 0, null);
    MqMessagePublisher publisher = mock(MqMessagePublisher.class);
    doThrow(new IllegalStateException("broker unavailable"))
        .when(publisher)
        .publish(any(MessageOutboxRecord.class));
    OutboxPublisher outboxPublisher = outboxPublisher(publisher, 2);

    outboxPublisher.publishReady();
    assertThat(status(record.getId())).isEqualTo("RETRY");
    assertThat(retryCount(record.getId())).isEqualTo(1);

    jdbcTemplate.update(
        "UPDATE message_outbox SET next_retry_at=? WHERE id=?",
        LocalDateTime.now(CLOCK).minusSeconds(1),
        record.getId());
    outboxPublisher.publishReady();

    verify(publisher, times(2)).publish(any(MessageOutboxRecord.class));
    assertThat(status(record.getId())).isEqualTo("DEAD");
    assertThat(retryCount(record.getId())).isEqualTo(2);
  }

  private OutboxPublisher outboxPublisher(MqMessagePublisher publisher, int maxRetries) {
    RocketMqProperties rocketProperties =
        new RocketMqProperties(
            true,
            "rocketmq-nameserver:9876",
            "meeting-booking",
            "meeting-domain",
            "meeting-booking-finalizer",
            "meeting-agent-result-callback");
    return new OutboxPublisher(
        mapper, publisher, new OutboxProperties(500, maxRetries), rocketProperties, CLOCK);
  }

  private MessageOutboxRecord insert(String status, int retryCount, LocalDateTime nextRetryAt) {
    MessageOutboxRecord record = new MessageOutboxRecord();
    record.setEventId("evt_" + status.toLowerCase() + '_' + retryCount);
    record.setEventType("MEETING_CONFIRMED");
    record.setAggregateType("MEETING");
    record.setAggregateId("9001");
    record.setTopic("meeting-domain");
    record.setTag("MEETING_CONFIRMED");
    record.setTraceId("trace_outbox_test");
    record.setPayloadJson("{}");
    record.setStatus(status);
    record.setRetryCount(retryCount);
    record.setNextRetryAt(nextRetryAt);
    record.setCreatedAt(LocalDateTime.now(CLOCK).minusMinutes(1));
    mapper.insert(record);
    return record;
  }

  private String status(long id) {
    return jdbcTemplate.queryForObject(
        "SELECT status FROM message_outbox WHERE id=?", String.class, id);
  }

  private int retryCount(long id) {
    return jdbcTemplate.queryForObject(
        "SELECT retry_count FROM message_outbox WHERE id=?", Integer.class, id);
  }
}
