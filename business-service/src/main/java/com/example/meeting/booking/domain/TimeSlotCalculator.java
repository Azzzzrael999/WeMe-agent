package com.example.meeting.booking.domain;

import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.web.ApiErrorDetail;
import java.time.Duration;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public class TimeSlotCalculator {

  static final int SLOT_MINUTES = 30;
  static final int MAX_DURATION_MINUTES = 240;

  private final ZoneId zoneId;

  public TimeSlotCalculator(@Value("${app.timezone}") String timezone) {
    this.zoneId = ZoneId.of(timezone);
  }

  public MeetingSchedule calculate(OffsetDateTime startAt, OffsetDateTime endAt) {
    if (startAt == null || endAt == null) {
      throw validation("startAt", "REQUIRED", "会议起止时间不能为空");
    }
    OffsetDateTime normalizedStart = normalizeOffset(startAt, "startAt");
    OffsetDateTime normalizedEnd = normalizeOffset(endAt, "endAt");
    validateBoundary(normalizedStart, "startAt");
    validateBoundary(normalizedEnd, "endAt");
    if (!normalizedEnd.isAfter(normalizedStart)) {
      throw validation("endAt", "INVALID_TIME_RANGE", "结束时间必须晚于开始时间");
    }
    if (!normalizedStart.toLocalDate().equals(normalizedEnd.toLocalDate())) {
      throw validation("endAt", "CROSS_DAY_NOT_ALLOWED", "会议必须在同一自然日内结束");
    }

    long durationMinutes = Duration.between(normalizedStart, normalizedEnd).toMinutes();
    if (durationMinutes < SLOT_MINUTES || durationMinutes > MAX_DURATION_MINUTES) {
      throw validation("endAt", "INVALID_DURATION", "会议时长必须在 30 分钟到 4 小时之间");
    }
    if (durationMinutes % SLOT_MINUTES != 0) {
      throw validation("endAt", "INVALID_SLOT_DURATION", "会议时长必须是 30 分钟的整数倍");
    }

    List<TimeSlot> slots = new ArrayList<>((int) (durationMinutes / SLOT_MINUTES));
    OffsetDateTime cursor = normalizedStart;
    while (cursor.isBefore(normalizedEnd)) {
      short slotIndex = (short) (cursor.getHour() * 2 + cursor.getMinute() / SLOT_MINUTES);
      LocalDateTime slotStart = cursor.toLocalDateTime();
      LocalDateTime slotEnd = cursor.plusMinutes(SLOT_MINUTES).toLocalDateTime();
      slots.add(new TimeSlot(cursor.toLocalDate(), slotIndex, slotStart, slotEnd));
      cursor = cursor.plusMinutes(SLOT_MINUTES);
    }
    return new MeetingSchedule(
        normalizedStart,
        normalizedEnd,
        normalizedStart.toLocalDateTime(),
        normalizedEnd.toLocalDateTime(),
        slots);
  }

  public OffsetDateTime normalizeQueryTime(OffsetDateTime value, String field) {
    return normalizeOffset(value, field);
  }

  public ZoneId zoneId() {
    return zoneId;
  }

  private OffsetDateTime normalizeOffset(OffsetDateTime value, String field) {
    ZoneOffset expectedOffset = zoneId.getRules().getOffset(value.toInstant());
    if (!value.getOffset().equals(expectedOffset)) {
      throw validation(field, "INVALID_TIMEZONE_OFFSET", "时间必须使用 Asia/Shanghai 偏移");
    }
    return value.atZoneSameInstant(zoneId).toOffsetDateTime();
  }

  private void validateBoundary(OffsetDateTime value, String field) {
    boolean validMinute = value.getMinute() == 0 || value.getMinute() == SLOT_MINUTES;
    if (!validMinute || value.getSecond() != 0 || value.getNano() != 0) {
      throw validation(field, "INVALID_SLOT_BOUNDARY", field + " 必须落在整点或半点");
    }
  }

  private BusinessException validation(String field, String reason, String message) {
    return new BusinessException(
        ErrorCode.VALIDATION_ERROR, message, List.of(new ApiErrorDetail(field, reason)));
  }
}
