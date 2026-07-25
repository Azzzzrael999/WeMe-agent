package com.example.meeting.booking.api;

import com.example.meeting.booking.application.BookingRequestQueryService;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.web.ApiResponseFactory;
import com.example.meeting.common.web.ApiSuccess;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/booking-requests")
public class BookingRequestController {

  private final BookingRequestQueryService queryService;
  private final ApiResponseFactory responseFactory;

  public BookingRequestController(
      BookingRequestQueryService queryService, ApiResponseFactory responseFactory) {
    this.queryService = queryService;
    this.responseFactory = responseFactory;
  }

  @GetMapping("/{requestNo}")
  public ApiSuccess<BookingRequestView> get(
      @PathVariable String requestNo,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(queryService.get(requestNo, actor), request);
  }
}
