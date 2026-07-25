package com.example.meeting.booking.application;

import com.example.meeting.booking.api.BookingRequestView;
import com.example.meeting.booking.domain.BookingRequestRecord;
import com.example.meeting.booking.infrastructure.BookingRequestMapper;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AuthenticatedUser;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class BookingRequestQueryService {

  private final BookingRequestMapper mapper;
  private final ZoneId zoneId;

  public BookingRequestQueryService(
      BookingRequestMapper mapper, @Value("${app.timezone}") String timezone) {
    this.mapper = mapper;
    this.zoneId = ZoneId.of(timezone);
  }

  @Transactional(readOnly = true)
  public BookingRequestView get(String requestNo, AuthenticatedUser actor) {
    BookingRequestRecord record =
        mapper
            .findByRequestNo(requestNo)
            .orElseThrow(() -> new BusinessException(ErrorCode.BOOKING_REQUEST_NOT_FOUND));
    if (!actor.roles().contains("ADMIN") && !record.getUserId().equals(actor.userId())) {
      throw new BusinessException(ErrorCode.BOOKING_REQUEST_NOT_FOUND);
    }
    return new BookingRequestView(
        record.getRequestNo(),
        record.getStatus(),
        record.getMeetingId(),
        record.getErrorCode(),
        record.getErrorMessage(),
        offset(record.getCreatedAt()),
        offset(record.getUpdatedAt()));
  }

  private OffsetDateTime offset(LocalDateTime value) {
    return value.atZone(zoneId).toOffsetDateTime();
  }
}
