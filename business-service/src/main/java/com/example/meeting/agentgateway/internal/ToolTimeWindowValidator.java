package com.example.meeting.agentgateway.internal;

import com.example.meeting.booking.domain.TimeSlotCalculator;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.web.ApiErrorDetail;
import java.time.Duration;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.util.List;
import org.springframework.stereotype.Component;

@Component
public class ToolTimeWindowValidator {
  private static final Duration MAX_WINDOW = Duration.ofDays(14);
  private final TimeSlotCalculator timeSlotCalculator;

  public ToolTimeWindowValidator(TimeSlotCalculator timeSlotCalculator) {
    this.timeSlotCalculator = timeSlotCalculator;
  }

  public Window validate(OffsetDateTime from, OffsetDateTime to) {
    OffsetDateTime normalizedFrom = timeSlotCalculator.normalizeQueryTime(from, "from");
    OffsetDateTime normalizedTo = timeSlotCalculator.normalizeQueryTime(to, "to");
    if (!normalizedTo.isAfter(normalizedFrom)) {
      throw validation("to", "INVALID_TIME_RANGE");
    }
    if (Duration.between(normalizedFrom, normalizedTo).compareTo(MAX_WINDOW) > 0) {
      throw validation("to", "QUERY_WINDOW_TOO_LARGE");
    }
    return new Window(normalizedFrom.toLocalDateTime(), normalizedTo.toLocalDateTime());
  }

  private BusinessException validation(String field, String reason) {
    return new BusinessException(
        ErrorCode.VALIDATION_ERROR, "Tool 时间窗口不符合要求", List.of(new ApiErrorDetail(field, reason)));
  }

  public record Window(LocalDateTime from, LocalDateTime to) {}
}
