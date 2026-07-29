package com.example.meeting.room.api;

import com.example.meeting.common.web.ApiResponseFactory;
import com.example.meeting.common.web.ApiSuccess;
import com.example.meeting.room.application.RoomAdministrationService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/admin/rooms")
public class AdminRoomController {

  private final RoomAdministrationService roomAdministrationService;
  private final ApiResponseFactory responseFactory;

  public AdminRoomController(
      RoomAdministrationService roomAdministrationService, ApiResponseFactory responseFactory) {
    this.roomAdministrationService = roomAdministrationService;
    this.responseFactory = responseFactory;
  }

  @PostMapping
  public ApiSuccess<RoomItemView> create(
      @Valid @RequestBody CreateRoomRequest body, HttpServletRequest request) {
    return responseFactory.success(roomAdministrationService.create(body), request);
  }

  @PutMapping("/{roomId}")
  public ApiSuccess<RoomItemView> update(
      @PathVariable long roomId,
      @Valid @RequestBody UpdateRoomRequest body,
      HttpServletRequest request) {
    return responseFactory.success(roomAdministrationService.update(roomId, body), request);
  }

  @PatchMapping("/{roomId}/status")
  public ApiSuccess<RoomItemView> updateStatus(
      @PathVariable long roomId,
      @Valid @RequestBody UpdateRoomStatusRequest body,
      HttpServletRequest request) {
    return responseFactory.success(roomAdministrationService.updateStatus(roomId, body), request);
  }
}
