package com.example.meeting.common.error;

import com.example.meeting.common.web.ApiErrorDetail;
import java.util.List;

public class BusinessException extends RuntimeException {

  private final ErrorCode errorCode;
  private final List<ApiErrorDetail> details;

  public BusinessException(ErrorCode errorCode) {
    this(errorCode, errorCode.defaultMessage(), List.of());
  }

  public BusinessException(ErrorCode errorCode, String message) {
    this(errorCode, message, List.of());
  }

  public BusinessException(ErrorCode errorCode, String message, List<ApiErrorDetail> details) {
    super(message);
    this.errorCode = errorCode;
    this.details = List.copyOf(details);
  }

  public ErrorCode errorCode() {
    return errorCode;
  }

  public List<ApiErrorDetail> details() {
    return details;
  }
}
