package com.example.meeting.common.web;

import com.example.meeting.common.trace.TraceIds;
import jakarta.servlet.http.HttpServletRequest;
import java.time.Clock;
import java.time.OffsetDateTime;
import org.springframework.stereotype.Component;

@Component
public class ApiResponseFactory {

  private final Clock clock;

  public ApiResponseFactory(Clock clock) {
    this.clock = clock;
  }

  public <T> ApiSuccess<T> success(T data, HttpServletRequest request) {
    return new ApiSuccess<>(data, TraceIds.from(request), OffsetDateTime.now(clock));
  }
}
