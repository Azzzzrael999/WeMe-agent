package com.example.meeting.meeting.lifecycle.api;

import com.example.meeting.meeting.api.MeetingView;
import java.time.OffsetDateTime;
import java.util.List;

public record MeetingLifecycleView(
    MeetingView meeting,
    PermissionsView permissions,
    PreparationView preparation,
    PostMeetingView postMeeting) {

  public record PermissionsView(
      boolean canEditPreparation, boolean canSubmitRecord, boolean canReviewDraft) {}

  public record PreparationView(
      int version,
      List<AgendaItemView> agendaItems,
      List<MaterialView> materials,
      ChecklistView checklist) {
    public PreparationView {
      agendaItems = List.copyOf(agendaItems);
      materials = List.copyOf(materials);
    }
  }

  public record AgendaItemView(
      long id,
      int sequenceNo,
      String topic,
      long ownerEmployeeId,
      String ownerName,
      int plannedMinutes) {}

  public record MaterialView(
      long id,
      int sequenceNo,
      String title,
      long ownerEmployeeId,
      String ownerName,
      boolean required,
      String status,
      String versionLabel,
      String note) {}

  public record ChecklistView(
      String status, OffsetDateTime generatedAt, List<ChecklistItemView> items) {
    public ChecklistView {
      items = List.copyOf(items);
    }
  }

  public record ChecklistItemView(String code, boolean passed, String message) {}

  public record PostMeetingView(
      DraftView draft,
      MinutesView minutes,
      List<DecisionView> decisions,
      List<ActionItemView> actionItems) {
    public PostMeetingView {
      decisions = List.copyOf(decisions);
      actionItems = List.copyOf(actionItems);
    }
  }

  public record DraftView(
      long id,
      String status,
      int version,
      String agentRunId,
      String errorCode,
      PostMeetingDraftContent content) {}

  public record MinutesView(
      String background,
      String discussionSummary,
      String conclusion,
      long confirmedBy,
      OffsetDateTime confirmedAt) {}

  public record DecisionView(long id, int sequenceNo, String content, String rationale) {}

  public record ActionItemView(
      long id,
      int sequenceNo,
      String title,
      String description,
      long assigneeEmployeeId,
      String assigneeName,
      OffsetDateTime dueAt,
      String status,
      int version,
      OffsetDateTime completedAt) {}
}
