package com.example.meeting.common.error;

import com.example.meeting.common.trace.TraceIds;
import com.example.meeting.common.web.ApiError;
import com.example.meeting.common.web.ApiErrorDetail;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.ConstraintViolationException;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

@RestControllerAdvice
public class GlobalExceptionHandler {

  private static final Logger LOGGER = LoggerFactory.getLogger(GlobalExceptionHandler.class);

  @ExceptionHandler(BusinessException.class)
  ResponseEntity<ApiError> handleBusinessException(
      BusinessException exception, HttpServletRequest request) {
    ErrorCode errorCode = exception.errorCode();
    return ResponseEntity.status(errorCode.status())
        .body(error(errorCode, exception.getMessage(), exception.details(), request));
  }

  @ExceptionHandler(MethodArgumentNotValidException.class)
  ResponseEntity<ApiError> handleValidation(
      MethodArgumentNotValidException exception, HttpServletRequest request) {
    List<ApiErrorDetail> details =
        exception.getBindingResult().getFieldErrors().stream().map(this::toDetail).toList();
    return ResponseEntity.badRequest()
        .body(
            error(
                ErrorCode.VALIDATION_ERROR,
                ErrorCode.VALIDATION_ERROR.defaultMessage(),
                details,
                request));
  }

  @ExceptionHandler(ConstraintViolationException.class)
  ResponseEntity<ApiError> handleConstraintViolation(
      ConstraintViolationException exception, HttpServletRequest request) {
    List<ApiErrorDetail> details =
        exception.getConstraintViolations().stream()
            .map(
                violation ->
                    new ApiErrorDetail(
                        violation.getPropertyPath().toString(), violation.getMessage()))
            .toList();
    return ResponseEntity.badRequest()
        .body(
            error(
                ErrorCode.VALIDATION_ERROR,
                ErrorCode.VALIDATION_ERROR.defaultMessage(),
                details,
                request));
  }

  @ExceptionHandler(HttpMessageNotReadableException.class)
  ResponseEntity<ApiError> handleUnreadableBody(
      HttpMessageNotReadableException exception, HttpServletRequest request) {
    return ResponseEntity.badRequest()
        .body(
            error(
                ErrorCode.VALIDATION_ERROR,
                ErrorCode.VALIDATION_ERROR.defaultMessage(),
                List.of(new ApiErrorDetail("request", "MALFORMED_JSON")),
                request));
  }

  @ExceptionHandler(MethodArgumentTypeMismatchException.class)
  ResponseEntity<ApiError> handleTypeMismatch(
      MethodArgumentTypeMismatchException exception, HttpServletRequest request) {
    String field = exception.getName();
    return ResponseEntity.badRequest()
        .body(
            error(
                ErrorCode.VALIDATION_ERROR,
                ErrorCode.VALIDATION_ERROR.defaultMessage(),
                List.of(new ApiErrorDetail(field, "INVALID_TYPE")),
                request));
  }

  @ExceptionHandler(Exception.class)
  ResponseEntity<ApiError> handleUnexpected(Exception exception, HttpServletRequest request) {
    LOGGER.error("Unhandled request failure", exception);
    return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
        .body(
            error(
                ErrorCode.INTERNAL_ERROR,
                ErrorCode.INTERNAL_ERROR.defaultMessage(),
                List.of(),
                request));
  }

  private ApiErrorDetail toDetail(FieldError fieldError) {
    String reason = fieldError.getDefaultMessage();
    return new ApiErrorDetail(fieldError.getField(), reason == null ? "INVALID" : reason);
  }

  private ApiError error(
      ErrorCode errorCode,
      String message,
      List<ApiErrorDetail> details,
      HttpServletRequest request) {
    return new ApiError(errorCode.name(), message, details, TraceIds.from(request));
  }
}
