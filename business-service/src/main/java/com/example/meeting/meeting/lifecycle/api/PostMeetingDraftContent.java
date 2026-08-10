package com.example.meeting.meeting.lifecycle.api;

import com.fasterxml.jackson.annotation.JsonFormat;
import java.time.OffsetDateTime;
import java.util.List;

public record PostMeetingDraftContent(
    MinutesContent minutes, List<DecisionContent> decisions, List<ActionItemContent> actionItems) {

  public PostMeetingDraftContent {
    decisions = decisions == null ? List.of() : List.copyOf(decisions);
    actionItems = actionItems == null ? List.of() : List.copyOf(actionItems);
  }

  public record MinutesContent(String background, String discussionSummary, String conclusion) {}

  public record DecisionContent(String content, String rationale) {}

  public record ActionItemContent(
      String title,
      String description,
      Long assigneeEmployeeId,
      @JsonFormat(without = JsonFormat.Feature.ADJUST_DATES_TO_CONTEXT_TIME_ZONE)
          OffsetDateTime dueAt) {}
}
