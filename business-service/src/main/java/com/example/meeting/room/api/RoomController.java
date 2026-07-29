package com.example.meeting.room.api;

import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.web.ApiResponseFactory;
import com.example.meeting.common.web.ApiSuccess;
import com.example.meeting.room.application.RoomAvailabilityService;
import com.example.meeting.room.application.RoomQueryService;
import jakarta.servlet.http.HttpServletRequest;
import java.time.OffsetDateTime;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/rooms")
public class RoomController {

  private final RoomQueryService roomQueryService;
  private final RoomAvailabilityService roomAvailabilityService;
  private final ApiResponseFactory responseFactory;

  public RoomController(
      RoomQueryService roomQueryService,
      RoomAvailabilityService roomAvailabilityService,
      ApiResponseFactory responseFactory) {
    this.roomQueryService = roomQueryService;
    this.roomAvailabilityService = roomAvailabilityService;
    this.responseFactory = responseFactory;
  }

  @GetMapping
  public ApiSuccess<RoomListView> list(
      @AuthenticationPrincipal AuthenticatedUser actor, HttpServletRequest request) {
    return responseFactory.success(roomQueryService.findVisibleRooms(actor), request);
  }

  @GetMapping("/{roomId}")
  public ApiSuccess<RoomItemView> get(
      @PathVariable long roomId,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(roomQueryService.findVisibleRoom(roomId, actor), request);
  }

  @GetMapping("/{roomId}/availability")
  public ApiSuccess<RoomAvailabilityView> availability(
      @PathVariable long roomId,
      @RequestParam(required = false) OffsetDateTime from,
      @RequestParam(required = false) OffsetDateTime to,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(
        roomAvailabilityService.findAvailability(roomId, from, to, actor), request);
  }
}
