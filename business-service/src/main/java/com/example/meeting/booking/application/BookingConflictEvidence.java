package com.example.meeting.booking.application;

import com.example.meeting.booking.domain.NormalizedMeetingCommand;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.web.ApiErrorDetail;
import java.util.List;
import java.util.stream.Collectors;

public final class BookingConflictEvidence {

  public static final String TYPE = "BOOKING_CONFLICT";

  private BookingConflictEvidence() {}

  public static BusinessException exception(NormalizedMeetingCommand command) {
    return new BusinessException(
        ErrorCode.BOOKING_CONFLICT, ErrorCode.BOOKING_CONFLICT.defaultMessage(), details(command));
  }

  private static List<ApiErrorDetail> details(NormalizedMeetingCommand command) {
    String slots =
        command.schedule().slots().stream()
            .map(slot -> Short.toString(slot.slotIndex()))
            .collect(Collectors.joining(","));
    return List.of(
        new ApiErrorDetail("conflict.type", TYPE),
        new ApiErrorDetail("conflict.roomId", Long.toString(command.roomId())),
        new ApiErrorDetail("conflict.slots", slots));
  }
}
