package com.example.meeting.mq;

import com.example.meeting.booking.application.BookingCompletionWriter;
import com.example.meeting.booking.application.BookingTransactionService;
import com.example.meeting.booking.domain.BookingRequestRecord;
import com.example.meeting.booking.domain.NormalizedMeetingCommand;
import com.example.meeting.booking.infrastructure.BookingRequestMapper;
import com.example.meeting.outbox.EventEnvelope;
import java.time.Clock;
import java.time.LocalDateTime;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class BookingCommandFinalizationService {

  private final BookingRequestMapper requestMapper;
  private final EventConsumeRecordMapper consumeMapper;
  private final BookingTransactionService bookingTransactionService;
  private final BookingCompletionWriter completionWriter;
  private final RocketMqProperties properties;
  private final Clock clock;

  public BookingCommandFinalizationService(
      BookingRequestMapper requestMapper,
      EventConsumeRecordMapper consumeMapper,
      BookingTransactionService bookingTransactionService,
      BookingCompletionWriter completionWriter,
      RocketMqProperties properties,
      Clock clock) {
    this.requestMapper = requestMapper;
    this.consumeMapper = consumeMapper;
    this.bookingTransactionService = bookingTransactionService;
    this.completionWriter = completionWriter;
    this.properties = properties;
    this.clock = clock;
  }

  @Transactional
  public void finalizeSuccess(
      EventEnvelope event, BookingCommandPayload payload, NormalizedMeetingCommand command) {
    if (alreadyConsumed(event.eventId())) {
      return;
    }
    BookingRequestRecord request =
        requestMapper.findByRequestNoForUpdate(payload.requestNo()).orElseThrow();
    if (!"PENDING".equals(request.getStatus())) {
      return;
    }
    LocalDateTime now = LocalDateTime.now(clock);
    if (requestMapper.markProcessing(request.getId(), now) != 1) {
      return;
    }
    long meetingId =
        bookingTransactionService.createAgentMeeting(
            command, payload.userId(), request.getRunId(), request.getRequestNo());
    if (requestMapper.markSuccess(request.getId(), meetingId, now) != 1) {
      throw new IllegalStateException("Booking request SUCCESS transition failed");
    }
    completionWriter.writeConfirmed(
        meetingId,
        request.getRequestNo(),
        request.getUserId(),
        request.getTraceId(),
        request.getRunId(),
        true);
    recordConsumed(event.eventId(), "SUCCESS", now);
  }

  @Transactional
  public void finalizeConflict(
      EventEnvelope event, BookingCommandPayload payload, java.util.List<Short> slots) {
    if (alreadyConsumed(event.eventId())) {
      return;
    }
    BookingRequestRecord request =
        requestMapper.findByRequestNoForUpdate(payload.requestNo()).orElseThrow();
    if ("SUCCESS".equals(request.getStatus()) || "CONFLICT".equals(request.getStatus())) {
      return;
    }
    LocalDateTime now = LocalDateTime.now(clock);
    if (requestMapper.markConflict(request.getId(), "会议室或必须参加者在该时段已被占用", now) != 1) {
      throw new IllegalStateException("Booking request CONFLICT transition failed");
    }
    completionWriter.writeConflict(
        request.getRequestNo(), request.getTraceId(), request.getRunId(), payload.roomId(), slots);
    recordConsumed(event.eventId(), "CONFLICT", now);
  }

  private boolean alreadyConsumed(String eventId) {
    return consumeMapper.countConsumed(properties.bookingConsumerGroup(), eventId) > 0;
  }

  private void recordConsumed(String eventId, String status, LocalDateTime now) {
    EventConsumeRecord consumed = new EventConsumeRecord();
    consumed.setConsumerGroup(properties.bookingConsumerGroup());
    consumed.setEventId(eventId);
    consumed.setStatus(status);
    consumed.setConsumedAt(now);
    consumeMapper.insert(consumed);
  }
}
