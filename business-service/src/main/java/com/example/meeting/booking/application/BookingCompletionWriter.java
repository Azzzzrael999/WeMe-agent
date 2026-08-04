package com.example.meeting.booking.application;

import com.example.meeting.meeting.infrastructure.MeetingParticipantMapper;
import com.example.meeting.mq.BookingResultPayload;
import com.example.meeting.mq.MeetingConfirmedPayload;
import com.example.meeting.notification.NotificationMapper;
import com.example.meeting.notification.NotificationRecord;
import com.example.meeting.outbox.MessageOutboxMapper;
import com.example.meeting.outbox.OutboxEventFactory;
import java.time.Clock;
import java.time.LocalDateTime;
import java.util.LinkedHashSet;
import java.util.Set;
import org.slf4j.MDC;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Component
public class BookingCompletionWriter {

  private final NotificationMapper notificationMapper;
  private final MeetingParticipantMapper participantMapper;
  private final MessageOutboxMapper outboxMapper;
  private final OutboxEventFactory eventFactory;
  private final Clock clock;

  public BookingCompletionWriter(
      NotificationMapper notificationMapper,
      MeetingParticipantMapper participantMapper,
      MessageOutboxMapper outboxMapper,
      OutboxEventFactory eventFactory,
      Clock clock) {
    this.notificationMapper = notificationMapper;
    this.participantMapper = participantMapper;
    this.outboxMapper = outboxMapper;
    this.eventFactory = eventFactory;
    this.clock = clock;
  }

  @Transactional(propagation = Propagation.MANDATORY)
  public void writeConfirmed(
      long meetingId,
      String requestNo,
      long organizerId,
      String traceId,
      String runId,
      boolean includeBookingResult) {
    writeMeetingEvent(
        "MEETING_CONFIRMED",
        "Meeting confirmed",
        meetingId,
        requestNo,
        organizerId,
        traceId,
        runId);
    if (includeBookingResult) {
      outboxMapper.insert(
          eventFactory.bookingEvent(
              "BOOKING_RESULT",
              requestNo,
              traceId,
              runId,
              new BookingResultPayload(requestNo, "SUCCESS", meetingId, null)));
    }
  }

  @Transactional(propagation = Propagation.MANDATORY)
  public void writeChanged(long meetingId, long organizerId) {
    writeMeetingEvent(
        "MEETING_CHANGED", "Meeting changed", meetingId, null, organizerId, null, null);
  }

  @Transactional(propagation = Propagation.MANDATORY)
  public void writeCancelled(long meetingId, long organizerId) {
    writeMeetingEvent(
        "MEETING_CANCELLED", "Meeting cancelled", meetingId, null, organizerId, null, null);
  }

  @Transactional(propagation = Propagation.MANDATORY)
  public void writeConflict(
      String requestNo, String traceId, String runId, long roomId, java.util.List<Short> slots) {
    outboxMapper.insert(
        eventFactory.bookingEvent(
            "BOOKING_RESULT",
            requestNo,
            traceId,
            runId,
            new BookingResultPayload(
                requestNo,
                "CONFLICT",
                null,
                new BookingResultPayload.ConflictView(
                    BookingConflictEvidence.TYPE, roomId, slots))));
  }

  private void writeMeetingEvent(
      String eventType,
      String title,
      long meetingId,
      String requestNo,
      long organizerId,
      String traceId,
      String runId) {
    LocalDateTime now = LocalDateTime.now(clock);
    Set<Long> recipients =
        new LinkedHashSet<>(participantMapper.findEmployeeIdsByMeetingId(meetingId));
    recipients.add(organizerId);
    for (Long recipient : recipients) {
      NotificationRecord notification = new NotificationRecord();
      notification.setUserId(recipient);
      notification.setType(eventType);
      notification.setTitle(title);
      notification.setContent(title + ", meetingId=" + meetingId);
      notification.setRelatedMeetingId(meetingId);
      notification.setCreatedAt(now);
      notificationMapper.insert(notification);
    }
    outboxMapper.insert(
        eventFactory.domainEvent(
            eventType,
            Long.toString(meetingId),
            resolveTraceId(traceId),
            runId,
            new MeetingConfirmedPayload(meetingId, requestNo, organizerId)));
  }

  private String resolveTraceId(String traceId) {
    if (traceId != null && !traceId.isBlank()) {
      return traceId;
    }
    String currentTraceId = MDC.get("traceId");
    return currentTraceId == null || currentTraceId.isBlank() ? "trc_system" : currentTraceId;
  }
}
