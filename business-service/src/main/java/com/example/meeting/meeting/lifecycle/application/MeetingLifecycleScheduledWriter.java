package com.example.meeting.meeting.lifecycle.application;

import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.meeting.infrastructure.MeetingParticipantMapper;
import com.example.meeting.meeting.lifecycle.api.MeetingLifecycleView.ChecklistItemView;
import com.example.meeting.meeting.lifecycle.api.MeetingLifecycleView.ChecklistView;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleMapper;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.ActionReminderRow;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.ScheduledMeetingRow;
import com.example.meeting.notification.NotificationMapper;
import com.example.meeting.notification.NotificationRecord;
import java.time.Clock;
import java.time.LocalDateTime;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

@Component
public class MeetingLifecycleScheduledWriter {

  private final MeetingLifecycleMapper lifecycleMapper;
  private final MeetingParticipantMapper participantMapper;
  private final NotificationMapper notificationMapper;
  private final MeetingLifecycleQueryService queryService;
  private final Clock clock;
  private final int batchSize;

  public MeetingLifecycleScheduledWriter(
      MeetingLifecycleMapper lifecycleMapper,
      MeetingParticipantMapper participantMapper,
      NotificationMapper notificationMapper,
      MeetingLifecycleQueryService queryService,
      Clock clock,
      @Value("${app.lifecycle.scan-batch-size:100}") int batchSize) {
    this.lifecycleMapper = lifecycleMapper;
    this.participantMapper = participantMapper;
    this.notificationMapper = notificationMapper;
    this.queryService = queryService;
    this.clock = clock;
    this.batchSize = Math.max(1, Math.min(batchSize, 500));
  }

  @Transactional
  public ScanResult scan() {
    LocalDateTime now = LocalDateTime.now(clock);
    int completed = completeMeetings(now);
    int meetingNotifications = deliverMeetingReminders(now);
    int actionNotifications = deliverActionReminders(now);
    return new ScanResult(completed, meetingNotifications, actionNotifications);
  }

  private int completeMeetings(LocalDateTime now) {
    int completed = 0;
    for (ScheduledMeetingRow meeting : lifecycleMapper.findMeetingsToComplete(now, batchSize)) {
      completed += lifecycleMapper.completeMeeting(meeting.id(), now);
    }
    return completed;
  }

  private int deliverMeetingReminders(LocalDateTime now) {
    int delivered = 0;
    LocalDateTime within24Hours = now.plusHours(24);
    for (ScheduledMeetingRow meeting :
        lifecycleMapper.findMeetingsForReminders(now, within24Hours, batchSize)) {
      Set<Long> recipients = new LinkedHashSet<>();
      recipients.add(meeting.organizerId());
      recipients.addAll(participantMapper.findEmployeeIdsByMeetingId(meeting.id()));
      for (Long recipient : recipients) {
        delivered +=
            deliverMeetingNotification(
                meeting,
                recipient,
                "MEETING_REMINDER_24H",
                "会议即将开始",
                "会议“%s”将在 24 小时内开始。".formatted(meeting.title()),
                now);
        if (!meeting.startAt().isAfter(now.plusMinutes(30))) {
          delivered +=
              deliverMeetingNotification(
                  meeting,
                  recipient,
                  "MEETING_REMINDER_30M",
                  "会议即将开始",
                  "会议“%s”将在 30 分钟内开始。".formatted(meeting.title()),
                  now);
        }
      }

      ChecklistView checklist =
          queryService
              .get(
                  meeting.id(),
                  new AuthenticatedUser(
                      meeting.organizerId(), "lifecycle-scheduler", List.of("EMPLOYEE")))
              .preparation()
              .checklist();
      if (!"READY".equals(checklist.status())) {
        String missing =
            checklist.items().stream()
                .filter(item -> !item.passed())
                .map(ChecklistItemView::message)
                .collect(java.util.stream.Collectors.joining("；"));
        delivered +=
            deliverMeetingNotification(
                meeting,
                meeting.organizerId(),
                "PREPARATION_MISSING",
                "会议准备仍有缺失",
                abbreviate("会议“%s”准备未完成：%s".formatted(meeting.title(), missing), 1000),
                now);
      }
    }
    return delivered;
  }

  private int deliverActionReminders(LocalDateTime now) {
    int delivered = 0;
    for (ActionReminderRow action :
        lifecycleMapper.findActionsForReminders(now, now.plusHours(24), batchSize)) {
      boolean overdue = !action.dueAt().isAfter(now);
      String type = overdue ? "ACTION_ITEM_OVERDUE" : "ACTION_ITEM_DUE_SOON";
      if (lifecycleMapper.insertActionDelivery(
              action.id(), action.dueAt(), action.assigneeEmployeeId(), type, now)
          == 1) {
        insertNotification(
            action.assigneeEmployeeId(),
            type,
            overdue ? "行动项已逾期" : "行动项即将到期",
            overdue
                ? "会议“%s”的行动项“%s”已逾期，请尽快处理。".formatted(action.meetingTitle(), action.title())
                : "会议“%s”的行动项“%s”将在 24 小时内到期。".formatted(action.meetingTitle(), action.title()),
            action.meetingId(),
            now);
        delivered++;
      }
    }
    return delivered;
  }

  private int deliverMeetingNotification(
      ScheduledMeetingRow meeting,
      long recipientId,
      String type,
      String title,
      String content,
      LocalDateTime now) {
    if (lifecycleMapper.insertMeetingDelivery(
            meeting.id(), meeting.startAt(), recipientId, type, now)
        != 1) {
      return 0;
    }
    insertNotification(recipientId, type, title, content, meeting.id(), now);
    return 1;
  }

  private void insertNotification(
      long userId, String type, String title, String content, long meetingId, LocalDateTime now) {
    NotificationRecord notification = new NotificationRecord();
    notification.setUserId(userId);
    notification.setType(type);
    notification.setTitle(title);
    notification.setContent(abbreviate(content, 1000));
    notification.setRelatedMeetingId(meetingId);
    notification.setCreatedAt(now);
    notificationMapper.insert(notification);
  }

  private String abbreviate(String value, int maximum) {
    return value.length() <= maximum ? value : value.substring(0, maximum);
  }

  public record ScanResult(
      int completedMeetings, int meetingNotifications, int actionNotifications) {}
}
