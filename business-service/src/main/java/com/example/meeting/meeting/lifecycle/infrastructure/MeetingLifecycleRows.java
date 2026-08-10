package com.example.meeting.meeting.lifecycle.infrastructure;

import java.time.LocalDateTime;

public final class MeetingLifecycleRows {

  private MeetingLifecycleRows() {}

  public record AgendaRow(
      Long id,
      Long meetingId,
      Integer sequenceNo,
      String topic,
      Long ownerEmployeeId,
      String ownerName,
      Integer plannedMinutes) {}

  public record MaterialRow(
      Long id,
      Long meetingId,
      Integer sequenceNo,
      String title,
      Long ownerEmployeeId,
      String ownerName,
      Boolean required,
      String status,
      String versionLabel,
      String note) {}

  public record DraftRow(
      Long id,
      Long meetingId,
      String requestId,
      String agentRunId,
      String transcript,
      String payloadJson,
      String status,
      Integer version,
      String errorCode,
      Long submittedBy,
      Long reviewedBy,
      LocalDateTime createdAt,
      LocalDateTime updatedAt,
      LocalDateTime reviewedAt) {}

  public record MinutesRow(
      Long id,
      Long meetingId,
      String background,
      String discussionSummary,
      String conclusion,
      Long confirmedBy,
      LocalDateTime confirmedAt) {}

  public record DecisionRow(
      Long id, Long meetingId, Integer sequenceNo, String content, String rationale) {}

  public record ActionItemRow(
      Long id,
      Long meetingId,
      Integer sequenceNo,
      String title,
      String description,
      Long assigneeEmployeeId,
      String assigneeName,
      LocalDateTime dueAt,
      String status,
      Integer version,
      LocalDateTime completedAt,
      LocalDateTime createdAt,
      LocalDateTime updatedAt) {}

  public record ScheduledMeetingRow(
      Long id, String title, Long organizerId, LocalDateTime startAt, LocalDateTime endAt) {}

  public record ActionReminderRow(
      Long id,
      Long meetingId,
      String meetingTitle,
      String title,
      Long assigneeEmployeeId,
      LocalDateTime dueAt,
      String status) {}
}
