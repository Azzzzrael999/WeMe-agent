package com.example.meeting.room.api;

import com.example.meeting.common.web.ApiResponseFactory;
import com.example.meeting.common.web.ApiSuccess;
import com.example.meeting.room.application.RoomQueryService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/rooms")
public class RoomController {

  private final RoomQueryService roomQueryService;
  private final ApiResponseFactory responseFactory;

  public RoomController(RoomQueryService roomQueryService, ApiResponseFactory responseFactory) {
    this.roomQueryService = roomQueryService;
    this.responseFactory = responseFactory;
  }

  @GetMapping
  public ApiSuccess<RoomListView> list(HttpServletRequest request) {
    return responseFactory.success(roomQueryService.findActiveRooms(), request);
  }
}
