package com.example.meeting.mq;

import com.example.meeting.outbox.MessageOutboxRecord;

public interface MqMessagePublisher {
  void publish(MessageOutboxRecord message);
}
