package com.example.meeting.booking.application;

import com.example.meeting.booking.domain.MeetingSchedule;
import com.example.meeting.booking.domain.NormalizedMeetingCommand;
import com.example.meeting.booking.domain.TimeSlotCalculator;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.web.ApiErrorDetail;
import com.example.meeting.meeting.api.CreateMeetingRequest;
import com.example.meeting.meeting.api.UpdateMeetingRequest;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import org.springframework.stereotype.Component;

@Component
public class MeetingCommandFactory {

  private static final int MAX_PARTICIPANTS = 100;

  private final TimeSlotCalculator timeSlotCalculator;

  public MeetingCommandFactory(TimeSlotCalculator timeSlotCalculator) {
    this.timeSlotCalculator = timeSlotCalculator;
  }

  public NormalizedMeetingCommand create(CreateMeetingRequest request, long organizerId) {
    return normalize(
        request.title(),
        request.meetingType(),
        request.roomId(),
        request.startAt(),
        request.endAt(),
        request.requiredParticipantIds(),
        request.optionalParticipantIds(),
        organizerId);
  }

  public NormalizedMeetingCommand update(UpdateMeetingRequest request, long organizerId) {
    return normalize(
        request.title(),
        request.meetingType(),
        request.roomId(),
        request.startAt(),
        request.endAt(),
        request.requiredParticipantIds(),
        request.optionalParticipantIds(),
        organizerId);
  }

  private NormalizedMeetingCommand normalize(
      String title,
      String meetingType,
      Long roomId,
      java.time.OffsetDateTime startAt,
      java.time.OffsetDateTime endAt,
      List<Long> requiredIds,
      List<Long> optionalIds,
      long organizerId) {
    if (roomId == null || roomId <= 0) {
      throw validation("roomId", "MUST_BE_POSITIVE", "roomId 必须为正数");
    }
    MeetingSchedule schedule = timeSlotCalculator.calculate(startAt, endAt);
    TreeSet<Long> required = positiveIds(requiredIds, "requiredParticipantIds");
    TreeSet<Long> optional = positiveIds(optionalIds, "optionalParticipantIds");
    required.add(organizerId);
    optional.remove(organizerId);

    Set<Long> overlap = new TreeSet<>(required);
    overlap.retainAll(optional);
    if (!overlap.isEmpty()) {
      throw validation(
          "optionalParticipantIds", "PARTICIPANT_TYPE_OVERLAP", "同一员工不能同时是 REQUIRED 和 OPTIONAL");
    }
    if (required.size() + optional.size() > MAX_PARTICIPANTS) {
      throw validation("requiredParticipantIds", "TOO_MANY_PARTICIPANTS", "会议参与者最多 100 人");
    }

    return new NormalizedMeetingCommand(
        title.trim(),
        meetingType.trim(),
        roomId,
        schedule,
        new ArrayList<>(required),
        new ArrayList<>(optional));
  }

  private TreeSet<Long> positiveIds(Collection<Long> ids, String field) {
    if (ids == null) {
      throw validation(field, "REQUIRED", field + " 不能为空");
    }
    TreeSet<Long> normalized = new TreeSet<>();
    for (Long id : ids) {
      if (id == null || id <= 0) {
        throw validation(field, "MUST_BE_POSITIVE", field + " 只能包含正数 ID");
      }
      normalized.add(id);
    }
    return normalized;
  }

  private BusinessException validation(String field, String reason, String message) {
    return new BusinessException(
        ErrorCode.VALIDATION_ERROR, message, List.of(new ApiErrorDetail(field, reason)));
  }
}
