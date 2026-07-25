package com.example.meeting.mq;

import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import java.time.LocalDateTime;

@TableName("event_consume_record")
public class EventConsumeRecord {
  @TableId private Long id;
  private String consumerGroup;
  private String eventId;
  private String status;
  private LocalDateTime consumedAt;

  public Long getId() {
    return id;
  }

  public void setId(Long id) {
    this.id = id;
  }

  public String getConsumerGroup() {
    return consumerGroup;
  }

  public void setConsumerGroup(String consumerGroup) {
    this.consumerGroup = consumerGroup;
  }

  public String getEventId() {
    return eventId;
  }

  public void setEventId(String eventId) {
    this.eventId = eventId;
  }

  public String getStatus() {
    return status;
  }

  public void setStatus(String status) {
    this.status = status;
  }

  public LocalDateTime getConsumedAt() {
    return consumedAt;
  }

  public void setConsumedAt(LocalDateTime consumedAt) {
    this.consumedAt = consumedAt;
  }
}
