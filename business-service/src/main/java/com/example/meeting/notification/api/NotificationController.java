package com.example.meeting.notification.api;

import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.web.ApiResponseFactory;
import com.example.meeting.common.web.ApiSuccess;
import com.example.meeting.notification.application.NotificationService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/notifications")
public class NotificationController {

  private final NotificationService service;
  private final ApiResponseFactory responseFactory;

  public NotificationController(NotificationService service, ApiResponseFactory responseFactory) {
    this.service = service;
    this.responseFactory = responseFactory;
  }

  @GetMapping
  public ApiSuccess<NotificationListView> list(
      @RequestParam(defaultValue = "false") boolean unreadOnly,
      @RequestParam(required = false) String type,
      @RequestParam(defaultValue = "1") int page,
      @RequestParam(defaultValue = "20") int size,
      @AuthenticationPrincipal AuthenticatedUser user,
      HttpServletRequest request) {
    return responseFactory.success(
        service.list(user.userId(), unreadOnly, type, page, size), request);
  }

  @GetMapping("/unread-count")
  public ApiSuccess<UnreadCountView> unreadCount(
      @AuthenticationPrincipal AuthenticatedUser user, HttpServletRequest request) {
    return responseFactory.success(
        new UnreadCountView(service.unreadCount(user.userId())), request);
  }

  @PatchMapping("/{notificationId}/read")
  public ApiSuccess<NotificationItemView> markRead(
      @PathVariable long notificationId,
      @AuthenticationPrincipal AuthenticatedUser user,
      HttpServletRequest request) {
    return responseFactory.success(service.markRead(notificationId, user.userId()), request);
  }

  @PatchMapping("/read-all")
  public ApiSuccess<ReadAllResultView> markAllRead(
      @AuthenticationPrincipal AuthenticatedUser user, HttpServletRequest request) {
    return responseFactory.success(service.markAllRead(user.userId()), request);
  }
}
