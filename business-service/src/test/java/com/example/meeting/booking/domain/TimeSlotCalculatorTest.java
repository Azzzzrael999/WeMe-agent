package com.example.meeting.booking.domain;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import java.time.OffsetDateTime;
import org.junit.jupiter.api.Test;

class TimeSlotCalculatorTest {

  private final TimeSlotCalculator calculator = new TimeSlotCalculator("Asia/Shanghai");

  @Test
  void ninetyMinutesProduceThreeHalfOpenSlots() {
    MeetingSchedule schedule =
        calculator.calculate(time("2026-08-19T15:00:00+08:00"), time("2026-08-19T16:30:00+08:00"));

    assertThat(schedule.slots()).hasSize(3);
    assertThat(schedule.slots())
        .extracting(TimeSlot::slotIndex)
        .containsExactly((short) 30, (short) 31, (short) 32);
    assertThat(schedule.slots().getFirst().startAt().toString()).isEqualTo("2026-08-19T15:00");
    assertThat(schedule.slots().getLast().endAt().toString()).isEqualTo("2026-08-19T16:30");
  }

  @Test
  void acceptsMinimumAndMaximumSameDayDurations() {
    MeetingSchedule minimum =
        calculator.calculate(time("2026-08-19T00:00:00+08:00"), time("2026-08-19T00:30:00+08:00"));
    MeetingSchedule maximum =
        calculator.calculate(time("2026-08-19T08:00:00+08:00"), time("2026-08-19T12:00:00+08:00"));

    assertThat(minimum.slots()).extracting(TimeSlot::slotIndex).containsExactly((short) 0);
    assertThat(maximum.slots()).hasSize(8);
  }

  @Test
  void rejectsNonBoundaryCrossDayAndInvalidDurations() {
    assertValidationReason(
        "INVALID_SLOT_BOUNDARY",
        () ->
            calculator.calculate(
                time("2026-08-19T15:15:00+08:00"), time("2026-08-19T16:00:00+08:00")));
    assertValidationReason(
        "CROSS_DAY_NOT_ALLOWED",
        () ->
            calculator.calculate(
                time("2026-08-19T23:30:00+08:00"), time("2026-08-20T00:00:00+08:00")));
    assertValidationReason(
        "INVALID_DURATION",
        () ->
            calculator.calculate(
                time("2026-08-19T08:00:00+08:00"), time("2026-08-19T12:30:00+08:00")));
    assertValidationReason(
        "INVALID_TIME_RANGE",
        () ->
            calculator.calculate(
                time("2026-08-19T15:00:00+08:00"), time("2026-08-19T15:00:00+08:00")));
  }

  @Test
  void rejectsOffsetOutsideAsiaShanghai() {
    assertValidationReason(
        "INVALID_TIMEZONE_OFFSET",
        () -> calculator.calculate(time("2026-08-19T07:00:00Z"), time("2026-08-19T08:00:00Z")));
  }

  private OffsetDateTime time(String value) {
    return OffsetDateTime.parse(value);
  }

  private void assertValidationReason(String reason, Runnable action) {
    assertThatThrownBy(action::run)
        .isInstanceOfSatisfying(
            BusinessException.class,
            exception -> {
              assertThat(exception.errorCode()).isEqualTo(ErrorCode.VALIDATION_ERROR);
              assertThat(exception.details())
                  .extracting(detail -> detail.reason())
                  .contains(reason);
            });
  }
}
