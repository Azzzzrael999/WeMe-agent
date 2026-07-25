package com.example.meeting.meeting.api;

import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.web.ApiResponseFactory;
import com.example.meeting.common.web.ApiSuccess;
import com.example.meeting.meeting.application.MeetingApplicationService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/meetings")
public class MeetingController {

  private final MeetingApplicationService meetingService;
  private final ApiResponseFactory responseFactory;

  public MeetingController(
      MeetingApplicationService meetingService, ApiResponseFactory responseFactory) {
    this.meetingService = meetingService;
    this.responseFactory = responseFactory;
  }

  @PostMapping
  public ApiSuccess<MeetingView> create(
      @Valid @RequestBody CreateMeetingRequest body,
      @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(meetingService.create(body, idempotencyKey, actor), request);
  }

  @GetMapping
  public ApiSuccess<MeetingListView> list(
      @RequestParam(required = false) String from,
      @RequestParam(required = false) String to,
      @RequestParam(required = false) String status,
      @RequestParam(required = false) String page,
      @RequestParam(required = false) String size,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(
        meetingService.list(from, to, status, page, size, actor), request);
  }

  @GetMapping("/{meetingId}")
  public ApiSuccess<MeetingView> get(
      @PathVariable long meetingId,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(meetingService.get(meetingId, actor), request);
  }

  @PutMapping("/{meetingId}")
  public ApiSuccess<MeetingView> update(
      @PathVariable long meetingId,
      @Valid @RequestBody UpdateMeetingRequest body,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(meetingService.update(meetingId, body, actor), request);
  }

  @DeleteMapping("/{meetingId}")
  public ApiSuccess<MeetingView> cancel(
      @PathVariable long meetingId,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(meetingService.cancel(meetingId, actor), request);
  }
}
