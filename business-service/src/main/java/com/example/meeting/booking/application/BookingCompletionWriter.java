package com.example.meeting.booking.application;

import com.example.meeting.meeting.infrastructure.MeetingMapper;
import com.example.meeting.meeting.infrastructure.MeetingParticipantMapper;
import com.example.meeting.mq.BookingResultPayload;
import com.example.meeting.mq.MeetingConfirmedPayload;
import com.example.meeting.notification.NotificationMapper;
import com.example.meeting.notification.NotificationRecord;
import com.example.meeting.outbox.MessageOutboxMapper;
import com.example.meeting.outbox.OutboxEventFactory;
import java.time.Clock;
import java.time.LocalDateTime;
import java.util.Collection;
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
  private final MeetingMapper meetingMapper;
  private final MessageOutboxMapper outboxMapper;
  private final OutboxEventFactory eventFactory;
  private final Clock clock;

  public BookingCompletionWriter(
      NotificationMapper notificationMapper,
      MeetingParticipantMapper participantMapper,
      MeetingMapper meetingMapper,
      MessageOutboxMapper outboxMapper,
      OutboxEventFactory eventFactory,
      Clock clock) {
    this.notificationMapper = notificationMapper;
    this.participantMapper = participantMapper;
    this.meetingMapper = meetingMapper;
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
        "MEETING_CONFIRMED", meetingId, requestNo, organizerId, traceId, runId, Set.of());
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
  public void writeChanged(
      long meetingId, long organizerId, Collection<Long> previousParticipantIds) {
    writeMeetingEvent(
        "MEETING_CHANGED", meetingId, null, organizerId, null, null, previousParticipantIds);
  }

  @Transactional(propagation = Propagation.MANDATORY)
  public void writeCancelled(long meetingId, long organizerId) {
    writeMeetingEvent("MEETING_CANCELLED", meetingId, null, organizerId, null, null, Set.of());
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
      long meetingId,
      String requestNo,
      long organizerId,
      String traceId,
      String runId,
      Collection<Long> previousParticipantIds) {
    LocalDateTime now = LocalDateTime.now(clock);
    Set<Long> recipients = new LinkedHashSet<>(previousParticipantIds);
    recipients.addAll(participantMapper.findEmployeeIdsByMeetingId(meetingId));
    recipients.add(organizerId);
    String meetingTitle = meetingMapper.selectById(meetingId).getTitle();
    String notificationTitle = notificationTitle(eventType);
    for (Long recipient : recipients) {
      NotificationRecord notification = new NotificationRecord();
      notification.setUserId(recipient);
      notification.setType(eventType);
      notification.setTitle(notificationTitle);
      notification.setContent(notificationContent(eventType, meetingTitle));
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

  private String notificationTitle(String eventType) {
    return switch (eventType) {
      case "MEETING_CONFIRMED" -> "会议已确认";
      case "MEETING_CHANGED" -> "会议已变更";
      case "MEETING_CANCELLED" -> "会议已取消";
      default -> throw new IllegalArgumentException("Unsupported meeting event: " + eventType);
    };
  }

  private String notificationContent(String eventType, String meetingTitle) {
    return switch (eventType) {
      case "MEETING_CONFIRMED" -> "会议“" + meetingTitle + "”已确认。";
      case "MEETING_CHANGED" -> "会议“" + meetingTitle + "”的时间或参会信息已更新。";
      case "MEETING_CANCELLED" -> "会议“" + meetingTitle + "”已取消。";
      default -> throw new IllegalArgumentException("Unsupported meeting event: " + eventType);
    };
  }

  private String resolveTraceId(String traceId) {
    if (traceId != null && !traceId.isBlank()) {
      return traceId;
    }
    String currentTraceId = MDC.get("traceId");
    return currentTraceId == null || currentTraceId.isBlank() ? "trc_system" : currentTraceId;
  }
}
