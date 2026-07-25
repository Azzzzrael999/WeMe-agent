package com.example.meeting.common.web;

import java.util.List;

public record ApiError(String code, String message, List<ApiErrorDetail> details, String traceId) {

  public ApiError {
    details = List.copyOf(details);
  }
}
