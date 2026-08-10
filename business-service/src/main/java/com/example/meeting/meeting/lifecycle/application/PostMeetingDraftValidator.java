package com.example.meeting.meeting.lifecycle.application;

import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.web.ApiErrorDetail;
import com.example.meeting.meeting.api.MeetingView;
import com.example.meeting.meeting.lifecycle.api.PostMeetingDraftContent;
import com.example.meeting.meeting.lifecycle.api.PostMeetingDraftContent.ActionItemContent;
import com.example.meeting.meeting.lifecycle.api.PostMeetingDraftContent.DecisionContent;
import com.example.meeting.meeting.lifecycle.api.PostMeetingDraftContent.MinutesContent;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public class PostMeetingDraftValidator {

  private static final int MAX_DECISIONS = 20;
  private static final int MAX_ACTION_ITEMS = 50;

  private final ZoneId zoneId;

  public PostMeetingDraftValidator(@Value("${app.timezone}") String timezone) {
    this.zoneId = ZoneId.of(timezone);
  }

  public PostMeetingDraftContent validateAndNormalize(
      PostMeetingDraftContent content, MeetingView meeting) {
    if (content == null || content.minutes() == null) {
      throw validation("editedDraft.minutes", "REQUIRED", "草案必须包含会议纪要");
    }
    if (content.decisions().size() > MAX_DECISIONS) {
      throw validation("editedDraft.decisions", "TOO_MANY_ITEMS", "决策最多 20 条");
    }
    if (content.actionItems().size() > MAX_ACTION_ITEMS) {
      throw validation("editedDraft.actionItems", "TOO_MANY_ITEMS", "行动项最多 50 条");
    }

    Set<Long> allowedEmployees = new LinkedHashSet<>();
    allowedEmployees.add(meeting.organizerId());
    meeting.participants().forEach(participant -> allowedEmployees.add(participant.employeeId()));

    MinutesContent minutes =
        new MinutesContent(
            requiredText(content.minutes().background(), 2000, "editedDraft.minutes.background"),
            requiredText(
                content.minutes().discussionSummary(),
                10000,
                "editedDraft.minutes.discussionSummary"),
            requiredText(content.minutes().conclusion(), 2000, "editedDraft.minutes.conclusion"));

    List<DecisionContent> decisions =
        java.util.stream.IntStream.range(0, content.decisions().size())
            .mapToObj(
                index -> {
                  DecisionContent decision = content.decisions().get(index);
                  if (decision == null) {
                    throw validation("editedDraft.decisions[" + index + "]", "REQUIRED", "决策不能为空");
                  }
                  return new DecisionContent(
                      requiredText(
                          decision.content(), 1000, "editedDraft.decisions[" + index + "].content"),
                      optionalText(
                          decision.rationale(),
                          1000,
                          "editedDraft.decisions[" + index + "].rationale"));
                })
            .toList();

    List<ActionItemContent> actionItems =
        java.util.stream.IntStream.range(0, content.actionItems().size())
            .mapToObj(
                index -> {
                  ActionItemContent action = content.actionItems().get(index);
                  String prefix = "editedDraft.actionItems[" + index + "]";
                  if (action == null) {
                    throw validation(prefix, "REQUIRED", "行动项不能为空");
                  }
                  if (action.assigneeEmployeeId() == null
                      || !allowedEmployees.contains(action.assigneeEmployeeId())) {
                    throw validation(
                        prefix + ".assigneeEmployeeId",
                        "ASSIGNEE_NOT_IN_MEETING",
                        "行动项负责人必须是会议参与者或组织者");
                  }
                  if (action.dueAt() == null) {
                    throw validation(prefix + ".dueAt", "INVALID_DUE_AT", "行动项截止时间必须晚于会议结束时间");
                  }
                  if (!zoneId
                      .getRules()
                      .getValidOffsets(action.dueAt().toLocalDateTime())
                      .contains(action.dueAt().getOffset())) {
                    throw validation(
                        prefix + ".dueAt",
                        "INVALID_TIME_OFFSET",
                        "行动项截止时间必须使用 Asia/Shanghai 的 +08:00 偏移");
                  }
                  if (!action.dueAt().isAfter(meeting.endAt())) {
                    throw validation(prefix + ".dueAt", "INVALID_DUE_AT", "行动项截止时间必须晚于会议结束时间");
                  }
                  OffsetDateTime dueAt =
                      action.dueAt().toInstant().atZone(zoneId).toOffsetDateTime();
                  return new ActionItemContent(
                      requiredText(action.title(), 200, prefix + ".title"),
                      optionalText(action.description(), 1000, prefix + ".description"),
                      action.assigneeEmployeeId(),
                      dueAt);
                })
            .toList();
    return new PostMeetingDraftContent(minutes, decisions, actionItems);
  }

  private String requiredText(String value, int maximum, String field) {
    if (value == null || value.isBlank()) {
      throw validation(field, "REQUIRED", field + " 不能为空");
    }
    String normalized = value.trim();
    if (normalized.length() > maximum) {
      throw validation(field, "TOO_LONG", field + " 超出长度上限");
    }
    return normalized;
  }

  private String optionalText(String value, int maximum, String field) {
    if (value == null || value.isBlank()) {
      return null;
    }
    String normalized = value.trim();
    if (normalized.length() > maximum) {
      throw validation(field, "TOO_LONG", field + " 超出长度上限");
    }
    return normalized;
  }

  private BusinessException validation(String field, String reason, String message) {
    return new BusinessException(
        ErrorCode.VALIDATION_ERROR, message, List.of(new ApiErrorDetail(field, reason)));
  }
}
